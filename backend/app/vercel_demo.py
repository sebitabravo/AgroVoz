"""Entrypoint slim para el deploy público en Vercel (demo web).

Sin infraestructura propia (VPS/NAS) tras la baja del piloto Crea INACAP,
la demo pública vive en el free tier de Vercel Functions. Ese entorno no
tiene ffmpeg, no tiene los ~2,5 GB de modelos locales (Whisper/Qwen/Piper)
y su filesystem es de solo lectura salvo /tmp — por eso esta app NO es
`app.main:app`, es un subconjunto explícito.

Lo que monta:
- /api/v1/demo/preguntar y /api/v1/demo/status — el chat de la landing.
  Responde por fast-path determinista (precio ODEPA, clima OpenMeteo) para
  la mayoría de consultas y usa OpenRouter (LLM_PRIMARY_PROVIDER=openrouter)
  solo cuando el fast-path no aplica. Ver docs/ARCHITECTURE.md decisión 34.
- /api/v1/prices, /api/v1/weather, /api/v1/data — endpoints públicos de
  solo lectura que la demo y el landing consumen directamente.
- /api/v1/health — readiness slim (solo DB; sin chequeo de ffmpeg, que este
  modo nunca usa).

Lo que NO monta, a propósito: webhooks de WhatsApp, todo /admin, el panel
del agricultor, el consultor agrónomo, visión por computador y el router MCP.
Ninguno de esos flujos tiene sentido sin Open-WA, un dashboard con sesión o
modelos locales — montarlos solo agregaría superficie de ataque sin función.

La copia de la DB de solo-lectura a /tmp (única ruta escribible en Vercel)
ocurre ANTES de importar cualquier módulo que toque `app.core.database`,
para que el engine SQLAlchemy abra directamente sobre la ruta escribible.
"""

import logging
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

# La DB de solo lectura versionada por el repo (ver scripts/build_demo_db.py).
# Vercel no permite escribir en el bundle: se copia a /tmp en el primer import.
_BUNDLED_DEMO_DB = Path(__file__).resolve().parent / "data" / "demo.db"
_WRITABLE_DEMO_DB = Path("/tmp/agrovoz-demo.db")

logger = logging.getLogger(__name__)


def _prepare_writable_database() -> None:
    """Copia el snapshot público a /tmp si aún no existe en esta instancia.

    Las instancias de Vercel Functions pueden reciclarse entre invocaciones
    (warm start reusa /tmp; cold start no). Copiar es barato (< 5 MB) y
    idempotente: si el archivo ya está, no vuelve a copiar.
    """
    if _WRITABLE_DEMO_DB.exists():
        return
    if not _BUNDLED_DEMO_DB.exists():
        logger.warning(
            "demo.db no está en el bundle — la app arranca sin datos ODEPA/directorio."
        )
        return
    shutil.copyfile(_BUNDLED_DEMO_DB, _WRITABLE_DEMO_DB)


_prepare_writable_database()

import os  # noqa: E402 - debe fijarse antes de importar app.core.config

os.environ.setdefault("DATABASE_URL", f"sqlite:///{_WRITABLE_DEMO_DB}")

from app import __version__  # noqa: E402
from app.api.data_hub import router as data_hub_router  # noqa: E402
from app.api.demo import router as demo_router  # noqa: E402
from app.api.prices import router as prices_router  # noqa: E402
from app.api.weather import router as weather_router  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import engine  # noqa: E402
from app.core.security import ALLOWED_HOSTS, RateLimitMiddleware, SecurityHeadersMiddleware  # noqa: E402


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Versión mínima del middleware de trazabilidad de app.main.

    Se reimplementa acá (en vez de importarse) porque app.main construye su
    propio ContextVar y formatter de logging acoplados al resto del lifespan
    completo; para la demo alcanza con no romper si el header llega ausente.
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Request-ID", "demo")
        return response


def _check_db() -> bool:
    """Verifica que la DB de la demo responde. Sin chequeo de ffmpeg: no aplica aquí."""
    try:
        with engine.connect() as conn:
            return bool(conn.exec_driver_sql("SELECT 1").scalar())
    except (SQLAlchemyError, OSError) as exc:
        logger.error("Health check slim: DB no responde — error=%s", type(exc).__name__)
        return False


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Lifespan mínimo: sin preload de modelos, sin schedulers de cron.

    app.main.lifespan precarga Qwen (~6s), visión ONNX y levanta seis tareas
    de fondo (sync ODEPA, purgas TTL). Ninguna aplica acá: no hay modelo
    local que precargar y Vercel no sostiene tareas de fondo entre requests
    (cada invocación es efímera). El refresco de demo.db es un cron externo
    de GitHub Actions, no un scheduler in-process.
    """
    logger.info("AgroVoz demo slim iniciando — version=%s", __version__)
    yield


app = FastAPI(
    title="AgroVoz API (demo pública)",
    version=__version__,
    description="Subconjunto público de solo lectura para la demo web en Vercel.",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(prices_router, prefix="/api/v1")
app.include_router(weather_router, prefix="/api/v1")
app.include_router(data_hub_router, prefix="/api/v1")
app.include_router(demo_router, prefix="/api/v1")


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    """Readiness slim: solo DB. Sin ffmpeg — este modo nunca lo necesita."""
    if _check_db():
        return {"status": "ok", "database": "connected", "version": __version__}
    return {"status": "degraded", "database": "unavailable", "version": __version__}


@app.exception_handler(Exception)
async def global_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Nunca filtra detalles internos al cliente público."""
    logger.error("Error no manejado en demo slim — tipo=%s", type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "Error interno"})
