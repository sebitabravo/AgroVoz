"""add user_prefs.alert_consent

ID de revisión: b7c3e91a4d28
Revisión anterior: ab2a98397321
Fecha: 2026-07-26 03:15:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# Identificadores de revisión usados por Alembic.
revision: str = 'b7c3e91a4d28'
down_revision: str | None = 'ab2a98397321'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Consentimiento para alertas proactivas de precio y clima.
    # Separado de dataset_consent porque es otro tratamiento: una alerta la
    # inicia AgroVoz sin que el productor pregunte (comunicación no solicitada)
    # y necesita su propia base de licitud bajo la Ley 21.719.
    #
    # server_default='0' es obligatorio: sin él, las filas existentes quedan
    # con NULL y la columna es NOT NULL. Además el default correcto es NO
    # consentir — las alertas que ya se enviaron se hicieron sin opt-in
    # registrado, así que nadie queda con permiso retroactivo.
    with op.batch_alter_table('user_prefs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'alert_consent',
                sa.Boolean(),
                nullable=False,
                server_default='0',
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('user_prefs', schema=None) as batch_op:
        batch_op.drop_column('alert_consent')
