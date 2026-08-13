"""Sincronización one-shot del Data Hub local y sus verificadores oficiales.

Uso manual o desde cron dentro del contenedor::

    python -m app.jobs.sync_data_hub

El job no recibe URLs ni secretos por argumentos. Las fuentes remotas están
declaradas como adaptadores fijos en ``app.services.data_hub_remote``.
"""

from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal
from app.services.data_hub_remote import sync_remote_data_hub
from app.services.data_hub_service import DataHubValidationError, sync_data_hub

logger = logging.getLogger(__name__)


def main() -> int:
    """Ejecuta sync local y remoto; retorna 1 si alguna fuente remota falla."""
    db = SessionLocal()
    try:
        local_result = sync_data_hub(db)
        remote_result = sync_remote_data_hub(db)
    except (DataHubValidationError, OSError, SQLAlchemyError, ValueError) as exc:
        logger.error("Sync Data Hub falló — error=%s", type(exc).__name__)
        return 1
    finally:
        db.close()

    logger.info(
        "Sync Data Hub OK: fuentes=%d hechos=%d remotas_ok=%d remotas_error=%d",
        local_result.sources_synced,
        local_result.facts_synced,
        remote_result.sources_synced,
        len(remote_result.source_errors),
    )
    return 1 if remote_result.source_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
