"""Modelo SQLAlchemy para historial de consultas (issue #195).

Guarda registro de consultas por agricultor SOLO si hay consentimiento
explícito (opt-in vía user_prefs.dataset_consent).

Cumplimiento Ley 21.719:
- Sin consentimiento → stateless (comportamiento actual).
- Borrado a pedido → irreversible, registrado en auditoría.
- Recordatorios proactivos → opt-in separado (reminders_opt_in).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ConsultationHistory(Base):
    """Historial de consultas por agricultor con consentimiento explícito."""

    __tablename__ = "consultation_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("user_prefs.phone_hash"), nullable=False, index=True
    )
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    producto: Mapped[str | None] = mapped_column(String(100), nullable=True)
    intent: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
