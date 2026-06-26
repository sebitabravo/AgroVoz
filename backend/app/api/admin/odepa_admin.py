"""Endpoints admin de ODEPA (JSON, T5.2).

- status: filas totales, fecha de dato más reciente, productos/mercados.
- sync: dispara sincronización manual (async, descarga CSV + upsert).
- products: lista productos disponibles.

Todos requieren header X-Admin-Key.
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.admin.deps import require_admin_key
from app.core.database import get_db
from app.services.metrics_service import get_odepa_status
from app.services.odepa_service import list_products, sync_odepa

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/odepa",
    tags=["admin-odepa"],
    dependencies=[Depends(require_admin_key)],
)


@router.get("/status")
def status(db: Session = Depends(get_db)) -> dict[str, object]:  # noqa: B008
    """Estado de la data ODEPA: totales, fecha más reciente, cardinalidades.

    El campo ``ultima_fecha`` es proxy de la última sync exitosa: ODEPA
    publica datos del día anterior, así que si la fecha máxima es de hoy-1
    la sync está al día.
    """
    s = get_odepa_status(db)
    return {
        "total_filas": s.total_filas,
        "ultima_fecha": s.ultima_fecha.isoformat() if s.ultima_fecha else None,
        "productos": s.productos,
        "mercados": s.mercados,
    }


@router.post("/sync")
async def sync() -> dict[str, object]:
    """Dispara sincronización ODEPA manual (descarga + parseo + upsert).

    El scheduler automático corre a las 06:00 AM; este endpoint es para
    forzar un refresh desde el dashboard. Es async (httpx) y bloquea hasta
    terminar (~5-10s en VPS CX43).
    """
    resultado = await sync_odepa()
    logger.info(
        "Sync ODEPA manual: %d insertados, %d actualizados",
        resultado.insertados,
        resultado.actualizados,
    )
    return {
        "insertados": resultado.insertados,
        "actualizados": resultado.actualizados,
        "total": resultado.total,
    }


@router.get("/products")
def products(db: Session = Depends(get_db)) -> list[str]:  # noqa: B008
    """Lista productos disponibles en ODEPA, ordenados A-Z."""
    return list_products(db)
