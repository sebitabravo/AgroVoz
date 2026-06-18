"""Router de health check.

Endpoint de monitoreo para load balancers y health checks de Docker.
Distingue entre liveness (¿está vivo el proceso?) y readiness
(¿está listo para recibir tráfico? DB + modelos + ffmpeg).
"""

import logging
import shutil

from fastapi import APIRouter, Query, Response

from app.core.database import engine

router = APIRouter()
logger = logging.getLogger(__name__)


def _check_db() -> bool:
    """Verifica que la DB responde con un SELECT 1."""
    try:
        with engine.connect() as conn:
            result = conn.exec_driver_sql("SELECT 1")
            return result.scalar() == 1
    except Exception:
        logger.exception("Health check: DB no responde")
        return False


def _check_ffmpeg() -> bool:
    """Verifica que ffmpeg está instalado y accesible."""
    return shutil.which("ffmpeg") is not None


@router.get("/health")
def health(
    response: Response,
    probe: str = Query(default="readiness", pattern="^(liveness|readiness)$"),
) -> dict[str, str]:
    """Health check endpoint con distinción liveness vs readiness.

    - probe=liveness: chequeo rápido, solo verifica que el proceso responde.
      Usado por Docker HEALTHCHECK y balanceadores de carga.
    - probe=readiness: chequeo completo (DB + ffmpeg). Usado para saber si
      el servicio está listo para recibir tráfico después del startup.
    """
    if probe == "liveness":
        return {"status": "ok"}

    # Readiness: verificar dependencias
    db_ok = _check_db()
    ffmpeg_ok = _check_ffmpeg()

    if db_ok and ffmpeg_ok:
        return {"status": "ok", "database": "connected", "ffmpeg": "available"}

    # Al menos una dependencia falló — 503 Service Unavailable
    response.status_code = 503
    return {
        "status": "degraded",
        "database": "connected" if db_ok else "unavailable",
        "ffmpeg": "available" if ffmpeg_ok else "missing",
    }
