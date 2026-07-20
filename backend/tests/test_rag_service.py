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
