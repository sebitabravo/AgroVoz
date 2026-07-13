"""Cron job de alertas climaticas: evalua helada/lluvia extrema.

Entrada crontab (18:00 todos los dias) para alertar de helada nocturna::

    0 18 * * * cd /app && python -m app.jobs.check_weather_alerts >> /var/log/agrovoz-clima.log 2>&1

Ejecucion manual::

    python -m app.jobs.check_weather_alerts   # desde backend/

El job evalua alertas climaticas activas contra el pronostico diario de
OpenMeteo y envia audios por WhatsApp cuando se cumplen los umbrales fijos.
"""

import asyncio
import logging
import sys

import sqlalchemy.exc

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.alert_service import evaluar_alertas_clima

logger = logging.getLogger(__name__)


async def _ejecutar() -> int:
    """Corre una evaluacion de alertas climaticas. Devuelve exit code."""
    session = SessionLocal()
    try:
        enviados = await evaluar_alertas_clima(session, settings)
        logger.info(
            "Evaluacion de alertas climaticas terminada: %d enviadas",
            len(enviados),
        )
        return 0
    except (sqlalchemy.exc.SQLAlchemyError, OSError, ValueError) as exc:
        logger.exception("Evaluacion de alertas climaticas fallo: %s", exc)
        return 1
    finally:
        session.close()


def main() -> None:
    """Entrypoint CLI para crontab y ejecucion manual."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    sys.exit(asyncio.run(_ejecutar()))


if __name__ == "__main__":
    main()
