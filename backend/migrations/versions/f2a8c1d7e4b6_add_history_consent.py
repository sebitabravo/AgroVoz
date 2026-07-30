"""Agrega consentimiento separado para historial de consultas.

Revision ID: f2a8c1d7e4b6
Revises: d9f3a6b2c7e1
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a8c1d7e4b6"
down_revision: str | None = "d9f3a6b2c7e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Agrega opt-in de historial con privacidad por defecto."""
    with op.batch_alter_table("user_prefs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "history_consent",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.create_check_constraint(
            "ck_user_prefs_history_consent_bool",
            "history_consent IN (0, 1)",
        )


def downgrade() -> None:
    """Retira el consentimiento de historial sin borrar preferencias."""
    with op.batch_alter_table("user_prefs") as batch_op:
        batch_op.drop_constraint(
            "ck_user_prefs_history_consent_bool",
            type_="check",
        )
        batch_op.drop_column("history_consent")
