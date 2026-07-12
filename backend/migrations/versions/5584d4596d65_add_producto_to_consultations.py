"""add producto to consultations

Agrega campo producto (nullable, String(100)) a la tabla consultations.
Se usa para estadisticas del agricultor (comando "resumen").
Nullable porque no toda consulta tiene producto (clima, resumen, desconocido).

ID de revisión: 5584d4596d65
Revisión anterior: 673b9fe338cd
Fecha: 2026-07-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Identificadores de revisión usados por Alembic.
revision: str = '5584d4596d65'
down_revision: str | None = '673b9fe338cd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('consultations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('producto', sa.String(length=100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('consultations', schema=None) as batch_op:
        batch_op.drop_column('producto')
