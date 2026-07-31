"""CLI one-shot para purgar gastos vencidos según su ``expires_at`` (#170).

Complementa al scheduler del lifespan: permite forzar la retención desde cron
o durante una revisión operativa sin levantar la app. No depende del feature
gate, porque apagar las escrituras nuevas no suspende la retención de lo ya
registrado.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.services.expense_service import (
    ExpenseOperationError,
    purge_expired_expenses,
)

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    """Ejecuta una purga TTL; retorna cero en éxito/no-op y uno en fallo."""
    del argv  # El job no recibe parámetros: el corte siempre es "ahora".
    try:
        records_deleted = purge_expired_expenses()
    except ExpenseOperationError:
        logger.error("El job de purga TTL de gastos no pudo confirmar la operación")
        return 1

    logger.info(
        "Job de purga TTL de gastos finalizado — registros=%d",
        records_deleted,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
