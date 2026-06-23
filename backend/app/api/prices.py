"""Endpoint REST de consulta de precios ODEPA.

Issue #16: GET /api/v1/prices/{producto}?mercado=X
Issue #18: El LLM usará get_price_for_llm() vía Tool Calling.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.prices import PriceListResponse, PriceResponse
from app.services.odepa_service import (
    format_price_text,
    list_mercados,
    list_products,
    query_latest_price,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["prices"])


@router.get("/prices/{producto}", response_model=PriceListResponse | PriceResponse)
def get_prices(
    producto: str,
    mercado: str | None = Query(None, description="Filtrar por mercado específico"),
    db: Session = Depends(get_db),  # noqa: B008 — FastAPI dependency injection pattern
) -> PriceListResponse | PriceResponse:
    """Consulta precios ODEPA para un producto.

    Si se especifica ?mercado=X, devuelve el precio más reciente para ese
    producto+mercado como PriceResponse.

    Si no se especifica mercado, devuelve todos los mercados donde el
    producto tiene datos como PriceListResponse.
    """
    # Validar producto no vacío
    if not producto or not producto.strip():
        raise HTTPException(status_code=400, detail="El producto no puede estar vacío.")

    producto_norm = producto.strip().lower()

    # Verificar que el producto existe en la DB
    productos = list_products(db)
    if producto_norm not in productos:
        raise HTTPException(
            status_code=404,
            detail=f"No hay datos de precio para '{producto_norm}'.",
        )

    if mercado:
        # Caso: mercado específico
        mercado_norm = mercado.strip()
        record = query_latest_price(db, producto_norm, mercado_norm)

        if record is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No se encontró el mercado '{mercado_norm}' "
                    f"para '{producto_norm}'."
                ),
            )

        return PriceResponse(
            producto=record.producto,
            mercado=record.mercado,
            precio_kg=float(record.precio_kg),
            unidad=record.unidad,
            fecha=record.fecha.isoformat(),
            texto=format_price_text(record),
        )

    # Caso: todos los mercados para este producto
    mercados = list_mercados(db, producto_norm)
    precios: list[PriceResponse] = []
    for m in mercados:
        record = query_latest_price(db, producto_norm, m)
        if record is not None:
            precios.append(
                PriceResponse(
                    producto=record.producto,
                    mercado=record.mercado,
                    precio_kg=float(record.precio_kg),
                    unidad=record.unidad,
                    fecha=record.fecha.isoformat(),
                    texto=format_price_text(record),
                )
            )

    return PriceListResponse(
        producto=producto_norm,
        total_mercados=len(precios),
        precios=precios,
    )


@router.get("/products", response_model=list[str])
def get_products(
    db: Session = Depends(get_db),  # noqa: B008
) -> list[str]:
    """Lista todos los productos disponibles en ODEPA, ordenados A-Z."""
    return list_products(db)


@router.get("/mercados", response_model=list[str])
def get_mercados(
    producto: str | None = Query(None, description="Filtrar por producto"),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[str]:
    """Lista mercados disponibles, opcionalmente filtrados por producto."""
    return list_mercados(db, producto)
