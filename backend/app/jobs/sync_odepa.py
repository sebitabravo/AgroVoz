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
import datetime
import logging
import sys
from pathlib import Path

import sqlalchemy.exc

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.alert_service import evaluar_alertas_precio
from app.services.odepa_service import OdepaSyncError, sync_odepa

logger = logging.getLogger(__name__)

# Alerta proactiva al equipo (#176): a partir de esta cantidad de fallos
# consecutivos del cron, se emite un log ERROR con marca reconocible para
# que quede visible en /var/log/agrovoz-odepa.log o cualquier monitoreo
# que grepee ese archivo.
_FALLOS_CONSECUTIVOS_PARA_ALERTA = 3

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_TS_FILE = _DATA_DIR / ".odepa_last_sync"
_FAIL_COUNT_FILE = _DATA_DIR / ".odepa_fail_count"


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
        _registrar_resultado_sync(exito=True)
        return 0
    except OdepaSyncError as exc:
        logger.error("Sync ODEPA falló: %s", exc)
        _registrar_resultado_sync(exito=False)
        return 1
    except (sqlalchemy.exc.SQLAlchemyError, OSError, ValueError) as exc:
        # No se usa except Exception por convención del proyecto.
        logger.exception("Sync ODEPA falló con error inesperado: %s", exc)
        _registrar_resultado_sync(exito=False)
        return 1


def _touch_sync_timestamp() -> None:
    """Escribe archivo de timestamp tras sync exitoso para monitoreo."""
    _TS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TS_FILE.write_text(datetime.datetime.now().isoformat())


def get_sync_stale_hours() -> float | None:
    """Retorna horas desde el último sync exitoso, o None si nunca sync."""
    if not _TS_FILE.exists():
        return None
    try:
        last = datetime.datetime.fromisoformat(_TS_FILE.read_text().strip())
        return (datetime.datetime.now() - last).total_seconds() / 3600
    except (ValueError, OSError):
        return None


def _leer_fallos_consecutivos() -> int:
    """Lee el contador de fallos consecutivos del cron ODEPA. 0 si no existe o es inválido."""
    if not _FAIL_COUNT_FILE.exists():
        return 0
    try:
        return int(_FAIL_COUNT_FILE.read_text().strip())
    except (ValueError, OSError):
        return 0


def _registrar_resultado_sync(*, exito: bool) -> int:
    """Actualiza el contador de fallos consecutivos y alerta al equipo si corresponde (#176).

    Éxito reinicia el contador a 0. Fallo lo incrementa; al llegar a
    ``_FALLOS_CONSECUTIVOS_PARA_ALERTA`` emite un log ERROR con marca
    reconocible (alerta proactiva "al equipo", nunca al agricultor).
    Retorna el contador resultante (útil para tests).
    """
    nuevo_conteo = 0 if exito else _leer_fallos_consecutivos() + 1
    try:
        _FAIL_COUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _FAIL_COUNT_FILE.write_text(str(nuevo_conteo))
    except OSError:
        logger.warning("No se pudo persistir el contador de fallos ODEPA")

    if nuevo_conteo >= _FALLOS_CONSECUTIVOS_PARA_ALERTA:
        logger.error(
            "ALERTA EQUIPO: sync ODEPA lleva %d fallos consecutivos. Revisar %s.",
            nuevo_conteo,
            settings.odepa_csv_url,
        )
    return nuevo_conteo


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
