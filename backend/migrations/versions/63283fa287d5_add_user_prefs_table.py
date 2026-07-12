"""add user_prefs table

Crea la tabla user_prefs para el onboarding por voz (#86).

Almacena la comuna del productor asociada a su phone_hash (HMAC-SHA256).
La comuna se registra presencialmente via admin durante el piloto de
Traiguén. Una fila por phone_hash (unique index).

ID de revisión: 63283fa287d5
Revisión anterior: 673b9fe338cd
Fecha: 2026-07-11
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# Identificadores de revisión usados por Alembic.
revision: str = '63283fa287d5'
down_revision: str | None = '673b9fe338cd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'user_prefs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        # HMAC-SHA256 hex digest (64 chars). Unique: una fila por productor.
        sa.Column('phone_hash', sa.String(length=64), nullable=False),
        # Comuna del productor. Nullable: se setea via admin post-onboarding.
        sa.Column('comuna', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('user_prefs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_prefs_phone_hash'), ['phone_hash'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('user_prefs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_prefs_phone_hash'))

    op.drop_table('user_prefs')
