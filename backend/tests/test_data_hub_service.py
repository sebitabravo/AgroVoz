"""Pruebas del catálogo, sincronización y frescura del Data Hub."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.data_hub import DataFact, DataSource
from app.services.data_hub_service import (
    DataHubValidationError,
    get_data_hub_status,
    load_manifest,
    mark_data_source_success,
    sync_data_hub,
)

_CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus"
_MANIFEST = _CORPUS_DIR / "fuentes_datos.yaml"


def _session() -> Session:
    """Crea una DB aislada con todos los modelos registrados."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


class TestDataHubManifest:
    """El manifest real debe ser completo y validar sus dominios."""

    def test_manifest_real_valido_y_distingue_fuentes(self) -> None:
        entries = load_manifest(_MANIFEST)

        assert len(entries) == 9
        assert {entry.key for entry in entries} >= {
            "odepa_precios_mayoristas",
            "openmeteo_clima",
            "inia_conocimiento_agronomico",
            "ciren_ide_minagri",
        }
        assert all(entry.url.startswith("https://") for entry in entries)
        assert any(not entry.connected for entry in entries)

    def test_manifest_rechaza_url_no_https(self, tmp_path: Path) -> None:
        invalid = tmp_path / "fuentes_datos.yaml"
        invalid.write_text(
            _MANIFEST.read_text(encoding="utf-8").replace(
                "https://datos.odepa.gob.cl/es/",
                "http://datos.odepa.gob.cl/es/",
                1,
            ),
            encoding="utf-8",
        )

        with pytest.raises(DataHubValidationError, match="HTTPS"):
            load_manifest(invalid)

    def test_manifest_rechaza_version_esquema_invalida(self, tmp_path: Path) -> None:
        invalid = tmp_path / "fuentes_datos.yaml"
        invalid.write_text(
            _MANIFEST.read_text(encoding="utf-8").replace(
                "    conectado: true\n", "    conectado: true\n    version_esquema: 0\n", 1
            ),
            encoding="utf-8",
        )

        with pytest.raises(DataHubValidationError, match="version_esquema"):
            load_manifest(invalid)


class TestDataHubSync:
    """La carga local es idempotente y conserva estados honestos."""

    def test_sync_idempotente_sin_duplicar_hechos(self) -> None:
        with _session() as db:
            first = sync_data_hub(
                db,
                manifest_path=_MANIFEST,
                corpus_dir=_CORPUS_DIR,
                today=datetime.date(2026, 8, 13),
            )
            first_count = db.scalar(select(DataFact.id).order_by(DataFact.id.desc()))
            total_first = db.query(DataFact).count()

            second = sync_data_hub(
                db,
                manifest_path=_MANIFEST,
                corpus_dir=_CORPUS_DIR,
                today=datetime.date(2026, 8, 13),
            )

            assert first.sources_synced == second.sources_synced == 9
            assert first.facts_synced == second.facts_synced == 114
            assert first_count is not None
            assert db.query(DataFact).count() == total_first == 114
            assert db.query(DataSource).count() == 9
            assert db.query(DataFact.fact_hash).distinct().count() == 114

    def test_sync_marca_stale_sin_servirlo_como_actual(self) -> None:
        with _session() as db:
            result = sync_data_hub(
                db,
                manifest_path=_MANIFEST,
                corpus_dir=_CORPUS_DIR,
                today=datetime.date(2028, 1, 1),
            )
            status = get_data_hub_status(db, today=datetime.date(2028, 1, 1))

            assert result.stale_sources
            assert status["stale_sources"] == 5
            assert status["not_connected_sources"] == 4
            sources = status["sources"]
            assert isinstance(sources, list)
            odepa = next(
                row for row in sources if row["key"] == "odepa_precios_mayoristas"
            )
            assert odepa["status"] == "stale"

    def test_sync_no_requiere_red_para_snapshots(self) -> None:
        with _session() as db:
            result = sync_data_hub(db, manifest_path=_MANIFEST, corpus_dir=_CORPUS_DIR)

            assert result.facts_synced > 100
            assert "ciren_ide_minagri" in result.not_connected_sources

    def test_sync_invalido_no_reemplaza_la_carga_anterior(self, tmp_path: Path) -> None:
        """Una falla de validación conserva los hechos ya confirmados."""
        invalid_manifest = tmp_path / "fuentes_datos.yaml"
        invalid_manifest.write_text(
            _MANIFEST.read_text(encoding="utf-8").replace(
                "https://datos.odepa.gob.cl/es/",
                "http://datos.odepa.gob.cl/es/",
                1,
            ),
            encoding="utf-8",
        )

        with _session() as db:
            sync_data_hub(db, manifest_path=_MANIFEST, corpus_dir=_CORPUS_DIR)
            with pytest.raises(DataHubValidationError):
                sync_data_hub(db, manifest_path=invalid_manifest, corpus_dir=_CORPUS_DIR)

            assert db.query(DataFact).count() == 114
            assert db.query(DataSource).count() == 9

    def test_adaptador_odepa_marca_frescura_real(self) -> None:
        """El sync estructurado de ODEPA puede confirmar el estado del catálogo."""
        with _session() as db:
            sync_data_hub(db, manifest_path=_MANIFEST, corpus_dir=_CORPUS_DIR)
            mark_data_source_success(
                db,
                "odepa_precios_mayoristas",
                record_count=79,
                valid_until=datetime.date(2026, 8, 14),
            )

            status = get_data_hub_status(db, today=datetime.date(2026, 8, 13))
            sources = cast(list[dict[str, object]], status["sources"])
            odepa = next(row for row in sources if row["key"] == "odepa_precios_mayoristas")
            assert odepa["status"] == "healthy"
            assert odepa["record_count"] == 79
            assert odepa["last_success_at"] is not None

class TestDataHubConversational:
    """La capa de voz puede explicar el ecosistema sin alucinar capacidades."""

    async def test_keyword_ecosistema_distingue_conectadas(self) -> None:
        from app.services.llm_keywords import _force_keyword_tool

        response = await _force_keyword_tool("¿Qué servicios tiene AgroVoz?")

        assert response is not None
        assert "ODEPA" in response
        assert "Open-Meteo" in response
        assert "no conectadas" in response
        assert "marketplace" in response
