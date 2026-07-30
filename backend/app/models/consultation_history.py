"""Modelo SQLAlchemy para historial de consultas (issue #195).

Guarda registro de consultas por agricultor SOLO si hay consentimiento
explícito (opt-in vía user_prefs.dataset_consent).

Cumplimiento Ley 21.719:
- Sin consentimiento → stateless (comportamiento actual).
- Borrado a pedido → irreversible, registrado en auditoría.
- Retención: TTL configurable con purga automática y auditoría agregada.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ConsultationHistory(Base):
    """Historial de consultas por agricultor con consentimiento explícito."""

    __tablename__ = "consultation_history"
    __table_args__ = (
        UniqueConstraint(
            "source_consultation_id",
            name="uq_consultation_history_source_consultation_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_consultation_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "consultations.id",
            name="fk_consultation_history_source_consultation_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    phone_hash: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("user_prefs.phone_hash", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    producto: Mapped[str | None] = mapped_column(String(100), nullable=True)
    intent: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class ConsultationHistoryDeletionAudit(Base):
    """Evidencia append-only de borrados sin contenido ni identificadores directos."""

    __tablename__ = "consultation_history_deletion_audit"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            name="uq_consultation_history_deletion_audit_event_id",
        ),
        CheckConstraint(
            "length(event_id) = 36",
            name="ck_history_deletion_audit_event_id_length",
        ),
        CheckConstraint(
            "subject_token IS NULL OR (length(subject_token) = 64 AND subject_token NOT GLOB '*[^0-9a-f]*')",
            name="ck_history_deletion_audit_subject_token_format",
        ),
        CheckConstraint(
            "key_version >= 1",
            name="ck_history_deletion_audit_key_version",
        ),
        CheckConstraint(
            "reason IN ('user_request', 'consent_revoked', 'ttl')",
            name="ck_history_deletion_audit_reason",
        ),
        CheckConstraint(
            "records_deleted >= 0",
            name="ck_history_deletion_audit_records_deleted",
        ),
        CheckConstraint(
            "requested_via IN ('verified_whatsapp', 'admin_api', 'system_retention')",
            name="ck_history_deletion_audit_requested_via",
        ),
        CheckConstraint(
            "((reason = 'ttl' AND subject_token IS NULL) OR "
            "(reason IN ('user_request', 'consent_revoked') "
            "AND subject_token IS NOT NULL))",
            name="ck_history_deletion_audit_subject_scope",
        ),
        CheckConstraint(
            "((reason = 'ttl' AND cutoff_at IS NOT NULL) OR "
            "(reason IN ('user_request', 'consent_revoked') "
            "AND cutoff_at IS NULL))",
            name="ck_history_deletion_audit_cutoff_scope",
        ),
        CheckConstraint(
            "((reason = 'ttl' AND requested_via = 'system_retention') OR "
            "(reason IN ('user_request', 'consent_revoked') "
            "AND requested_via IN ('verified_whatsapp', 'admin_api')))",
            name="ck_history_deletion_audit_request_source",
        ),
        CheckConstraint(
            "outcome IN ('completed', 'no_records')",
            name="ck_history_deletion_audit_outcome",
        ),
        CheckConstraint(
            "((records_deleted = 0 AND outcome = 'no_records') OR (records_deleted > 0 AND outcome = 'completed'))",
            name="ck_history_deletion_audit_outcome_count",
        ),
        Index(
            "ix_consultation_history_deletion_audit_deleted_at",
            "deleted_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    subject_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    records_deleted: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_via: Mapped[str] = mapped_column(String(24), nullable=False)
    cutoff_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
