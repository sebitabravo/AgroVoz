"""add history source consultation

ID de revisión: c8e1f4a7b2d5
Revisión anterior: f2a9c4e7d1b6
Fecha: 2026-07-30 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Identificadores de revisión usados por Alembic.
revision: str = "c8e1f4a7b2d5"
down_revision: str | None = "f2a9c4e7d1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_NAME = "fk_consultation_history_source_consultation_id"
UQ_NAME = "uq_consultation_history_source_consultation_id"


def upgrade() -> None:
    """Vincula opcionalmente cada historial con una consulta entregada."""
    with op.batch_alter_table("consultation_history", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_consultation_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            FK_NAME,
            "consultations",
            ["source_consultation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint(
            UQ_NAME,
            ["source_consultation_id"],
        )


def downgrade() -> None:
    """Elimina el vínculo sin alterar las consultas ni el historial."""
    with op.batch_alter_table("consultation_history", schema=None) as batch_op:
        batch_op.drop_constraint(UQ_NAME, type_="unique")
        batch_op.drop_constraint(FK_NAME, type_="foreignkey")
        batch_op.drop_column("source_consultation_id")
