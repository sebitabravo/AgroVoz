"""Tests del servicio RAG (RAGCorpus + search_corpus_for_llm).

Cubre:
- Carga de corpus desde YAML
- Busqueda TF-IDF con resultados relevantes
- Chunks sin match retornan lista vacia
- Corpus vacio o directorio inexistente
- Formatting de search_corpus_for_llm
- Citas con fuente y fecha
- Fallback cuando no hay resultados
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
import yaml

from app.services.rag_service import RAGCorpus, search_corpus_for_llm

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def corpus_tmp_dir(tmp_path: Path) -> Path:
    """Crea un directorio temporal con un archivo de corpus YAML."""
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()

    doc = {
        "documentos": [
            {
                "titulo": "Test boletin papa",
                "fuente": "ODEPA — Boletín de la papa",
                "fecha": "enero 2026",
                "chunks": [
                    {
                        "texto": (
                            "El precio promedio mensual de la papa en enero 2026 "
                            "fue de $8.000 por saco de 25 kilos en el mercado "
                            "mayorista, segun ODEPA."
                        ),
                    },
                    {
                        "texto": (
                            "La papa se cultiva comercialmente desde la Region de "
                            "Coquimbo a Los Lagos, destacando la zona sur como "
                            "principal zona productora."
                        ),
                    },
                ],
            },
            {
                "titulo": "Agricultura Familiar",
                "fuente": "ODEPA — Estudios",
                "fecha": "2024",
                "chunks": [
                    {
                        "texto": (
                            "La pequena agricultura enfrenta importantes desafios "
                            "para lograr su plena integracion al sector."
                        ),
                    },
                ],
            },
        ],
    }

    yaml_path = corpus_dir / "test_papa.yaml"
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(doc, f, allow_unicode=True)

    return corpus_dir


@pytest.fixture
def empty_corpus_dir(tmp_path: Path) -> Path:
    """Directorio sin archivos YAML."""
    d = tmp_path / "empty_corpus"
    d.mkdir()
    return d


@pytest.fixture
def nonexistent_dir() -> Path:
    """Directorio que no existe."""
    return Path("/tmp/nonexistent_corpus_dir_xyz")


# ── Corpus real del repositorio (no el sintético de tests) ───────────


class TestRealCorpusDirectory:
    """El corpus versionado en corpus/ debe cargar sin errores.

    A diferencia del resto de la suite (fixtures sintéticas aisladas),
    esto valida el YAML que de verdad se despliega — protege contra un
    error de sintaxis en un archivo nuevo (#35 idea 9: ampliar fuentes).
    """

    def test_corpus_real_carga_sin_errores(self) -> None:
        corpus = RAGCorpus(corpus_dir=Path(__file__).resolve().parent.parent / "corpus")
        corpus.load()
        assert len(corpus._chunks) > 0

    def test_corpus_real_incluye_trigo_araucania(self) -> None:
        """Regresión de la fuente agregada: variedad Galactiko INIA (#35)."""
        corpus = RAGCorpus(corpus_dir=Path(__file__).resolve().parent.parent / "corpus")
        corpus.load()

        results = corpus.search("variedad de trigo invernal", top_k=5)

        assert any("Galactiko" in str(r.get("text")) for r in results)

    def test_corpus_real_excluye_snapshots_vencidos(self) -> None:
        """La búsqueda no sirve snapshots posteriores a su revisión."""
        corpus_dir = Path(__file__).resolve().parent.parent / "corpus"
        corpus = RAGCorpus(corpus_dir=corpus_dir, today=datetime.date(2028, 1, 1))

        assert corpus.search("programa INDAP") == []


# ── RAGCorpus: carga y disponibilidad ────────────────────────────────


class TestRAGCorpusLoad:
    """Verifica carga de corpus desde YAML."""

    def test_load_corpus_valido(self, corpus_tmp_dir: Path) -> None:
        """Carga exitosa con archivo YAML valido."""
        rag = RAGCorpus(corpus_dir=str(corpus_tmp_dir))
        rag.load()
        assert rag.is_available()
        assert len(rag._chunks) == 3  # 2 + 1 chunks

    def test_load_corpus_idempotente(self, corpus_tmp_dir: Path) -> None:
        """load() solo carga una vez."""
        rag = RAGCorpus(corpus_dir=str(corpus_tmp_dir))
        rag.load()
        rag.load()  # Segunda llamada no deberia recargar
        assert rag.is_available()
        assert len(rag._chunks) == 3

    def test_load_empty_dir(self, empty_corpus_dir: Path) -> None:
        """Directorio sin YAML no causa error."""
        rag = RAGCorpus(corpus_dir=str(empty_corpus_dir))
        rag.load()
        assert not rag.is_available()

    def test_load_nonexistent_dir(self, nonexistent_dir: Path) -> None:
        """Directorio inexistente no causa error."""
        rag = RAGCorpus(corpus_dir=str(nonexistent_dir))
        rag.load()
        assert not rag.is_available()


# ── RAGCorpus: busqueda ──────────────────────────────────────────────


class TestRAGCorpusSearch:
    """Verifica busqueda TF-IDF."""

    def test_search_relevante(self, corpus_tmp_dir: Path) -> None:
        """Query sobre precio retorna el chunk de precio."""
        rag = RAGCorpus(corpus_dir=str(corpus_tmp_dir))
        results = rag.search("precio de la papa en enero")
        assert len(results) >= 1
        assert "precio promedio" in results[0]["text"]
        assert "$8.000" in results[0]["text"]
        assert results[0]["source"] == "ODEPA — Boletín de la papa"
        assert results[0]["date"] == "enero 2026"

    def test_search_agricultura(self, corpus_tmp_dir: Path) -> None:
        """Query sobre agricultura retorna el chunk de agricultura."""
        rag = RAGCorpus(corpus_dir=str(corpus_tmp_dir))
        results = rag.search("agricultura familiar campesina")
        assert len(results) >= 1
        assert "pequena agricultura" in results[0]["text"]

    def test_search_sin_match(self, corpus_tmp_dir: Path) -> None:
        """Query sin relacion retorna lista vacia."""
        rag = RAGCorpus(corpus_dir=str(corpus_tmp_dir))
        results = rag.search("receta de cocina con zapallo")
        assert len(results) == 0

    def test_search_query_vacia(self, corpus_tmp_dir: Path) -> None:
        """Query vacia retorna lista vacia."""
        rag = RAGCorpus(corpus_dir=str(corpus_tmp_dir))
        results = rag.search("")
        assert results == []

    def test_search_top_k_respetado(self, corpus_tmp_dir: Path) -> None:
        """Solo retorna top_k resultados."""
        rag = RAGCorpus(corpus_dir=str(corpus_tmp_dir))
        results = rag.search("papa", top_k=1)
        assert len(results) <= 1

    def test_search_sin_corpus(self, empty_corpus_dir: Path) -> None:
        """Corpus vacio retorna lista vacia."""
        rag = RAGCorpus(corpus_dir=str(empty_corpus_dir))
        results = rag.search("papa")
        assert results == []

    def test_search_resultados_tienen_campos(self, corpus_tmp_dir: Path) -> None:
        """Cada resultado tiene text, source, date, title, score."""
        rag = RAGCorpus(corpus_dir=str(corpus_tmp_dir))
        results = rag.search("papa precio")
        assert len(results) >= 1
        r = results[0]
        assert "text" in r
        assert "source" in r
        assert "date" in r
        assert "title" in r
        assert "score" in r
        assert isinstance(r["score"], float)
        assert 0.0 < r["score"] <= 1.0


# ── RAGCorpus: recarga ───────────────────────────────────────────────


class TestRAGCorpusReload:
    """Verifica recarga del corpus."""

    def test_reload_resetea_estado(self, corpus_tmp_dir: Path) -> None:
        """reload() recarga desde disco."""
        rag = RAGCorpus(corpus_dir=str(corpus_tmp_dir))
        rag.load()
        assert rag.is_available()
        rag.reload()
        assert rag.is_available()
        assert len(rag._chunks) == 3


# ── search_corpus_for_llm ────────────────────────────────────────────


class TestSearchCorpusForLLM:
    """Verifica la funcion de busqueda formateada para el LLM."""

    def test_formato_con_resultados(self, corpus_tmp_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Resultados incluyen texto, fuente y fecha."""
        monkeypatch.setattr("app.services.rag_service.rag_corpus._corpus_dir", corpus_tmp_dir)
        # Resetear el singleton para que recargue desde el nuevo directorio
        monkeypatch.setattr("app.services.rag_service.rag_corpus._loaded", False)

        result = search_corpus_for_llm("precio de la papa enero")
        assert "precio promedio" in result
        assert "ODEPA — Boletín de la papa" in result
        assert "enero 2026" in result
        assert "[1]" in result

    def test_formato_sin_resultados(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sin resultados, retorna mensaje de no encontrado."""
        # Usar directorio vacio temporal
        import tempfile

        empty_dir = Path(tempfile.mkdtemp())
        (empty_dir / ".gitkeep").write_text("")

        monkeypatch.setattr("app.services.rag_service.rag_corpus._corpus_dir", empty_dir)
        monkeypatch.setattr("app.services.rag_service.rag_corpus._loaded", False)

        result = search_corpus_for_llm("receta de cocina")
        assert "No se encontraron documentos" in result

    def test_multiplos_resultados_tienen_numeracion(
        self, corpus_tmp_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Multiples chunks se numeran [1], [2], etc."""
        monkeypatch.setattr("app.services.rag_service.rag_corpus._corpus_dir", corpus_tmp_dir)
        monkeypatch.setattr("app.services.rag_service.rag_corpus._loaded", False)

        result = search_corpus_for_llm("papa agricultura")
        # Deberia tener al menos 2 resultados
        assert "[1]" in result
        assert "[2]" in result or "[3]" in result


# ── RAGCorpus: robustez y determinismo ───────────────────────────────


class TestRAGCorpusRobustez:
    """Casos borde: determinismo, top_k > corpus, tildes y YAML corrupto."""

    def test_search_determinista(self, corpus_tmp_dir: Path) -> None:
        """La misma query dos veces retorna resultados idénticos.

        TF-IDF es determinista y argsort usa kind='stable', así que el orden
        (incluso ante scores empatados) no debe variar entre ejecuciones.
        """
        rag = RAGCorpus(corpus_dir=str(corpus_tmp_dir))
        r1 = rag.search("papa precio mercado")
        r2 = rag.search("papa precio mercado")
        assert [c["text"] for c in r1] == [c["text"] for c in r2]
        assert [c["score"] for c in r1] == [c["score"] for c in r2]

    def test_search_top_k_mayor_que_corpus(self, corpus_tmp_dir: Path) -> None:
        """Pedir más resultados que chunks no rompe ni inventa entradas."""
        rag = RAGCorpus(corpus_dir=str(corpus_tmp_dir))
        # El corpus tiene 3 chunks; top_k=10 debe acotar sin error.
        results = rag.search("papa", top_k=10)
        assert len(results) <= 3

    def test_search_tildes_matchea(self, corpus_tmp_dir: Path) -> None:
        """strip_accents='unicode': 'región' (con tilde) matchea 'Region'."""
        rag = RAGCorpus(corpus_dir=str(corpus_tmp_dir))
        results = rag.search("región de coquimbo")
        assert len(results) >= 1

    def test_load_ignora_yaml_malformado(self, tmp_path: Path) -> None:
        """Un YAML corrupto se ignora; los archivos válidos siguen cargando."""
        corpus_dir = tmp_path / "corpus"
        corpus_dir.mkdir()

        valido = {
            "documentos": [
                {
                    "titulo": "OK",
                    "fuente": "ODEPA",
                    "fecha": "2026",
                    "chunks": [{"texto": "la papa cuesta ocho mil pesos el saco"}],
                }
            ]
        }
        with open(corpus_dir / "ok.yaml", "w", encoding="utf-8") as f:
            yaml.dump(valido, f, allow_unicode=True)
        # Sintaxis YAML rota (llaves/corchetes sin cerrar).
        (corpus_dir / "roto.yaml").write_text("documentos: [ {titulo: 'x', chunks: [", encoding="utf-8")

        rag = RAGCorpus(corpus_dir=str(corpus_dir))
        rag.load()

        assert rag.is_available()
        assert len(rag._chunks) == 1

    def test_manifest_invalido_falla_cerrado(self, tmp_path: Path) -> None:
        """Un manifest presente pero inválido nunca activa el cargador legado."""
        corpus_dir = tmp_path / "corpus"
        corpus_dir.mkdir()
        (corpus_dir / "fuentes_datos.yaml").write_text("fuentes: [", encoding="utf-8")
        (corpus_dir / "documento.yaml").write_text(
            """
documentos:
  - titulo: Documento no validado
    fuente: Fuente no validada
    fecha: 2026
    chunks:
      - texto: la papa cuesta ocho mil pesos
""",
            encoding="utf-8",
        )

        rag = RAGCorpus(corpus_dir=str(corpus_dir))

        assert rag.search("precio de la papa") == []
