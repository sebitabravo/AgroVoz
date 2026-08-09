"""Agrega ubicación GPS opcional a las preferencias del productor.

Revision ID: c5d9e7f1a2b3
Revises: b3f8e2a91c47
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5d9e7f1a2b3"
down_revision: str | None = "b3f8e2a91c47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Agrega coordenadas opcionales y conserva las preferencias existentes."""
    with op.batch_alter_table("user_prefs") as batch_op:
        batch_op.add_column(sa.Column("lat", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("lng", sa.Float(), nullable=True))
        batch_op.create_check_constraint(
            "ck_user_prefs_location_pair",
            "(lat IS NULL AND lng IS NULL) OR (lat IS NOT NULL AND lng IS NOT NULL)",
        )
        batch_op.create_check_constraint(
            "ck_user_prefs_lat_range",
            "lat IS NULL OR lat BETWEEN -90.0 AND 90.0",
        )
        batch_op.create_check_constraint(
            "ck_user_prefs_lng_range",
            "lng IS NULL OR lng BETWEEN -180.0 AND 180.0",
        )


def downgrade() -> None:
    """Retira las coordenadas sin eliminar la fila de preferencias."""
    with op.batch_alter_table("user_prefs") as batch_op:
        batch_op.drop_constraint("ck_user_prefs_lng_range", type_="check")
        batch_op.drop_constraint("ck_user_prefs_lat_range", type_="check")
        batch_op.drop_constraint("ck_user_prefs_location_pair", type_="check")
        batch_op.drop_column("lng")
        batch_op.drop_column("lat")
