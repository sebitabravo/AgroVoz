"""CLI one-shot para purgar parcelas vencidas según su ``expires_at`` (C5).

Complementa al scheduler del lifespan: permite forzar la retención desde cron
o durante una revisión operativa sin levantar la app. No depende del feature
gate, porque apagar las escrituras nuevas no suspende la retención de lo ya
registrado.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.services.parcela_service import (
    ParcelaOperationError,
    purge_expired_parcelas,
)

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    """Ejecuta una purga TTL; retorna cero en éxito/no-op y uno en fallo."""
    del argv  # El job no recibe parámetros: el corte siempre es "ahora".
    try:
        records_deleted = purge_expired_parcelas()
    except ParcelaOperationError:
        logger.error("El job de purga TTL de parcelas no pudo confirmar la operación")
        return 1

    logger.info(
        "Job de purga TTL de parcelas finalizado — registros=%d",
        records_deleted,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
