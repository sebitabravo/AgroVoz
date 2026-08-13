"""Pruebas deterministas de los verificadores remotos del Data Hub."""

from __future__ import annotations

import datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.data_hub import DataSource
from app.services.data_hub_remote import (
    RemoteDataHubError,
    sync_remote_data_hub,
    verify_remote_source,
)
from app.services.data_hub_service import load_manifest, sync_data_hub

_CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus"
_MANIFEST = _CORPUS_DIR / "fuentes_datos.yaml"


def _session() -> Session:
    """Crea una base aislada con las tablas del Data Hub."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _entry(key: str):
    """Obtiene una entrada real del manifest para evitar fixtures divergentes."""
    return next(entry for entry in load_manifest(_MANIFEST) if entry.key == key)


def test_verifica_catalogo_inia_sin_persistir_datos_de_estacion() -> None:
    """El adaptador cuenta estaciones y solo conserva un hash de identidad."""
    payload = [
        {"id": "1", "nombre": "Estación A", "status": "active", "latitud": -38.7},
        {"id": "2", "nombre": "Estación B", "status": "inactive", "latitud": -39.0},
        {"config": True},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL(
            "https://agrometeorologia.cl/assets/db/items-resumen.json"
        )
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = verify_remote_source(_entry("inia_red_agrometeorologica"), client)

    assert result.record_count == 2
    assert len(result.content_hash) == 64


def test_verifica_ciren_con_endpoint_fijo_y_respuesta_ok() -> None:
    """El probe IDE usa coordenadas públicas fijas y valida el contrato JSON."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {
            "x": "-72.67",
            "y": "-38.74",
            "comuna": "09101",
            "sr": "4326",
        }
        return httpx.Response(
            200,
            json={
                "data": [{"comuna": "09101", "existeComuna": "Si"}],
                "mensaje": "Ok",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = verify_remote_source(_entry("ciren_ide_minagri"), client)

    assert result.record_count == 1


def test_verifica_catalogo_ine_sin_descargar_archivos_masivos() -> None:
    """El adaptador solo procesa el índice oficial de archivos."""
    payload = {
        "documento": [
            {
                "Titulo": "Seccion_1",
                "Url": "http://www.ine.gob.cl/docs/base.csv",
                "Tipo": ".CSV",
            },
            {
                "Titulo": "Diccionario",
                "Url": "https://www.ine.gob.cl/docs/diccionario.xlsx",
                "Tipo": ".XLSX",
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/getArchivos/")
        assert request.content == b"idFolder=22686352-9895-4615-aec2-7d55ccc924de"
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = verify_remote_source(_entry("ine_censo_agropecuario"), client)

    assert result.record_count == 2


def test_adaptador_falla_cerrado_con_json_invalido() -> None:
    """Un payload remoto incompleto no se convierte en fuente saludable."""
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(RemoteDataHubError, match="invalid_json"),
    ):
        verify_remote_source(_entry("inia_red_agrometeorologica"), client)


def test_ciren_convierte_falla_de_red_en_error_saneado() -> None:
    """Una caída de CIREN no interrumpe el procesamiento de las otras fuentes."""
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret internal network detail")

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(RemoteDataHubError, match="network_error"),
    ):
        verify_remote_source(_entry("ciren_ide_minagri"), client)


def test_sync_remoto_actualiza_exitos_y_conserva_error_por_fuente() -> None:
    """El sync remoto es parcial: una fuente caída no borra éxitos previos."""
    with _session() as db:
        sync_data_hub(db, manifest_path=_MANIFEST, corpus_dir=_CORPUS_DIR)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "agrometeorologia.cl":
                return httpx.Response(
                    200,
                    json=[{"id": "1", "nombre": "Estación A", "status": "active"}],
                )
            if request.url.host == "api-ideminagri.ciren.cl":
                return httpx.Response(503, json={"mensaje": "caido"})
            assert request.url.host == "www.ine.gob.cl"
            return httpx.Response(
                200,
                json={
                    "documento": [
                        {
                            "Titulo": "Seccion_1",
                            "Url": "https://www.ine.gob.cl/docs/base.csv",
                            "Tipo": ".CSV",
                        }
                    ]
                },
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = sync_remote_data_hub(
                db,
                manifest_path=str(_MANIFEST),
                today=datetime.date(2026, 8, 13),
                client=client,
            )

        assert result.sources_synced == 2
        assert result.source_errors == ("ciren_ide_minagri:http_status",)
        sources = {
            source.key: source
            for source in db.scalars(select(DataSource)).all()
        }
        assert sources["inia_red_agrometeorologica"].status == "healthy"
        assert sources["inia_red_agrometeorologica"].record_count == 1
        assert sources["ciren_ide_minagri"].status == "error"
        assert sources["ciren_ide_minagri"].last_error_code == "http_status"
        assert sources["ine_censo_agropecuario"].status == "healthy"

        # Recargar el corpus local no debe ocultar un error remoto reciente.
        sync_data_hub(db, manifest_path=_MANIFEST, corpus_dir=_CORPUS_DIR)
        assert sources["ciren_ide_minagri"].status == "error"
        assert sources["ciren_ide_minagri"].last_error_code == "http_status"
