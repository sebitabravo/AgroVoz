"""add feedback and decision_productiva to consultations

ID de revisión: 6fffaca5a384
Revisión anterior: 673b9fe338cd
Fecha: 2026-07-12 17:04:01.502847
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# Identificadores de revisión usados por Alembic.
revision: str = '6fffaca5a384'
down_revision: str | None = '673b9fe338cd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Agrega campos para feedback del agricultor y marcado manual de
    # decision productiva. Nullable para no romper registros existentes.
    with op.batch_alter_table('consultations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('feedback', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('decision_productiva', sa.Boolean(), nullable=False, server_default=sa.text('0')))


def downgrade() -> None:
    with op.batch_alter_table('consultations', schema=None) as batch_op:
        batch_op.drop_column('decision_productiva')
        batch_op.drop_column('feedback')
