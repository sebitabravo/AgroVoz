"""Modelo SQLAlchemy para precios ODEPA.

Almacena precios mayoristas de productos agrícolas obtenidos desde
los datos abiertos de ODEPA (Oficina de Estudios y Políticas Agrarias).
Sync diario vía cron job a las 06:00 AM.
"""

import datetime

from sqlalchemy import Date, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OdepaPrice(Base):
    """Precio de un producto en un mercado específico para una fecha."""

    __tablename__ = "odepa_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    producto: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    mercado: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    precio_kg: Mapped[float] = mapped_column(Float, nullable=False)
    unidad: Mapped[str] = mapped_column(String(20), nullable=False, default="kg")
    fecha: Mapped[datetime.date] = mapped_column(Date, nullable=False, index=True)
    fuente: Mapped[str] = mapped_column(String(100), nullable=False, default="ODEPA")
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<OdepaPrice(producto='{self.producto}', mercado='{self.mercado}', "
            f"precio_kg={self.precio_kg}, fecha={self.fecha})>"
        )
