"""add alerts table

ID de revisión: 8fbcbe020460
Revisión anterior: 6fffaca5a384
Fecha: 2026-07-12 22:30:28.889278
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# Identificadores de revisión usados por Alembic.
revision: str = '8fbcbe020460'
down_revision: str | None = '6fffaca5a384'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Crea la tabla de alertas proactivas (issue #88).
    op.create_table('alerts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('phone_hash', sa.String(length=64), nullable=False),
    sa.Column('wa_chat_id', sa.String(length=50), nullable=True),
    sa.Column('tipo', sa.String(length=20), nullable=False),
    sa.Column('producto', sa.String(length=100), nullable=True),
    sa.Column('condicion', sa.String(length=2), nullable=True),
    sa.Column('umbral', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('umbral_clima', sa.String(length=20), nullable=True),
    sa.Column('activa', sa.Boolean(), nullable=False),
    sa.Column('last_triggered_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('alerts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_alerts_phone_hash'), ['phone_hash'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('alerts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_alerts_phone_hash'))

    op.drop_table('alerts')
