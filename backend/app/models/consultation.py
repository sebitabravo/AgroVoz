"""Modelo SQLAlchemy para consultas de agricultores.

Registra cada interacción del pipeline de voz para métricas anonimizadas:
qué preguntó el agricultor, qué respondió el sistema, latencia y duración.
Sin PII — el número de teléfono se guarda como hash SHA-256.
"""

import datetime

from sqlalchemy import Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Consultation(Base):
    """Registro anonimizado de una consulta procesada por el pipeline."""

    __tablename__ = "consultations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    intent: Mapped[str] = mapped_column(String(20), nullable=False, default="desconocido")
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    audio_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<Consultation(phone_hash='{self.phone_hash[:8]}...', "
            f"intent='{self.intent}', latency_ms={self.latency_ms})>"
        )
