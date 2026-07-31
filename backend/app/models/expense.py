"""Modelo SQLAlchemy para gastos consentidos del agricultor (#170)."""

import datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Expense(Base):
    """Gasto mínimo, seudonimizado y sujeto a vencimiento automático.

    ``phone_hash`` referencia la preferencia consentida del agricultor; nunca
    se persiste el número de WhatsApp. ``expires_at`` se fija al registrar para
    que cada fila tenga un límite de retención explícito e independiente de
    cambios posteriores de configuración.
    """

    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("amount_clp > 0", name="ck_expenses_amount_positive"),
        CheckConstraint(
            "length(producto) BETWEEN 1 AND 100",
            name="ck_expenses_producto_length",
        ),
        CheckConstraint(
            "length(concepto) BETWEEN 1 AND 120",
            name="ck_expenses_concepto_length",
        ),
        Index(
            "ix_expenses_subject_product_date",
            "phone_hash",
            "producto",
            "occurred_on",
        ),
        Index("ix_expenses_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone_hash: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("user_prefs.phone_hash", ondelete="CASCADE"),
        nullable=False,
    )
    producto: Mapped[str] = mapped_column(String(100), nullable=False)
    concepto: Mapped[str] = mapped_column(String(120), nullable=False)
    amount_clp: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_on: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        """Representa metadatos operativos sin monto, concepto ni identidad."""
        return (
            f"<Expense(id={self.id}, occurred_on={self.occurred_on!r}, "
            f"expires_at={self.expires_at!r})>"
        )
