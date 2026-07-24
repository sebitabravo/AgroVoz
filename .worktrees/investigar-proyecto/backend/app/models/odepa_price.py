"""Modelo SQLAlchemy para precios ODEPA.

Almacena precios mayoristas de productos agrícolas obtenidos desde
los datos abiertos de ODEPA (Oficina de Estudios y Políticas Agrarias).
Sync diario vía cron job a las 06:00 AM.

Los datos ODEPA son append-only: cada ejecución del cron inserta filas
nuevas, nunca modifica existentes. updated_at refleja la fecha de inserción
inicial y no lleva onupdate por este motivo.
"""

import datetime
from decimal import Decimal

from sqlalchemy import Date, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OdepaPrice(Base):
    """Precio de un producto en un mercado específico para una fecha."""

    __tablename__ = "odepa_prices"

    __table_args__ = (
        UniqueConstraint(
            "producto",
            "mercado",
            "fecha",
            name="uq_odepa_producto_mercado_fecha",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    producto: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    mercado: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    # Numeric(10,2): precisión exacta para valores monetarios.
    # Float (IEEE 754) pierde precisión con fracciones decimales
    # (ej: 500.1 → 500.09999999999997). SQLite almacena NUMERIC como
    # afinidad ANY, pero SQLAlchemy devuelve Decimal. Al migrar a
    # PostgreSQL, Numeric(10,2) mapea a NUMERIC(10,2) nativo.
    # OJO: pese al nombre, guarda el precio en la unidad de venta que ODEPA
    # reporta para ese mercado (columna `unidad`), que no siempre es el kilo.
    # La conversión a kilo se hace al formatear (format_price_text), nunca acá.
    precio_kg: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    # ODEPA usa 130+ unidades de venta distintas ("$/saco 25 kilos",
    # "$/bandeja 12 canastillos 125 gramos", ...). String(100) cubre la más
    # larga observada (38 chars) con margen; SQLite no enforcea largo, pero
    # PostgreSQL sí lo hará al migrar.
    unidad: Mapped[str] = mapped_column(String(100), nullable=False, default="kg")
    fecha: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    # Constante "ODEPA": único origen de datos de precios para MVP.
    fuente: Mapped[str] = mapped_column(String(100), nullable=False, default="ODEPA")
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    # Sin onupdate: datos append-only. Cada cron inserta filas nuevas,
    # nunca hace UPDATE. updated_at = created_at en todas las filas.
    updated_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<OdepaPrice(producto='{self.producto}', mercado='{self.mercado}', "
            f"precio_kg={self.precio_kg}, fecha={self.fecha})>"
        )
