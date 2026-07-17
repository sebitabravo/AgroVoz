"""add cultivos to user_prefs

Agrega la columna ``cultivos`` a ``user_prefs`` para almacenar los
cultivos de interes del productor como JSON en TEXT (issue #125).
Nullable: los cultivos se capturan durante el onboarding o via admin
despues del primer contacto. No afecta filas existentes.

ID de revisión: 52257cb43405
Revisión anterior: 639a5eff9407
Fecha: 2026-07-16 23:13:49.816440
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# Identificadores de revisión usados por Alembic.
revision: str = '52257cb43405'
down_revision: str | None = '639a5eff9407'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite requiere batch mode para ALTER TABLE.
    # La columna es nullable=True: filas existentes quedan con NULL.
    # No hay server_default porque el JSON no tiene un valor por defecto
    # significativo (el onboarding captura cultivos explicitamente).
    with op.batch_alter_table("user_prefs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("cultivos", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("user_prefs", schema=None) as batch_op:
        batch_op.drop_column("cultivos")
