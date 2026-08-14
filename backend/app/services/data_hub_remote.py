"""Verificadores remotos explícitos para fuentes oficiales del Data Hub.

Estos adaptadores solo se ejecutan desde el sync administrativo autenticado.
No usan URLs entregadas por usuarios, no descargan bases masivas y no forman
parte del camino de una pregunta de voz.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.services.data_hub_service import (
    DataHubValidationError,
    SourceManifestEntry,
    load_manifest,
    mark_data_source_error,
    mark_data_source_success,
)

_DEFAULT_TIMEOUT = httpx.Timeout(8.0, connect=3.0)
_MAX_RESPONSE_BYTES = 12 * 1024 * 1024

# Endpoints fijos y públicos verificados contra los portales oficiales. No se
# toman desde el manifest ni desde parámetros HTTP para evitar SSRF.
_INIA_STATIONS_URL = "https://agrometeorologia.cl/assets/db/items-resumen.json"
_CIREN_VALIDATOR_URL = "https://api-ideminagri.ciren.cl/api/validador/valida-coordenada-comuna/"
_INE_CATALOG_URL = (
    "https://www.ine.gob.cl/estadisticas-por-tema/agricultura-y-medio-ambiente/"
    "censo-agropecuario/getArchivos/"
)
_INE_BASE_FOLDER_ID = "22686352-9895-4615-aec2-7d55ccc924de"


class RemoteDataHubError(RuntimeError):
    """Falla remota reducida a un código seguro y estable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RemoteSourceVerification:
    """Resultado mínimo de verificar un catálogo oficial."""

    record_count: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class RemoteDataHubSyncResult:
    """Resumen de una sincronización remota parcial o completa."""

    sources_synced: int
    source_errors: tuple[str, ...]


def _payload_hash(payload: object) -> str:
    """Calcula un hash reproducible sin persistir el payload remoto."""
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RemoteDataHubError("invalid_payload") from exc
    return hashlib.sha256(encoded).hexdigest()


def _request_json(
    client: httpx.Client,
    *,
    method: str,
    url: str,
    data: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> object:
    """Consulta un endpoint fijo con límite de tamaño y errores saneados."""
    try:
        if method == "GET":
            request_method = "GET"
        elif method == "POST":
            request_method = "POST"
        else:
            raise ValueError(f"Método no soportado: {method}")
        with client.stream(
            request_method,
            url,
            params=params,
            data=data or {},
        ) as response:
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    declared_size = 0
                if declared_size > _MAX_RESPONSE_BYTES:
                    raise RemoteDataHubError("response_too_large")
            payload = bytearray()
            for chunk in response.iter_bytes():
                payload.extend(chunk)
                if len(payload) > _MAX_RESPONSE_BYTES:
                    raise RemoteDataHubError("response_too_large")
    except httpx.HTTPStatusError as exc:
        raise RemoteDataHubError("http_status") from exc
    except httpx.RequestError as exc:
        raise RemoteDataHubError("network_error") from exc
    try:
        return json.loads(payload)
    except ValueError as exc:
        raise RemoteDataHubError("invalid_json") from exc


def _verify_inia_stations(client: httpx.Client) -> RemoteSourceVerification:
    """Verifica el resumen oficial de estaciones sin ingerir coordenadas."""
    payload = _request_json(client, method="GET", url=_INIA_STATIONS_URL)
    if not isinstance(payload, list):
        raise RemoteDataHubError("invalid_station_catalog")

    # El JSON incluye una entrada de configuración sin id/nombre. Solo las
    # filas con identidad de estación cuentan como registros verificables.
    stations = [
        row
        for row in payload
        if isinstance(row, dict) and row.get("id") and row.get("nombre")
    ]
    if not stations:
        raise RemoteDataHubError("empty_station_catalog")
    identity = [
        {
            "id": row.get("id"),
            "nombre": row.get("nombre"),
            "status": row.get("status"),
        }
        for row in stations
    ]
    return RemoteSourceVerification(len(stations), _payload_hash(identity))


def _verify_ciren_ide(client: httpx.Client) -> RemoteSourceVerification:
    """Verifica el validador público IDE Minagri con una coordenada fija."""
    payload = _request_json(
        client,
        method="GET",
        url=_CIREN_VALIDATOR_URL,
        params={"x": "-72.67", "y": "-38.74", "comuna": "09101", "sr": "4326"},
    )
    if not isinstance(payload, dict) or payload.get("mensaje") != "Ok":
        raise RemoteDataHubError("invalid_ide_response")
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise RemoteDataHubError("empty_ide_response")
    return RemoteSourceVerification(len(data), _payload_hash(payload))


def _verify_ine_catalog(client: httpx.Client) -> RemoteSourceVerification:
    """Verifica el catálogo oficial del VIII Censo sin bajar sus archivos."""
    payload = _request_json(
        client,
        method="POST",
        url=_INE_CATALOG_URL,
        data={"idFolder": _INE_BASE_FOLDER_ID},
    )
    if not isinstance(payload, dict):
        raise RemoteDataHubError("invalid_ine_catalog")
    documents = payload.get("documento")
    if not isinstance(documents, list):
        raise RemoteDataHubError("invalid_ine_catalog")

    catalog: list[dict[str, str]] = []
    for document in documents:
        if not isinstance(document, dict):
            raise RemoteDataHubError("invalid_ine_document")
        title = document.get("Titulo")
        source_url = document.get("Url")
        file_type = document.get("Tipo")
        if not isinstance(title, str) or not title.strip():
            raise RemoteDataHubError("invalid_ine_document")
        if not isinstance(source_url, str) or not source_url.strip():
            raise RemoteDataHubError("invalid_ine_document")
        parsed = urlparse(source_url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if hostname != "ine.gob.cl" and not hostname.endswith(".ine.gob.cl"):
            raise RemoteDataHubError("untrusted_ine_document_host")
        if parsed.scheme not in {"http", "https"}:
            raise RemoteDataHubError("invalid_ine_document_url")
        catalog.append(
            {
                "title": title.strip(),
                "url": source_url.strip(),
                "type": file_type.strip() if isinstance(file_type, str) else "",
            }
        )
    if not catalog:
        raise RemoteDataHubError("empty_ine_catalog")
    return RemoteSourceVerification(len(catalog), _payload_hash(catalog))


def verify_remote_source(
    entry: SourceManifestEntry,
    client: httpx.Client,
) -> RemoteSourceVerification:
    """Ejecuta el adaptador remoto declarado para una fuente."""
    adapter = entry.remote_adapter
    if adapter == "inia_red_agrometeorologica":
        return _verify_inia_stations(client)
    if adapter == "ciren_ide_minagri":
        return _verify_ciren_ide(client)
    if adapter == "ine_censo_catalogo":
        return _verify_ine_catalog(client)
    raise DataHubValidationError(f"Fuente sin adaptador remoto soportado: {entry.key}")


def sync_remote_data_hub(
    db: Session,
    *,
    manifest_path: str | None = None,
    today: datetime.date | None = None,
    client: httpx.Client | None = None,
) -> RemoteDataHubSyncResult:
    """Sincroniza verificaciones oficiales, conservando fallas por fuente."""
    entries = [entry for entry in load_manifest(manifest_path) if entry.remote_adapter]
    current_day = today or datetime.date.today()
    owns_client = client is None
    http_client = client or httpx.Client(
        timeout=_DEFAULT_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": "AgroVoz-DataHub/1.0"},
    )
    sources_synced = 0
    source_errors: list[str] = []
    try:
        for entry in entries:
            try:
                result = verify_remote_source(entry, http_client)
            except (DataHubValidationError, RemoteDataHubError) as exc:
                code = exc.code if isinstance(exc, RemoteDataHubError) else "invalid_adapter"
                mark_data_source_error(db, entry.key, error_code=code)
                source_errors.append(f"{entry.key}:{code}")
                continue
            mark_data_source_success(
                db,
                entry.key,
                record_count=result.record_count,
                valid_until=current_day,
                content_hash=result.content_hash,
            )
            sources_synced += 1
    finally:
        if owns_client:
            http_client.close()
    return RemoteDataHubSyncResult(
        sources_synced=sources_synced,
        source_errors=tuple(source_errors),
    )


__all__ = [
    "RemoteDataHubError",
    "RemoteDataHubSyncResult",
    "RemoteSourceVerification",
    "sync_remote_data_hub",
    "verify_remote_source",
]
