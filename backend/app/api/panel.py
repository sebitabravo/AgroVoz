"""Router del panel PWA del agricultor: link firmado, sin login (C3).

Endpoint de solo lectura para el resumen del productor. La identidad se
resuelve desde el token en la URL, no desde una sesión ni una cuenta.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.services.panel_service import get_panel_summary, verify_panel_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/panel", tags=["panel"])


def _require_panel_enabled() -> None:
    """Bloquea el endpoint si el panel está deshabilitado."""
    if not settings.farmer_panel_enabled:
        raise HTTPException(
            status_code=503,
            detail="El panel web todavía no está disponible.",
        )


_panel_enabled_dep = Depends(_require_panel_enabled)


@router.get("/{token}")
def get_panel(
    token: str = Path(..., description="Token firmado recibido por WhatsApp."),
    _enabled: None = _panel_enabled_dep,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, object]:
    """Devuelve el resumen del productor si el token es válido y no venció."""
    phone_hash = verify_panel_token(token)
    if phone_hash is None:
        raise HTTPException(
            status_code=401,
            detail="Link inválido o vencido. Pide uno nuevo por WhatsApp.",
        )

    summary = get_panel_summary(db, phone_hash)
    if summary is None:
        raise HTTPException(
            status_code=404,
            detail="No hay datos registrados para este link.",
        )

    return {
        "comuna": summary.comuna,
        "cultivos": summary.cultivos,
        "parcelas": summary.parcelas,
        "alertas": summary.alertas,
    }
