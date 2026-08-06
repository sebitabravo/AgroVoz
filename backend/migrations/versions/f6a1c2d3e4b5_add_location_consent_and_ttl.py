"""Agrega consentimiento y timestamp de retención para ubicación GPS.

Revision ID: f6a1c2d3e4b5
Revises: c5d9e7f1a2b3
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a1c2d3e4b5"
down_revision: str | None = "c5d9e7f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Agrega el opt-in independiente y el origen del TTL del pin GPS."""
    with op.batch_alter_table("user_prefs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "location_consent",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("location_updated_at", sa.DateTime(), nullable=True))
        batch_op.create_check_constraint(
            "ck_user_prefs_location_consent_bool",
            "location_consent IN (0, 1)",
        )


def downgrade() -> None:
    """Retira el consentimiento y el timestamp sin tocar lat/lng."""
    with op.batch_alter_table("user_prefs") as batch_op:
        batch_op.drop_constraint("ck_user_prefs_location_consent_bool", type_="check")
        batch_op.drop_column("location_updated_at")
        batch_op.drop_column("location_consent")
