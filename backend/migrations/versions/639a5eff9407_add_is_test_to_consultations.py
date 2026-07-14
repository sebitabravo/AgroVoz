"""add is_test to consultations

Agrega la columna ``is_test`` a ``consultations`` para poder excluir filas
generadas por pytest/smoke tests de las métricas del dashboard admin (QA
piloto: 148+ filas de prueba sesgaban success_rate y percentiles de
latencia). Default False para no marcar retroactivamente nada como test.

ID de revisión: 639a5eff9407
Revisión anterior: 885fa6daf453
Fecha: 2026-07-13 20:21:11.172835
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# Identificadores de revisión usados por Alembic.
revision: str = '639a5eff9407'
down_revision: str | None = '885fa6daf453'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite requiere batch mode para ALTER TABLE.
    with op.batch_alter_table("consultations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_test",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.create_index("ix_consultations_is_test", ["is_test"])


def downgrade() -> None:
    with op.batch_alter_table("consultations", schema=None) as batch_op:
        batch_op.drop_index("ix_consultations_is_test")
        batch_op.drop_column("is_test")
