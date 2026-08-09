"""Modelo SQLAlchemy para el directorio de servicios agrícolas.

El directorio contiene únicamente datos públicos de contacto publicados por
organismos oficiales. No guarda datos de agricultores ni depende de APIs
externas durante una consulta.
"""

import datetime

from sqlalchemy import CheckConstraint, Date, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DirectorioAgricola(Base):
    """Sede de INDAP, PRODESAL o cooperativa agrícola por comuna."""

    __tablename__ = "directorio_agricola"
    __table_args__ = (
        CheckConstraint(
            "tipo IN ('indap', 'prodesal', 'cooperativa')",
            name="ck_directorio_agricola_tipo",
        ),
        CheckConstraint(
            "length(comuna) BETWEEN 1 AND 100",
            name="ck_directorio_agricola_comuna_length",
        ),
        CheckConstraint(
            "length(nombre) BETWEEN 1 AND 200",
            name="ck_directorio_agricola_nombre_length",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comuna: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    nombre: Mapped[str] = mapped_column(String(200), nullable=False)
    direccion: Mapped[str | None] = mapped_column(String(300), nullable=True)
    telefono: Mapped[str | None] = mapped_column(String(80), nullable=True)
    horario: Mapped[str | None] = mapped_column(String(300), nullable=True)
    fuente: Mapped[str] = mapped_column(String(150), nullable=False)
    fuente_url: Mapped[str] = mapped_column(Text, nullable=False)
    # Se conserva el formato textual porque algunos datasets solo informan
    # año o mes, y forzar un día inventaría precisión que la fuente no tiene.
    fecha_fuente: Mapped[str | None] = mapped_column(String(20), nullable=True)
    verificado_el: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())

    def __repr__(self) -> str:
        """Representa una sede sin incluir información sensible."""
        return (
            f"<DirectorioAgricola(tipo='{self.tipo}', comuna='{self.comuna}', "
            f"nombre='{self.nombre}')>"
        )
