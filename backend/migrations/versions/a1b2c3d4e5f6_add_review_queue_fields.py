"""add_review_queue_fields

Agrega campos de cola de revisión humana a consultations:
- requires_review: flag automático cuando el pipeline produce fallback
- revisado_por: quién revisó (admin)
- nota_revision: comentario del revisor
- resuelto: si ya fue revisado

ID de revisión: a1b2c3d4e5f6
Revisión anterior: 8f2a4c7e1d90
Fecha: 2026-07-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Identificadores de revisión usados por Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: str | None = '673b9fe338cd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('consultations', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('requires_review', sa.Boolean(), nullable=False, server_default='0')
        )
        batch_op.add_column(
            sa.Column('revisado_por', sa.String(length=100), nullable=True)
        )
        batch_op.add_column(
            sa.Column('nota_revision', sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column('resuelto', sa.Boolean(), nullable=False, server_default='0')
        )

    # Índice para filtrar rápido las pendientes de revisión.
    op.create_index(
        'ix_consultations_requires_review',
        'consultations',
        ['requires_review'],
    )


def downgrade() -> None:
    op.drop_index('ix_consultations_requires_review', table_name='consultations')
    with op.batch_alter_table('consultations', schema=None) as batch_op:
        batch_op.drop_column('resuelto')
        batch_op.drop_column('nota_revision')
        batch_op.drop_column('revisado_por')
        batch_op.drop_column('requires_review')
