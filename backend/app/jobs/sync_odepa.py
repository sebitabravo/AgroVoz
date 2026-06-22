"""Cron job ODEPA: descarga CSV y carga precios en SQLite.

Entrada crontab (06:00 AM todos los días)::

    0 6 * * * cd /app && python -m app.jobs.sync_odepa >> /var/log/agrovoz-odepa.log 2>&1

Ejecución manual::

    make sync-odepa           # desde raíz del repo
    python -m app.jobs.sync_odepa   # desde backend/

La lógica vive en app.services.odepa_service; este módulo solo orquesta
para uso como entrypoint de cron. Exit code: 0 OK, 1 error (para que cron
reporte falla vía MAILTO o monitoreo).
"""

import asyncio
import logging
import sys

import sqlalchemy.exc

from app.services.odepa_service import OdepaSyncError, sync_odepa

logger = logging.getLogger(__name__)


async def _ejecutar() -> int:
    """Corre una sincronización. Devuelve exit code."""
    try:
        resultado = await sync_odepa()
    except OdepaSyncError as exc:
        logger.error("Sync ODEPA falló: %s", exc)
        return 1
    except (sqlalchemy.exc.SQLAlchemyError, OSError, ValueError) as exc:
        # Errores de BD/sistema inesperados: trace completo en log y exit 1.
        # No se usa except Exception por convención del proyecto.
        logger.exception("Sync ODEPA falló con error inesperado: %s", exc)
        return 1

    logger.info(
        "Sync ODEPA terminada: %d insertados, %d actualizados",
        resultado.insertados,
        resultado.actualizados,
    )
    return 0


def main() -> None:
    """Entrypoint CLI para crontab y ejecución manual."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    sys.exit(asyncio.run(_ejecutar()))


if __name__ == "__main__":
    main()
