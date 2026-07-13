"""Servicio de resumen de actividad del agricultor.

Genera un resumen hablado con estadísticas de las consultas del agricultor
en los últimos 30 días: total de consultas, producto más consultado y
variación de precio de ese producto.

Se activa con el comando "resumen" por voz y retorna texto listo para TTS.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.consultation import Consultation

logger = logging.getLogger(__name__)

# Ventana de tiempo para el resumen (dias).
_RESUMEN_WINDOW_DAYS = 30


def _formatear_variacion_pct(actual: Decimal, antiguo: Decimal) -> str:
    """Formatea la variación porcentual como texto hablado.

    Args:
        actual: Precio actual.
        antiguo: Precio anterior.

    Returns:
        Texto como "subido un 8 por ciento" o "bajado un 3 por ciento".
    """
    if antiguo == 0:
        return "no puedo calcular la variacion"
    variacion = (actual - antiguo) / antiguo * 100
    if abs(variacion) < Decimal("0.05"):
        return "se mantiene igual"
    direccion = "subido" if variacion > 0 else "bajado"
    pct = abs(variacion).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    entero, _, dec = str(pct).partition(".")
    pct_str = entero if not dec or dec == "0" else f"{entero} coma {dec}"
    return f"{direccion} un {pct_str} por ciento"


def get_consultation_summary(session: Session, phone_hash: str) -> str:
    """Genera un resumen hablado de la actividad del agricultor.

    Agrega las consultas del phone_hash en los últimos 30 días:
    - Total de consultas
    - Producto más consultado (top 1)
    - Variación de precio del producto top (reusa get_price_history)

    Si no hay consultas previas, retorna un mensaje amable invitando a consultar.

    Args:
        session: Sesión de base de datos SQLAlchemy.
        phone_hash: Hash del número de teléfono del agricultor.

    Returns:
        Texto en español chileno listo para TTS.
    """
    fecha_corte = datetime.now() - timedelta(days=_RESUMEN_WINDOW_DAYS)

    # 1. Contar total de consultas en los últimos 30 días.
    total_stmt = (
        select(func.count(Consultation.id))
        .where(
            Consultation.phone_hash == phone_hash,
            Consultation.created_at >= fecha_corte,
        )
    )
    total: int = session.scalar(total_stmt) or 0

    if total == 0:
        return (
            "Aun no tienes consultas. "
            "Preguntame por el precio de la papa o el clima y te respondo por voz."
        )

    # 2. Producto más consultado (top 1, solo consultas de precio con producto).
    producto_top_stmt = (
        select(Consultation.producto, func.count(Consultation.id).label("cnt"))
        .where(
            Consultation.phone_hash == phone_hash,
            Consultation.created_at >= fecha_corte,
            Consultation.producto.isnot(None),
            Consultation.producto != "",
        )
        .group_by(Consultation.producto)
        .order_by(func.count(Consultation.id).desc())
        .limit(1)
    )
    row = session.execute(producto_top_stmt).first()

    # Texto base: total de consultas.
    resumen = f"Este mes consultaste {total} veces"

    if row is None:
        # Tenía consultas pero sin producto (ej: solo clima).
        resumen += ". Sigue consultando precios y clima para tener mas datos."
        return resumen

    producto_top: str = row[0]
    resumen += f". Tu producto mas consultado fue {producto_top}"

    # 3. Variación de precio del producto top (reusa odepa_service).
    try:
        from app.services.odepa_service import (
            _select_registro_referencia,
            query_latest_by_product,
        )

        precios_por_mercado = query_latest_by_product(session, producto_top)
        if precios_por_mercado:
            actual = _select_registro_referencia(precios_por_mercado)
            fecha_limite = actual.fecha - timedelta(days=_RESUMEN_WINDOW_DAYS)

            # Buscar punto histórico: mismo mercado, misma unidad.
            from app.models.odepa_price import OdepaPrice

            q_hist = (
                select(OdepaPrice)
                .where(
                    func.lower(OdepaPrice.producto) == actual.producto.lower(),
                    OdepaPrice.mercado == actual.mercado,
                    OdepaPrice.unidad == actual.unidad,
                    OdepaPrice.fecha <= fecha_limite,
                )
                .order_by(OdepaPrice.fecha.desc())
                .limit(1)
            )
            antiguo = session.scalars(q_hist).first()

            if antiguo is not None:
                variacion_texto = _formatear_variacion_pct(
                    actual.precio_kg, antiguo.precio_kg
                )
                resumen += f". El precio {variacion_texto} este mes segun ODEPA"
            else:
                resumen += ". No tengo datos comparables de hace un mes para calcular la variacion"
        else:
            resumen += ". No tengo datos actuales de precio para calcular la variacion"
    except (ValueError, KeyError, TypeError):
        logger.warning(
            "Error calculando variacion de precio para %s en resumen",
            producto_top,
        )
        resumen += ". No pude calcular la variacion de precio"

    return resumen
