"""Agrega gastos consentidos con vencimiento explícito.

Revision ID: a7c4e9b2d1f6
Revises: f2a8c1d7e4b6
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c4e9b2d1f6"
down_revision: str | None = "f2a8c1d7e4b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea el opt-in independiente y la persistencia acotada de #170."""
    with op.batch_alter_table("user_prefs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "expense_consent",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.create_check_constraint(
            "ck_user_prefs_expense_consent_bool",
            "expense_consent IN (0, 1)",
        )

    op.create_table(
        "expenses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("phone_hash", sa.String(length=64), nullable=False),
        sa.Column("producto", sa.String(length=100), nullable=False),
        sa.Column("concepto", sa.String(length=120), nullable=False),
        sa.Column("amount_clp", sa.BigInteger(), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "amount_clp > 0",
            name="ck_expenses_amount_positive",
        ),
        sa.CheckConstraint(
            "length(producto) BETWEEN 1 AND 100",
            name="ck_expenses_producto_length",
        ),
        sa.CheckConstraint(
            "length(concepto) BETWEEN 1 AND 120",
            name="ck_expenses_concepto_length",
        ),
        sa.ForeignKeyConstraint(
            ["phone_hash"],
            ["user_prefs.phone_hash"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_expenses_subject_product_date",
        "expenses",
        ["phone_hash", "producto", "occurred_on"],
        unique=False,
    )
    op.create_index(
        "ix_expenses_expires_at",
        "expenses",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Elimina gastos y su consentimiento sin tocar otras preferencias."""
    op.drop_index("ix_expenses_expires_at", table_name="expenses")
    op.drop_index("ix_expenses_subject_product_date", table_name="expenses")
    op.drop_table("expenses")

    with op.batch_alter_table("user_prefs") as batch_op:
        batch_op.drop_constraint(
            "ck_user_prefs_expense_consent_bool",
            type_="check",
        )
        batch_op.drop_column("expense_consent")
