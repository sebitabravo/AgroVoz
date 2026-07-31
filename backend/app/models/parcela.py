"""Modelo SQLAlchemy para parcelas del agricultor, con consentimiento y TTL (C5)."""

import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Parcela(Base):
    """Parcela declarada por el agricultor: cultivo, superficie y comuna.

    ``phone_hash`` referencia la preferencia consentida del agricultor; nunca
    se persiste el número de WhatsApp. ``expires_at`` se fija al registrar
    para que cada fila tenga un límite de retención explícito e independiente
    de cambios posteriores de configuración. Alimenta el motor de reglas
    agronómicas (cultivo) y el clima por parcela (comuna).
    """

    __tablename__ = "parcelas"
    __table_args__ = (
        CheckConstraint("superficie_ha > 0", name="ck_parcelas_superficie_positiva"),
        CheckConstraint(
            "length(cultivo) BETWEEN 1 AND 100",
            name="ck_parcelas_cultivo_length",
        ),
        CheckConstraint(
            "length(comuna) BETWEEN 1 AND 100",
            name="ck_parcelas_comuna_length",
        ),
        Index(
            "ix_parcelas_subject_cultivo",
            "phone_hash",
            "cultivo",
        ),
        Index("ix_parcelas_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone_hash: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("user_prefs.phone_hash", ondelete="CASCADE"),
        nullable=False,
    )
    cultivo: Mapped[str] = mapped_column(String(100), nullable=False)
    superficie_ha: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    comuna: Mapped[str] = mapped_column(String(100), nullable=False)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        """Representa metadatos operativos sin cultivo, superficie ni identidad."""
        return f"<Parcela(id={self.id}, expires_at={self.expires_at!r})>"
