"""add consultation delivery status

ID de revisión: e4b7c2d91a63
Revisión anterior: b7c3e91a4d28
Fecha: 2026-07-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Identificadores de revisión usados por Alembic.
revision: str = "e4b7c2d91a63"
down_revision: str | None = "b7c3e91a4d28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Añade evidencia persistente del resultado de entrega al agricultor."""
    # Las consultas históricas quedan ``pending`` porque no existe evidencia
    # confiable para marcarlas retroactivamente como entregadas o fallidas.
    with op.batch_alter_table("consultations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "delivery_status",
                sa.String(length=20),
                nullable=False,
                server_default="pending",
            )
        )
        batch_op.add_column(
            sa.Column(
                "delivered_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "delivery_error_code",
                sa.String(length=100),
                nullable=True,
            )
        )
        batch_op.create_check_constraint(
            "ck_consultations_delivery_status",
            "delivery_status IN ('pending', 'delivered', 'failed')",
        )


def downgrade() -> None:
    """Elimina los campos y la restricción del estado de entrega."""
    with op.batch_alter_table("consultations", schema=None) as batch_op:
        batch_op.drop_constraint(
            "ck_consultations_delivery_status",
            type_="check",
        )
        batch_op.drop_column("delivery_error_code")
        batch_op.drop_column("delivered_at")
        batch_op.drop_column("delivery_status")
