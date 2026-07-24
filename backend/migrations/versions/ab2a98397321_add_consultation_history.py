"""add consultation_history

ID de revisión: ab2a98397321
Revisión anterior: 52257cb43405
Fecha: 2026-07-24 02:03:27.277016
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# Identificadores de revisión usados por Alembic.
revision: str = 'ab2a98397321'
down_revision: str | None = '52257cb43405'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Historial de consultas por agricultor (issue #195). Solo se escribe
    # con consentimiento explícito (ver consultation_history_service).
    # FK a user_prefs.phone_hash con ondelete CASCADE: borrar el perfil
    # del agricultor arrastra su historial (derecho al olvido, Ley 21.719).
    op.create_table(
        'consultation_history',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('phone_hash', sa.String(length=64), nullable=False),
        sa.Column('query_text', sa.Text(), nullable=False),
        sa.Column('response_text', sa.Text(), nullable=False),
        sa.Column('producto', sa.String(length=100), nullable=True),
        sa.Column('intent', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['phone_hash'], ['user_prefs.phone_hash'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('consultation_history', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_consultation_history_phone_hash'),
            ['phone_hash'],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table('consultation_history', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_consultation_history_phone_hash'))

    op.drop_table('consultation_history')
