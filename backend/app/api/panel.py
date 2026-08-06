"""Router del panel PWA del agricultor: link firmado, sin login (C3).

Endpoints de solo lectura para el resumen y el historial de precios ODEPA del
productor. La identidad se resuelve desde el token en la URL, no desde una
sesión ni una cuenta.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.schemas.panel import PanelPricePointResponse, PanelPriceSeriesResponse, PanelPricesResponse
from app.services.panel_service import (
    PanelPriceHistory,
    get_panel_price_history,
    get_panel_summary,
    verify_panel_token,
)

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


def _price_history_to_response(history: PanelPriceHistory) -> PanelPricesResponse:
    """Convierte el resultado del servicio al contrato JSON del panel."""
    cultivos = [
        PanelPriceSeriesResponse(
            cultivo=serie.cultivo,
            mercado=serie.mercado,
            unidad=serie.unidad,
            fuente=serie.fuente,
            precios=[
                PanelPricePointResponse(fecha=punto.fecha.isoformat(), precio=float(punto.precio))
                for punto in serie.precios
            ],
        )
        for serie in history.cultivos
    ]
    return PanelPricesResponse(
        desde=history.desde.isoformat(),
        hasta=history.hasta.isoformat(),
        dias=(history.hasta - history.desde).days + 1,
        cultivos=cultivos,
    )


@router.get("/{token}/prices", response_model=PanelPricesResponse)
def get_panel_prices(
    token: str = Path(..., description="Token firmado recibido por WhatsApp."),
    _enabled: None = _panel_enabled_dep,
    db: Session = Depends(get_db),  # noqa: B008
) -> PanelPricesResponse:
    """Devuelve los últimos 28 días de precios de los cultivos del productor."""
    phone_hash = verify_panel_token(token)
    if phone_hash is None:
        raise HTTPException(
            status_code=401,
            detail="Link inválido o vencido. Pide uno nuevo por WhatsApp.",
        )

    history = get_panel_price_history(db, phone_hash)
    if history is None:
        raise HTTPException(
            status_code=404,
            detail="No hay datos registrados para este link.",
        )
    return _price_history_to_response(history)


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
