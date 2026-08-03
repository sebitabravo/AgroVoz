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
from app.agronomist_web import router as agronomist_web_router
from app.api.admin.agronomist_admin import router as admin_agronomist_router
from app.api.admin.metrics import router as admin_metrics_router
from app.api.admin.odepa_admin import router as admin_odepa_router
from app.api.admin.user_admin import router as admin_user_router
from app.api.agronomist import router as agronomist_router
from app.api.demo import router as demo_router
from app.api.health import router as health_router
from app.api.panel import router as panel_router
from app.api.prices import router as prices_router
from app.api.vision import router as vision_router
from app.api.weather import router as weather_router
from app.api.webhooks import router as webhooks_router
from app.core.config import settings
from app.core.database import engine
from app.core.security import (
    ALLOWED_HOSTS,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.panel_web import router as panel_web_router

logger = logging.getLogger(__name__)

_CONSULTATION_HISTORY_PURGE_INTERVAL_SECONDS = 24 * 60 * 60
_CONSULTATION_STAGING_CLEANUP_INTERVAL_SECONDS = 60 * 60
_EXPENSE_PURGE_INTERVAL_SECONDS = 24 * 60 * 60
_PARCELA_PURGE_INTERVAL_SECONDS = 24 * 60 * 60

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
        except Exception as exc:
            # Cron de fondo en loop infinito: cualquier excepcion NO capturada
            # mata el scheduler para siempre. except Exception es intencional aca
            # (boundary de resiliencia). CancelledError hereda de BaseException,
            # no se captura -> shutdown limpio via odepa_task.cancel() en lifespan.
            logger.error(
                "ODEPA scheduler: error en sync automática — error=%s",
                type(exc).__name__,
            )


async def _consultation_history_scheduler() -> None:
    """Purga diariamente el historial vencido sin bloquear el event loop."""
    from app.services.consultation_history_service import (
        HistoryOperationError,
        purge_expired_history,
    )

    while True:
        try:
            result = await asyncio.to_thread(purge_expired_history)
            logger.info(
                "Historial scheduler: purga OK — %d registros",
                result.records_deleted,
            )
        except HistoryOperationError:
            logger.error("Historial scheduler: purga auditada no confirmada")
        except Exception as exc:
            # Boundary de resiliencia: una falla inesperada no debe matar el
            # scheduler para siempre. CancelledError no se captura.
            logger.error(
                "Historial scheduler: error inesperado — error=%s",
                type(exc).__name__,
            )

        await asyncio.sleep(_CONSULTATION_HISTORY_PURGE_INTERVAL_SECONDS)


async def _consultation_staging_cleanup_scheduler() -> None:
    """Redacta cada hora contenido transitorio que superó las 24 horas."""
    from app.services.delivery_service import (
        ContentRedactionError,
        redact_stale_consultation_content,
    )

    while True:
        try:
            records_redacted = await asyncio.to_thread(redact_stale_consultation_content)
            logger.info(
                "Staging scheduler: limpieza OK — %d registros",
                records_redacted,
            )
        except ContentRedactionError:
            logger.error("Staging scheduler: limpieza no confirmada")
        except Exception as exc:
            logger.error(
                "Staging scheduler: error inesperado — error=%s",
                type(exc).__name__,
            )

        await asyncio.sleep(_CONSULTATION_STAGING_CLEANUP_INTERVAL_SECONDS)


async def _expense_purge_scheduler() -> None:
    """Purga diariamente los gastos vencidos según su ``expires_at`` (#170)."""
    from app.services.expense_service import (
        ExpenseOperationError,
        purge_expired_expenses,
    )

    while True:
        try:
            records_deleted = await asyncio.to_thread(purge_expired_expenses)
            logger.info(
                "Gastos scheduler: purga TTL OK — %d registros",
                records_deleted,
            )
        except ExpenseOperationError:
            logger.error("Gastos scheduler: purga TTL no confirmada")
        except Exception as exc:
            # Boundary de resiliencia: una falla inesperada no debe matar el
            # scheduler para siempre. CancelledError no se captura.
            logger.error(
                "Gastos scheduler: error inesperado — error=%s",
                type(exc).__name__,
            )

        await asyncio.sleep(_EXPENSE_PURGE_INTERVAL_SECONDS)


async def _parcela_purge_scheduler() -> None:
    """Purga diariamente las parcelas vencidas según su ``expires_at`` (C5)."""
    from app.services.parcela_service import (
        ParcelaOperationError,
        purge_expired_parcelas,
    )

    while True:
        try:
            records_deleted = await asyncio.to_thread(purge_expired_parcelas)
            logger.info(
                "Parcelas scheduler: purga TTL OK — %d registros",
                records_deleted,
            )
        except ParcelaOperationError:
            logger.error("Parcelas scheduler: purga TTL no confirmada")
        except Exception as exc:
            # Boundary de resiliencia: una falla inesperada no debe matar el
            # scheduler para siempre. CancelledError no se captura.
            logger.error(
                "Parcelas scheduler: error inesperado — error=%s",
                type(exc).__name__,
            )

        await asyncio.sleep(_PARCELA_PURGE_INTERVAL_SECONDS)


def _start_consultation_history_scheduler() -> asyncio.Task[None] | None:
    """Crea la tarea TTL solo cuando el feature gate está habilitado."""
    if not settings.consultation_history_enabled:
        return None
    return asyncio.create_task(
        _consultation_history_scheduler(),
        name="consultation-history-ttl",
    )


def _start_consultation_staging_cleanup_scheduler() -> asyncio.Task[None]:
    """Crea siempre la tarea de minimización de contenido transitorio."""
    return asyncio.create_task(
        _consultation_staging_cleanup_scheduler(),
        name="consultation-staging-cleanup",
    )


def _start_expense_purge_scheduler() -> asyncio.Task[None]:
    """Crea siempre la purga TTL de gastos, incluso con el gate apagado.

    Apagar ``expense_tracking_enabled`` detiene las escrituras nuevas, no la
    retención de lo ya registrado: los gastos previos deben seguir venciendo.
    """
    return asyncio.create_task(
        _expense_purge_scheduler(),
        name="expense-ttl",
    )


def _start_parcela_purge_scheduler() -> asyncio.Task[None]:
    """Crea siempre la purga TTL de parcelas, incluso con el gate apagado.

    Apagar ``parcela_tracking_enabled`` detiene las escrituras nuevas, no la
    retención de lo ya registrado: las parcelas previas deben seguir venciendo.
    """
    return asyncio.create_task(
        _parcela_purge_scheduler(),
        name="parcela-ttl",
    )


async def _cancel_background_task(task: asyncio.Task[None] | None) -> None:
    """Cancela una tarea y espera su cierre cooperativo."""
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Maneja el ciclo de vida de la aplicación.

    Al iniciar: configura logging, valida configuración crítica en producción.
    Al cerrar: libera conexiones del pool SQLite.
    """
    # Revalidar incluso si settings fue mutado después de importar el módulo.
    # Debe ocurrir antes de precargar modelos o crear tareas de fondo.
    settings.validate_mcp_security()
    settings.validate_consultation_history_security()

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

    # Precalentar visión solo cuando el gate está activo. Si el artefacto ONNX
    # no fue provisionado, el servicio queda degradado con respuesta honesta
    # pero no impide arrancar el backend ni los flujos de audio/texto.
    from app.services.vision_service import preload_model as preload_vision_model

    preload_vision_model()

    # Scheduler ODEPA: sync diario a las 06:00 AM hora local.
    # Tarea de fondo del lifespan. Se cancela automáticamente al detener la app.
    odepa_task = asyncio.create_task(_odepa_scheduler())
    history_purge_task = _start_consultation_history_scheduler()
    staging_cleanup_task = _start_consultation_staging_cleanup_scheduler()
    expense_purge_task = _start_expense_purge_scheduler()
    parcela_purge_task = _start_parcela_purge_scheduler()

    yield

    logger.info("AgroVoz deteniendo — liberando conexiones")
    await _cancel_background_task(parcela_purge_task)
    await _cancel_background_task(expense_purge_task)
    await _cancel_background_task(staging_cleanup_task)
    await _cancel_background_task(history_purge_task)
    await _cancel_background_task(odepa_task)
    engine.dispose()
    from app.services.weather_service import _close_http_client

    await _close_http_client()
    from app.services.openrouter_service import close_http_client as _close_openrouter_client

    await _close_openrouter_client()


def _mount_mcp_router(application: FastAPI) -> None:
    """Monta la RPC administrativa MCP solo cuando su gate está habilitado."""
    if not settings.mcp_enabled:
        return
    if getattr(application.state, "_agrovoz_mcp_router_mounted", False):
        return

    settings.validate_mcp_security()
    from app.mcp.router import router as mcp_router

    application.include_router(mcp_router)
    application.state._agrovoz_mcp_router_mounted = True


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
    # allow_credentials queda en False (default): el landing consulta endpoints
    # publicos de forma anonima y el admin es same-origin (nunca cruza CORS).
    # Combinar allow_credentials=True con allow_origins=["*"] (dev) viola la
    # RFC 6454: el browser rechaza la respuesta y, peor, expondria la cookie de
    # sesion admin a cualquier origen. No usamos credenciales cross-origin.
    allow_credentials=False,
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
app.include_router(vision_router, prefix="/api/v1")
app.include_router(webhooks_router, prefix="/api/v1")
app.include_router(demo_router, prefix="/api/v1")
app.include_router(panel_router, prefix="/api/v1")
app.include_router(panel_web_router)  # prefix "/panel" va en el router
app.include_router(agronomist_router, prefix="/api/v1")
app.include_router(agronomist_web_router)  # prefix "/agronomo" va en el router
# Admin — APIs JSON (autenticadas con X-Admin-Key) + dashboard HTML (cookie).
app.include_router(admin_metrics_router, prefix="/api/v1")
app.include_router(admin_odepa_router, prefix="/api/v1")
app.include_router(admin_user_router, prefix="/api/v1")
app.include_router(admin_agronomist_router, prefix="/api/v1")
app.include_router(admin_html_router)  # prefix "/admin" va en el router
_mount_mcp_router(app)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Manejador global de excepciones.

    En desarrollo: expone el mensaje real para debug.
    En producción: mensaje genérico para no leakear información interna.
    """
    logger.error(
        "Error no manejado — method=%s path=%s error=%s",
        request.method,
        request.url.path,
        type(exc).__name__,
    )
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
