"""Endpoints admin para operar el catálogo local del Data Hub."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.admin.deps import require_admin_key
from app.core.database import get_db
from app.schemas.data_hub import DataHubStatusResponse, DataHubSyncResponse
from app.services.data_hub_remote import sync_remote_data_hub
from app.services.data_hub_service import (
    DataHubValidationError,
    get_data_hub_status,
    sync_data_hub,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/data-hub",
    tags=["admin-data-hub"],
    dependencies=[Depends(require_admin_key)],
)


@router.get("/status", response_model=DataHubStatusResponse)
def data_hub_status(
    db: Session = Depends(get_db),  # noqa: B008 — FastAPI dependency injection pattern
) -> DataHubStatusResponse:
    """Entrega estado, vigencia y conteos del catálogo local."""
    try:
        return DataHubStatusResponse.model_validate(get_data_hub_status(db))
    except DataHubValidationError as exc:
        logger.error("No se pudo leer manifest Data Hub — error=%s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El catálogo Data Hub no está disponible.",
        ) from exc


@router.post("/sync", response_model=DataHubSyncResponse)
def data_hub_sync(
    remote: bool = Query(
        default=False,
        description="Verifica endpoints oficiales; desactivado por defecto para no usar red.",
    ),
    db: Session = Depends(get_db),  # noqa: B008 — FastAPI dependency injection pattern
) -> DataHubSyncResponse:
    """Sincroniza el corpus local y opcionalmente verificadores remotos oficiales."""
    try:
        result = sync_data_hub(db)
    except DataHubValidationError as exc:
        logger.warning("Sync Data Hub rechazada — error=%s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="El manifest o snapshot del Data Hub no es válido.",
        ) from exc
    except (OSError, SQLAlchemyError) as exc:
        logger.error("Sync Data Hub falló — error=%s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo sincronizar el Data Hub.",
        ) from exc
    remote_sources_synced = 0
    remote_source_errors: list[str] = []
    if remote:
        try:
            remote_result = sync_remote_data_hub(db)
        except DataHubValidationError as exc:
            logger.warning("Sync remoto Data Hub rechazada — error=%s", type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="La configuración remota del Data Hub no es válida.",
            ) from exc
        except (OSError, SQLAlchemyError) as exc:
            logger.error("Sync remoto Data Hub falló — error=%s", type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No se pudo verificar el Data Hub remoto.",
            ) from exc
        remote_sources_synced = remote_result.sources_synced
        remote_source_errors = list(remote_result.source_errors)
    return DataHubSyncResponse(
        sources_synced=result.sources_synced,
        facts_synced=result.facts_synced,
        stale_sources=list(result.stale_sources),
        not_connected_sources=list(result.not_connected_sources),
        remote_sources_synced=remote_sources_synced,
        remote_source_errors=remote_source_errors,
    )
