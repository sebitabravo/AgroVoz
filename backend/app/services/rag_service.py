"""Servicio RAG — TF-IDF sobre corpus de documentos oficiales ODEPA.

Implementa RAGCorpus: carga documentos factuales (boletines ODEPA),
los indexa con TF-IDF (scikit-learn) y permite busqueda por similitud
coseno. Sin sentence-transformers, sin torch, sin DB externa.

Diseno deliberadamente simple:
- Corpus: archivos YAML estaticos en backend/corpus/
- Indexacion: TfidfVectorizer con ngram_range=(1,2) y stop_words="spanish"
- Retrieval: cosine similarity sobre la matriz TF-IDF (brute force)
- Sin persistencia: se reconstruye al iniciar el servicio
- Lazy loading: el corpus se carga en la primera busqueda

Para un corpus de ~30 chunks, TF-IDF retrieval es <2ms en CPU y supera
en precision a embeddings para terminos exactos del dominio agricola
(precios, papa, Traiguen, ODEPA).

Uso:
    from app.services.rag_service import rag_corpus
    resultados = rag_corpus.search("precio de la papa en ferias")
    # => [{"text": "...", "source": "...", "date": "...", "score": 0.85}, ...]
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

# Lista de stop words en español para TfidfVectorizer.
# scikit-learn solo incluye 'english' como built-in; para español
# usamos una lista manual. Suficiente para el dominio agricola.
_SPANISH_STOP_WORDS = [
    "a", "al", "algo", "algun", "alguna", "algunas", "algunos",
    "ante", "antes", "aquel", "aquella", "aqui", "asi",
    "bajo", "bastante", "bien",
    "cada", "casi", "cierto", "como", "con", "contra", "cual",
    "cuando", "cuanto", "de", "del", "demas", "dentro", "desde",
    "donde", "dos", "durante",
    "e", "ejemplo", "el", "ella", "ellas", "ellos", "en", "entre",
    "era", "eran", "es", "esa", "esas", "ese", "eso", "esos",
    "esta", "estaba", "estan", "estar", "este", "esto",
    "excepto",
    "fin",
    "fue", "fueron",
    "gran", "grandes",
    "ha", "haber", "habia", "habian", "hace", "hacia", "hasta",
    "hay", "hecho",
    "la", "las", "le", "les", "lo", "los",
    "mas", "mayor", "me", "menor", "menos", "mi", "mis", "mucho",
    "muy",
    "ni", "ningun", "ninguna", "no", "nos", "nuestra", "nuestro",
    "o", "os", "otra", "otro", "otros",
    "para", "pero", "poca", "poco", "por", "porque", "primer",
    "puede", "pues",
    "que",
    "sabe", "se", "sea", "segun", "ser", "si", "sido", "sin",
    "sino", "sobre", "solo", "son", "su", "sus",
    "tal", "tan", "tanta", "tanto", "tener", "tenia", "tiene",
    "todo", "todos", "tras", "tu",
    "un", "una", "unas", "unos", "usa", "usan",
    "va", "vamos", "van", "varios", "vez", "y", "ya",
]

# Directorio donde se almacenan los archivos YAML del corpus.
# Relativo a backend/corpus/ (fuera de data/ que esta en .gitignore).
_DEFAULT_CORPUS_DIR = Path(__file__).parent.parent.parent / "corpus"

# Maximo de chunks a retornar por busqueda.
_TOP_K = 3

# Umbral de similitud minima: resultados con score menor se descartan.
# TF-IDF con textos cortos rara vez da >0.1 para queries sin relacion.
_MIN_SCORE = 0.05


def _load_corpus_from_yaml(corpus_dir: Path) -> list[dict[str, str]]:
    """Carga todos los documentos YAML del directorio corpus.

    Cada archivo YAML debe tener el formato:
      documentos:
        - titulo: "..."
          fuente: "..."
          fecha: "..."
          chunks:
            - texto: "..."
            - texto: "..."

    Returns:
        Lista de chunks, cada uno con keys: text, source, date, title.
        Vacia si no hay archivos o el formato es invalido.
    """
    chunks: list[dict[str, str]] = []
    if not corpus_dir.is_dir():
        logger.warning("Directorio de corpus no encontrado: %s", corpus_dir)
        return chunks

    yaml_files = sorted(corpus_dir.glob("*.yaml"))
    if not yaml_files:
        logger.warning("No se encontraron archivos YAML en %s", corpus_dir)
        return chunks

    for path in yaml_files:
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
            logger.warning("Error leyendo corpus %s: %s", path.name, exc)
            continue

        if not isinstance(data, dict):
            continue
        docs = data.get("documentos", [])
        if not isinstance(docs, list):
            continue

        for doc in docs:
            if not isinstance(doc, dict):
                continue
            fuente = str(doc.get("fuente", ""))
            fecha = str(doc.get("fecha", ""))
            titulo = str(doc.get("titulo", ""))
            doc_chunks = doc.get("chunks", [])
            if not isinstance(doc_chunks, list):
                continue
            for chunk in doc_chunks:
                if not isinstance(chunk, dict):
                    continue
                texto = chunk.get("texto", "")
                if not texto or not isinstance(texto, str) or not texto.strip():
                    continue
                # Usar fuente/fecha del documento como default
                chunks.append({
                    "text": texto.strip(),
                    "source": fuente,
                    "date": fecha,
                    "title": titulo,
                })

    logger.info(
        "Corpus cargado: %d chunks desde %d archivos en %s",
        len(chunks),
        len(yaml_files),
        corpus_dir,
    )
    return chunks


class RAGCorpus:
    """Indexa y busca en el corpus de documentos ODEPA usando TF-IDF.

    Lazy loading: el corpus se carga y vectoriza en la primera busqueda.
    Thread-safe para lectura (la matriz TF-IDF es inmutable tras crearse).
    """

    def __init__(self, corpus_dir: str | Path | None = None) -> None:
        self._corpus_dir = Path(corpus_dir) if corpus_dir else _DEFAULT_CORPUS_DIR
        self._chunks: list[dict[str, str]] = []
        self._vectorizer: TfidfVectorizer | None = None
        self._tfidf_matrix: Any = None  # scipy sparse matrix
        self._loaded = False

    def load(self) -> None:
        """Carga y vectoriza el corpus. Idempotente: solo carga una vez."""
        if self._loaded:
            return

        self._chunks = _load_corpus_from_yaml(self._corpus_dir)
        if not self._chunks:
            logger.warning("Corpus vacio — RAG no disponible")
            self._loaded = True
            return

        texts = [c["text"] for c in self._chunks]

        # TfidfVectorizer con configuracion para espanol agricola.
        # ngram_range=(1,2): captura bigramas como "precio papa", "ferias libres"
        # max_features=5000: suficiente para corpus pequeno, evita ruido
        # stop_words lista manual: scikit-learn solo trae 'english' built-in.
        self._vectorizer = TfidfVectorizer(
            max_features=5000,
            ngram_range=(1, 2),
            stop_words=_SPANISH_STOP_WORDS,
            lowercase=True,
            strip_accents="unicode",
        )

        try:
            self._tfidf_matrix = self._vectorizer.fit_transform(texts)
            logger.info(
                "TF-IDF indexado: %d chunks, %d terminos",
                len(texts),
                self._tfidf_matrix.shape[1],
            )
        except (ValueError, TypeError) as exc:
            logger.error("Error indexando corpus TF-IDF: %s", exc)
            self._chunks = []
            self._vectorizer = None

        self._loaded = True

    def is_available(self) -> bool:
        """Retorna True si el corpus esta cargado y listo para busquedas."""
        return self._loaded and len(self._chunks) > 0 and self._vectorizer is not None

    def search(self, query: str, top_k: int = _TOP_K) -> list[dict[str, Any]]:
        """Busca los chunks mas relevantes para la consulta.

        Args:
            query: Texto de la consulta del agricultor.
            top_k: Numero maximo de resultados a retornar (default 3).

        Returns:
            Lista de dicts con keys: text, source, date, title, score.
            Vacia si el corpus no esta disponible o no hay match.
        """
        if not query or not query.strip():
            return []

        self.load()

        if not self.is_available():
            return []

        # mypy necesita assert para estrechar el tipo de _vectorizer
        assert self._vectorizer is not None

        try:
            query_vec = self._vectorizer.transform([query.strip()])
            scores = cosine_similarity(query_vec, self._tfidf_matrix)[0]

            # Obtener indices ordenados por score descendente.
            # np.argsort ordena ascendente; tomamos los ultimos top_k.
            # kind="stable" garantiza orden determinista ante scores empatados
            # (el default quicksort no es estable): requisito de tests
            # deterministas y de respuestas reproducibles para el agricultor.
            top_indices = np.argsort(scores, kind="stable")[-top_k:][::-1]

            results: list[dict[str, Any]] = []
            for idx in top_indices:
                score = float(scores[idx])
                if score < _MIN_SCORE:
                    continue
                chunk = self._chunks[idx]
                results.append({
                    "text": chunk["text"],
                    "source": chunk["source"],
                    "date": chunk["date"],
                    "title": chunk["title"],
                    "score": score,
                })

            logger.debug(
                "RAG search — query=%.100s top_k=%d results=%d",
                query, top_k, len(results),
            )
            return results

        except (ValueError, TypeError, AttributeError) as exc:
            logger.warning("Error en busqueda RAG: %s", exc)
            return []

    def reload(self) -> None:
        """Recarga el corpus desde disco (util tras agregar nuevos documentos).

        Resetea el estado interno y fuerza una recarga completa.
        """
        self._chunks = []
        self._vectorizer = None
        self._tfidf_matrix = None
        self._loaded = False
        self.load()
        logger.info("Corpus RAG recargado desde disco")


# Singleton compartido. Se crea una unica instancia al importar el modulo.
# Importar desde otros modulos:
#   from app.services.rag_service import rag_corpus
rag_corpus = RAGCorpus()


def search_corpus_for_llm(query: str) -> str:
    """Busca en el corpus ODEPA y retorna resultados formateados para el LLM.

    Formatea los top-3 chunks con fuente y fecha para que el LLM pueda
    citarlos en su respuesta. Si no hay resultados, retorna un string
    indicando que no se encontro informacion relevante.

    Esta funcion es sync y se ejecuta en <2ms: no necesita asyncio.to_thread.

    Args:
        query: Texto de la consulta del agricultor.

    Returns:
        Texto con los resultados formateados para inyectar en <tool_response>.
    """
    results = rag_corpus.search(query, top_k=3)
    if not results:
        return (
            "No se encontraron documentos relevantes en el corpus "
            "oficial sobre esa consulta."
        )

    lines: list[str] = []
    for i, r in enumerate(results, 1):
        citation = f"Fuente: {r['source']}, {r['date']}"
        lines.append(
            f"[{i}] {r['text']}\n   ({citation})"
        )

    return "\n\n".join(lines)
