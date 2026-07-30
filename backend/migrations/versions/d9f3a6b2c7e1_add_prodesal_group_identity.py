"""Agrega identidad operativa de grupo PRODESAL.

Revision ID: d9f3a6b2c7e1
Revises: c8e1f4a7b2d5
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9f3a6b2c7e1"
down_revision: str | None = "c8e1f4a7b2d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Agrega identidad y ubicación sin alterar el hash identificador."""
    with op.batch_alter_table("user_prefs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "identity_type",
                sa.String(length=20),
                nullable=False,
                server_default="individual",
            )
        )
        batch_op.add_column(sa.Column("group_label", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("localidad", sa.String(length=120), nullable=True))
        batch_op.create_check_constraint(
            "ck_user_prefs_identity_type",
            "identity_type IN ('individual', 'prodesal_group')",
        )
        batch_op.create_check_constraint(
            "ck_user_prefs_group_identity",
            """
            (identity_type = 'individual' AND group_label IS NULL)
            OR
            (
                identity_type = 'prodesal_group'
                AND group_label IS NOT NULL
                AND length(group_label) BETWEEN 1 AND 100
                AND group_label = trim(
                    group_label,
                    char(9) || char(10) || char(11) || char(12) || char(13) || ' '
                )
                AND length(group_label) >= 1
            )
            """,
        )
        batch_op.create_check_constraint(
            "ck_user_prefs_localidad_length",
            """
            localidad IS NULL
            OR (
                length(localidad) BETWEEN 1 AND 120
                AND localidad = trim(
                    localidad,
                    char(9) || char(10) || char(11) || char(12) || char(13) || ' '
                )
                AND length(localidad) >= 1
            )
            """,
        )


def downgrade() -> None:
    """Retira los metadatos grupales conservando las filas de preferencias."""
    with op.batch_alter_table("user_prefs") as batch_op:
        batch_op.drop_constraint(
            "ck_user_prefs_localidad_length",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_user_prefs_group_identity",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_user_prefs_identity_type",
            type_="check",
        )
        batch_op.drop_column("localidad")
        batch_op.drop_column("group_label")
        batch_op.drop_column("identity_type")
