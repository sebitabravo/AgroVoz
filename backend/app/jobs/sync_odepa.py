"""Cron job ODEPA: descarga CSV, carga precios, evalúa alertas.

Entrada crontab (06:00 AM todos los días)::

    0 6 * * * cd /app && python -m app.jobs.sync_odepa >> /var/log/agrovoz-odepa.log 2>&1

Ejecución manual::

    make sync-odepa           # desde raíz del repo
    python -m app.jobs.sync_odepa   # desde backend/

Orquestación:
1. Descarga y carga precios ODEPA (app.services.odepa_service.sync_odepa)
2. Evalúa alertas de precio configuradas por voz (app.services.alert_service.evaluar_alertas_precio)

Exit code: 0 OK, 1 error (para que cron reporte falla vía MAILTO o monitoreo).
"""

import asyncio
import logging
import sys

import sqlalchemy.exc

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.alert_service import evaluar_alertas_precio
from app.services.odepa_service import OdepaSyncError, sync_odepa

logger = logging.getLogger(__name__)


async def _ejecutar() -> int:
    """Sincroniza ODEPA y evalúa alertas de precio. Devuelve exit code."""
    try:
        # Etapa 1: sincronizar precios.
        resultado = await sync_odepa()
        logger.info(
            "Sync ODEPA terminada: %d insertados, %d actualizados",
            resultado.insertados,
            resultado.actualizados,
        )

        # Etapa 2: evaluar alertas de precio tras sincronización exitosa.
        session = SessionLocal()
        try:
            enviados = await evaluar_alertas_precio(session, settings)
            if enviados:
                logger.info("Alertas de precio disparadas: %d productores notificados", len(enviados))
        except Exception:
            logger.exception("Error evaluando alertas de precio (no interrumpe sync)")
        finally:
            session.close()

        _touch_sync_timestamp()
        return 0
    except OdepaSyncError as exc:
        logger.error("Sync ODEPA falló: %s", exc)
        return 1
    except (sqlalchemy.exc.SQLAlchemyError, OSError, ValueError) as exc:
        logger.exception("Sync ODEPA falló con error inesperado: %s", exc)
        # No se usa except Exception por convención del proyecto.
        logger.exception("Sync ODEPA falló con error inesperado: %s", exc)
        return 1


def _touch_sync_timestamp() -> None:
    """Escribe archivo de timestamp tras sync exitoso para monitoreo."""
    import datetime
    from pathlib import Path
    ts_file = Path(__file__).resolve().parent.parent / "data" / ".odepa_last_sync"
    ts_file.parent.mkdir(parents=True, exist_ok=True)
    ts_file.write_text(datetime.datetime.now().isoformat())


def get_sync_stale_hours() -> float | None:
    """Retorna horas desde el último sync exitoso, o None si nunca sync."""
    import datetime
    from pathlib import Path
    ts_file = Path(__file__).resolve().parent.parent / "data" / ".odepa_last_sync"
    if not ts_file.exists():
        return None
    try:
        last = datetime.datetime.fromisoformat(ts_file.read_text().strip())
        return (datetime.datetime.now() - last).total_seconds() / 3600
    except (ValueError, OSError):
        return None


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
