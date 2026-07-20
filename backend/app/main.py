"""Aplicación principal de FastAPI.

Punto de entrada del backend. Registra routers, middlewares y handlers.
"""

import asyncio
import contextvars
import datetime
import logging
import pathlib
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from app import __version__
from app.admin.admin import router as admin_html_router
from app.admin.auth import AdminAuthMiddleware
from app.api.admin.metrics import router as admin_metrics_router
from app.api.admin.odepa_admin import router as admin_odepa_router
from app.api.admin.user_admin import router as admin_user_router
from app.api.demo import router as demo_router
from app.api.health import router as health_router
from app.api.prices import router as prices_router
from app.api.weather import router as weather_router
from app.api.webhooks import router as webhooks_router
from app.core.config import settings
from app.core.database import engine
from app.core.security import (
    ALLOWED_HOSTS,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)

logger = logging.getLogger(__name__)

# ContextVar para propagar el request_id a los logs.
# El middleware lo setea por request; el logging.Filter lo inyecta en cada LogRecord.
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIDFormatter(logging.Formatter):
    """Formatter que inyecta request_id en cada LogRecord al formatear.

    Más robusto que un Filter porque no depende del orden de ejecución
    de los filtros del logger ni de que basicConfig sea un no-op.
    """

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_ctx.get()
        return super().format(record)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Inyecta X-Request-ID en cada request para trazabilidad en logs.

    Si el request ya tiene un header X-Request-ID, lo reusa.
    Si no, genera un UUID7-like (basado en tiempo para orden en logs).
    El header se agrega a la respuesta para trazabilidad end-to-end.
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """Procesa request inyectando X-Request-ID."""
        request_id = request.headers.get("X-Request-ID")
        # Sanitizar: solo alfanumérico + guiones, max 64 chars.
        # Previene log injection vía headers maliciosos.
        if request_id and not re.match(r"^[a-zA-Z0-9\-]{1,64}$", request_id):
            request_id = None
        if not request_id:
            request_id = f"{int(time.time() * 1000):x}-{uuid.uuid4().hex[:8]}"

        request.state.request_id = request_id
        request_id_ctx.set(request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


async def _odepa_scheduler() -> None:
    """Ejecuta sync ODEPA todos los dias a las 06:00 AM (hora local del VPS).

    Corre en loop infinito como tarea de fondo del lifespan de FastAPI.
    Calcula el proximo target 06:00, espera, ejecuta sync, repite cada 24h.
    No depende de crontab externo: funciona en dev y prod sin config extra.
    """
    from app.services.odepa_service import sync_odepa

    while True:
        now = datetime.datetime.now()
        target = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now >= target:
            target += datetime.timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        logger.info(
            "ODEPA scheduler: proxima sync en %.1f horas (%s)",
            wait_seconds / 3600,
            target.isoformat(),
        )
        await asyncio.sleep(wait_seconds)
        try:
            resultado = await sync_odepa()
            logger.info(
                "ODEPA scheduler: sync OK — %d insertados, %d actualizados",
                resultado.insertados,
                resultado.actualizados,
            )
        except Exception:
            # Cron de fondo en loop infinito: cualquier excepcion NO capturada
            # mata el scheduler para siempre. except Exception es intencional aca
            # (boundary de resiliencia). CancelledError hereda de BaseException,
            # no se captura -> shutdown limpio via odepa_task.cancel() en lifespan.
            logger.exception("ODEPA scheduler: error en sync automatica")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Maneja el ciclo de vida de la aplicación.

    Al iniciar: configura logging, valida configuración crítica en producción.
    Al cerrar: libera conexiones del pool SQLite.
    """
    # Configurar logging según entorno.
    # RequestIDFormatter inyecta request_id en cada línea de log desde el ContextVar.
    log_format = (
        "%(asctime)s [%(levelname)s] [%(request_id)s] %(name)s: %(message)s"
        if settings.app_env == "production"
        else "%(asctime)s [%(levelname)s] [%(request_id)s] %(name)s %(filename)s:%(lineno)d: %(message)s"
    )
    handler = logging.StreamHandler()
    handler.setFormatter(RequestIDFormatter(log_format, datefmt="%Y-%m-%dT%H:%M:%S"))
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        handlers=[handler],
        force=True,
    )

    # Validar configuración crítica en producción.
    # Falla rápido si secrets requeridos no están configurados, en vez de
    # arrancar silenciosamente y fallar en runtime con errores oscuros.
    if settings.app_env == "production":
        missing = []
        # OpenWeatherMap ya no es necesario — migrado a OpenMeteo (issue #51).
        if not settings.openwa_api_key.strip():
            missing.append("OPENWA_API_KEY")
        if not settings.openwa_webhook_secret.strip():
            missing.append("OPENWA_WEBHOOK_SECRET")
        if missing:
            raise ValueError(
                f"Secrets requeridos no configurados: {', '.join(missing)}. "
                "Defínelos en Dokploy Secrets UI o en el entorno de producción."
            )

    logger.info(
        "AgroVoz iniciando — app_env=%s debug=%s",
        settings.app_env,
        settings.debug,
    )

    # Pre-cargar modelo LLM en background (~6s en VPS CX43).
    # Evita cold start timeout en el primer request al pipeline.
    from app.services.llm_service import preload_model
    preload_model()

    # Scheduler ODEPA: sync diario a las 06:00 AM hora local.
    # Tarea de fondo del lifespan. Se cancela automáticamente al detener la app.
    odepa_task = asyncio.create_task(_odepa_scheduler())

    yield

    logger.info("AgroVoz deteniendo — liberando conexiones")
    odepa_task.cancel()
    with suppress(asyncio.CancelledError):
        await odepa_task
    engine.dispose()
    from app.services.weather_service import _close_http_client
    await _close_http_client()


app = FastAPI(
    title="AgroVoz API",
    version=__version__,
    description="Asistente de Voz para la Agricultura Familiar Campesina",
    docs_url="/docs" if settings.app_env == "development" else None,
    redoc_url=None,
    lifespan=lifespan,
)

# Middlewares — el orden importa: el último agregado es el más externo.
# Starlette usa insert(0, ...) en add_middleware, así que el middleware
# agregado primero termina siendo el más interno (innermost) y el último
# el más externo (outermost).
#
# Orden de procesamiento del request (outermost → innermost):
#   CORSMiddleware → RequestID → SecurityHeaders → TrustedHost → RateLimit → AdminAuth → GZip → app
#
# - CORSMiddleware es el MÁS EXTERNO (agregado último): debe responder OPTIONS
#   preflight antes que cualquier otro middleware, especialmente TrustedHost
#   que rechazaria el preflight por Origin no permitido.
#   En desarrollo permite "*", en producción solo origenes configurados.
# - RequestIDMiddleware setea el ContextVar antes que otros middlewares,
# - SecurityHeadersMiddleware envuelve todo: agrega headers de seguridad
#   incluso en respuestas de error de TrustedHost (P2-3).
# - TrustedHostMiddleware rechaza hosts no permitidos antes de llegar
#   al rate limiter y la app.
# - RateLimitMiddleware: solo cuenta requests que pasan
#   todas las validaciones previas (hosts, firma HMAC).
# - AdminAuthMiddleware protege /admin/* (excepto login) con cookie firmada.
#   RequestID, SecurityHeaders, TrustedHost y RateLimit lo envuelven, así que
#   cubren incluso los redirects de auth.
# - GZipMiddleware es el MÁS INTERNO: se agrega primero (insert(0)), recibe la
#   respuesta cruda de la app y la comprime antes que la envuelvan los
#   middlewares externos. Se ubica adentro para recibir el body sin la división
#   de streaming que genera BaseHTTPMiddleware (Starlette 1.3.1). Solo comprime
#   responses >= minimum_size (500 bytes) con content-type compresible. OJO:
#   Starlette solo excluye text/event-stream, así que audio/ogg TAMBIÉN se
#   comprime; es inofensivo para el MVP porque el audio se responde vía Open-WA,
#   no como response HTTP directa.
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(AdminAuthMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Static files — JS bundles locales (HTMX, Chart.js) + favicon.
# Montado antes que los routers para que las rutas estáticas tengan prioridad.
# Path absoluto (igual que _TEMPLATES_DIR): no depende del CWD desde donde
# se lanza uvicorn, lo que importa en Docker/systemd con WORKDIR distinto.
_STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# Routers
app.include_router(health_router, prefix="/api/v1")
app.include_router(prices_router, prefix="/api/v1")
app.include_router(weather_router, prefix="/api/v1")
app.include_router(webhooks_router, prefix="/api/v1")
app.include_router(demo_router, prefix="/api/v1")
# Admin — APIs JSON (autenticadas con X-Admin-Key) + dashboard HTML (cookie).
app.include_router(admin_metrics_router, prefix="/api/v1")
app.include_router(admin_odepa_router, prefix="/api/v1")
app.include_router(admin_user_router, prefix="/api/v1")
app.include_router(admin_html_router)  # prefix "/admin" va en el router


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Manejador global de excepciones.

    En desarrollo: expone el mensaje real para debug.
    En producción: mensaje genérico para no leakear información interna.
    """
    logger.exception("Error no manejado en %s %s", request.method, request.url.path)
    if settings.app_env == "development":
        response = JSONResponse(
            status_code=500,
            content={"detail": str(exc)},
        )
    else:
        response = JSONResponse(
            status_code=500,
            content={"detail": "Error interno del servidor."},
        )
    # X-Request-ID en respuestas 500: si call_next levanta excepción,
    # el middleware no alcanza a setear el header. Lo seteamos acá
    # para que el cliente pueda correlacionar errores con logs.
    response.headers["X-Request-ID"] = request_id_ctx.get()
    return response
