"""Servicio de exportación de datos ODEPA a CSV (issue #90).

Contiene la lógica de:
- Construcción de queries con filtros por producto, mercado, fechas.
- Armado del CSV con sanitización contra formula injection.
- Codificación UTF-8 con BOM para Excel en español.
"""

import csv
import datetime
import io

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.odepa_price import OdepaPrice


def _sanitize_csv_value(value: str) -> str:
    """Escapa caracteres peligrosos al inicio de una celda CSV.

    Previene CSV formula injection: si un valor comienza con `=`, `+`, `-`,
    `@` o tab, Excel/LibreOffice lo interpreta como fórmula. Este helper
    prefija un apóstrofo (') que obliga el tratamiento como texto.

    Args:
        value: cadena a sanitizar.

    Returns:
        Cadena con apóstrofo inicial si es necesario.
    """
    if value and value[0] in ("=", "+", "-", "@", "\t"):
        return f"'{value}"
    return value


def get_prices_for_export(
    db: Session,
    producto: str | None = None,
    mercado: str | None = None,
    desde: datetime.date | None = None,
    hasta: datetime.date | None = None,
) -> list[OdepaPrice]:
    """Obtiene precios ODEPA con filtros, ordenados descendentemente por fecha.

    Argumentos:
        db: sesión de SQLAlchemy.
        producto: filtro exacto por nombre (ej: "papa"). None = sin filtro.
        mercado: filtro exacto por nombre (ej: "Lo Valledor"). None = sin filtro.
        desde: fecha inclusiva (YYYY-MM-DD). None = sin límite inferior.
        hasta: fecha inclusiva (YYYY-MM-DD). None = sin límite superior.

    Retorna:
        Lista de OdepaPrice ordenada por fecha DESC, luego producto, luego mercado.
    """
    stmt = select(OdepaPrice)
    if producto:
        stmt = stmt.where(OdepaPrice.producto == producto)
    if mercado:
        stmt = stmt.where(OdepaPrice.mercado == mercado)
    if desde is not None:
        stmt = stmt.where(OdepaPrice.fecha >= desde)
    if hasta is not None:
        stmt = stmt.where(OdepaPrice.fecha <= hasta)
    stmt = stmt.order_by(
        OdepaPrice.fecha.desc(),
        OdepaPrice.producto,
        OdepaPrice.mercado,
    )

    return list(db.scalars(stmt))


def build_prices_csv(rows: list[OdepaPrice]) -> bytes:
    """Construye CSV de precios ODEPA como bytes UTF-8 con BOM.

    Formato:
    - Headers: fecha, producto, mercado, precio_kg, unidad
    - Fecha: YYYY-MM-DD (sin hora)
    - precio_kg: número crudo sin formato chileno (ej: "950.00")
    - BOM UTF-8 al inicio para que Excel en español infiera el encoding
      (sin BOM, Excel infiere latin-1 y rompe tildes)
    - Sanitización de fórmulas: producto y mercado escapados contra injection

    Argumentos:
        rows: lista de OdepaPrice.

    Retorna:
        Bytes UTF-8 con BOM.
    """
    buffer = io.StringIO()
    buffer.write("﻿")  # BOM UTF-8 para Excel en español.
    writer = csv.writer(buffer)
    writer.writerow(["fecha", "producto", "mercado", "precio_kg", "unidad"])
    for r in rows:
        writer.writerow(
            [
                r.fecha.isoformat(),
                _sanitize_csv_value(r.producto),
                _sanitize_csv_value(r.mercado),
                str(r.precio_kg),
                r.unidad,
            ]
        )
    return buffer.getvalue().encode("utf-8")
