"""add consultation history deletion audit

ID de revisión: f2a9c4e7d1b6
Revisión anterior: e4b7c2d91a63
Fecha: 2026-07-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Identificadores de revisión usados por Alembic.
revision: str = "f2a9c4e7d1b6"
down_revision: str | None = "e4b7c2d91a63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "consultation_history_deletion_audit"
NO_UPDATE_TRIGGER = "trg_history_deletion_audit_no_update"
NO_DELETE_TRIGGER = "trg_history_deletion_audit_no_delete"


def upgrade() -> None:
    """Crea la auditoría mínima de borrados y la protege contra mutaciones."""
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("subject_token", sa.String(length=64), nullable=True),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("records_deleted", sa.Integer(), nullable=False),
        sa.Column("requested_via", sa.String(length=24), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "length(event_id) = 36",
            name="ck_history_deletion_audit_event_id_length",
        ),
        sa.CheckConstraint(
            "subject_token IS NULL OR "
            "(length(subject_token) = 64 "
            "AND subject_token NOT GLOB '*[^0-9a-f]*')",
            name="ck_history_deletion_audit_subject_token_format",
        ),
        sa.CheckConstraint(
            "key_version >= 1",
            name="ck_history_deletion_audit_key_version",
        ),
        sa.CheckConstraint(
            "reason IN ('user_request', 'consent_revoked', 'ttl')",
            name="ck_history_deletion_audit_reason",
        ),
        sa.CheckConstraint(
            "records_deleted >= 0",
            name="ck_history_deletion_audit_records_deleted",
        ),
        sa.CheckConstraint(
            "requested_via IN "
            "('verified_whatsapp', 'admin_api', 'system_retention')",
            name="ck_history_deletion_audit_requested_via",
        ),
        sa.CheckConstraint(
            "((reason = 'ttl' AND subject_token IS NULL) OR "
            "(reason IN ('user_request', 'consent_revoked') "
            "AND subject_token IS NOT NULL))",
            name="ck_history_deletion_audit_subject_scope",
        ),
        sa.CheckConstraint(
            "((reason = 'ttl' AND cutoff_at IS NOT NULL) OR "
            "(reason IN ('user_request', 'consent_revoked') "
            "AND cutoff_at IS NULL))",
            name="ck_history_deletion_audit_cutoff_scope",
        ),
        sa.CheckConstraint(
            "((reason = 'ttl' AND requested_via = 'system_retention') OR "
            "(reason IN ('user_request', 'consent_revoked') "
            "AND requested_via IN ('verified_whatsapp', 'admin_api')))",
            name="ck_history_deletion_audit_request_source",
        ),
        sa.CheckConstraint(
            "outcome IN ('completed', 'no_records')",
            name="ck_history_deletion_audit_outcome",
        ),
        sa.CheckConstraint(
            "((records_deleted = 0 AND outcome = 'no_records') OR "
            "(records_deleted > 0 AND outcome = 'completed'))",
            name="ck_history_deletion_audit_outcome_count",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            name="uq_consultation_history_deletion_audit_event_id",
        ),
    )
    op.create_index(
        "ix_consultation_history_deletion_audit_deleted_at",
        TABLE_NAME,
        ["deleted_at"],
        unique=False,
    )

    # SQLite no ofrece roles por tabla. Estos triggers hacen append-only el
    # registro para todas las escrituras normales de la aplicación.
    op.execute(
        f"""
        CREATE TRIGGER {NO_UPDATE_TRIGGER}
        BEFORE UPDATE ON {TABLE_NAME}
        BEGIN
            SELECT RAISE(ABORT, 'consultation history audit is append-only');
        END
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {NO_DELETE_TRIGGER}
        BEFORE DELETE ON {TABLE_NAME}
        BEGIN
            SELECT RAISE(ABORT, 'consultation history audit is append-only');
        END
        """
    )


def downgrade() -> None:
    """Quita primero las protecciones y luego la tabla de auditoría."""
    op.execute(f"DROP TRIGGER IF EXISTS {NO_UPDATE_TRIGGER}")
    op.execute(f"DROP TRIGGER IF EXISTS {NO_DELETE_TRIGGER}")
    op.drop_index(
        "ix_consultation_history_deletion_audit_deleted_at",
        table_name=TABLE_NAME,
    )
    op.drop_table(TABLE_NAME)
