"""add dataset_consent to user_prefs

Agrega la columna ``dataset_consent`` a ``user_prefs`` para el dataset de
voz rural chilena (issue #96). Es opt-in explicito: default ``False`` para
filas existentes y nuevas. Solo se retiene audio cuando el productor firmó
el Acuerdo de Uso y Consentimiento.

ID de revisión: 885fa6daf453
Revisión anterior: 8fbcbe020460
Fecha: 2026-07-12 22:02:34.320182
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# Identificadores de revisión usados por Alembic.
revision: str = '885fa6daf453'
down_revision: str | None = '8fbcbe020460'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite requiere batch mode para ALTER TABLE. El default 0 asegura que
    # filas existentes queden con consentimiento=False (privacidad por defecto).
    with op.batch_alter_table("user_prefs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "dataset_consent",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("user_prefs", schema=None) as batch_op:
        batch_op.drop_column("dataset_consent")
