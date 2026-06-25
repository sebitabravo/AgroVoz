"""add_consultation_stage_timing

Agrega columnas whisper_ms, llm_ms, tts_ms a consultations para persistir
el desglose de latencia por etapa del pipeline. Necesario para las metricas
del dashboard admin (seccion desglose por etapa).

ID de revisión: 8f2a4c7e1d90
Revisión anterior: 37086656cc4e
Fecha: 2026-06-24
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Identificadores de revisión usados por Alembic.
revision: str = '8f2a4c7e1d90'
down_revision: str | None = '37086656cc4e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default='0' para que SQLite permita ADD COLUMN NOT NULL sobre
    # registros existentes (SQLite no soporta ADD NOT NULL sin default).
    with op.batch_alter_table('consultations', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('whisper_ms', sa.Integer(), nullable=False, server_default='0')
        )
        batch_op.add_column(
            sa.Column('llm_ms', sa.Integer(), nullable=False, server_default='0')
        )
        batch_op.add_column(
            sa.Column('tts_ms', sa.Integer(), nullable=False, server_default='0')
        )


def downgrade() -> None:
    with op.batch_alter_table('consultations', schema=None) as batch_op:
        batch_op.drop_column('tts_ms')
        batch_op.drop_column('llm_ms')
        batch_op.drop_column('whisper_ms')
