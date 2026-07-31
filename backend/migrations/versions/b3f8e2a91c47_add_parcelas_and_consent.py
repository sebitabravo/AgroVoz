"""Agrega parcelas consentidas con vencimiento explícito.

Revision ID: b3f8e2a91c47
Revises: a7c4e9b2d1f6
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3f8e2a91c47"
down_revision: str | None = "a7c4e9b2d1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea el opt-in independiente y la persistencia acotada de las parcelas (C5)."""
    with op.batch_alter_table("user_prefs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "parcela_consent",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.create_check_constraint(
            "ck_user_prefs_parcela_consent_bool",
            "parcela_consent IN (0, 1)",
        )

    op.create_table(
        "parcelas",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("phone_hash", sa.String(length=64), nullable=False),
        sa.Column("cultivo", sa.String(length=100), nullable=False),
        sa.Column("superficie_ha", sa.Numeric(10, 2), nullable=False),
        sa.Column("comuna", sa.String(length=100), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "superficie_ha > 0",
            name="ck_parcelas_superficie_positiva",
        ),
        sa.CheckConstraint(
            "length(cultivo) BETWEEN 1 AND 100",
            name="ck_parcelas_cultivo_length",
        ),
        sa.CheckConstraint(
            "length(comuna) BETWEEN 1 AND 100",
            name="ck_parcelas_comuna_length",
        ),
        sa.ForeignKeyConstraint(
            ["phone_hash"],
            ["user_prefs.phone_hash"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_parcelas_subject_cultivo",
        "parcelas",
        ["phone_hash", "cultivo"],
        unique=False,
    )
    op.create_index(
        "ix_parcelas_expires_at",
        "parcelas",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Elimina parcelas y su consentimiento sin tocar otras preferencias."""
    op.drop_index("ix_parcelas_expires_at", table_name="parcelas")
    op.drop_index("ix_parcelas_subject_cultivo", table_name="parcelas")
    op.drop_table("parcelas")

    with op.batch_alter_table("user_prefs") as batch_op:
        batch_op.drop_constraint(
            "ck_user_prefs_parcela_consent_bool",
            type_="check",
        )
        batch_op.drop_column("parcela_consent")
