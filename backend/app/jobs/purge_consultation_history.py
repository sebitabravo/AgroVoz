"""CLI one-shot para purgar historial vencido según el TTL configurado."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from typing import cast

from app.services.consultation_history_service import (
    HistoryOperationError,
    purge_expired_history,
)

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    """Construye los argumentos del job sin dependencias externas."""
    parser = argparse.ArgumentParser(
        description="Purga historial de consultas vencido y audita el resultado."
    )
    parser.add_argument(
        "--event-id",
        default=None,
        help="UUID opcional para reintentar la misma ejecución.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Ejecuta una purga; retorna cero en éxito/no-op y uno en fallo."""
    args = _build_parser().parse_args(argv)
    event_id = cast(str | None, args.event_id)
    try:
        result = purge_expired_history(event_id=event_id)
    except HistoryOperationError:
        logger.error("El job de purga TTL no pudo confirmar la operación")
        return 1

    if result.outcome == "disabled":
        logger.info("Job de purga TTL omitido — feature gate desactivado")
        return 0

    logger.info(
        "Job de purga TTL finalizado — registros=%d",
        result.records_deleted,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
