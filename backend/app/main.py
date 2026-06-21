"""Aplicación principal de FastAPI.

Punto de entrada del backend. Registra routers, middlewares y handlers.
"""

import contextvars
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from app import __version__
from app.api.health import router as health_router
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
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


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

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
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
        if not settings.openweathermap_api_key.strip():
            missing.append("OPENWEATHERMAP_API_KEY")
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

    yield

    logger.info("AgroVoz deteniendo — liberando conexiones")
    engine.dispose()


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
# RequestIDMiddleware debe ser el MÁS EXTERNO para setear el ContextVar
# antes de que RateLimitMiddleware, SecurityHeadersMiddleware y
# TrustedHostMiddleware procesen el request. Así los logs de rate limiting
# y security headers también tienen request_id.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.add_middleware(RequestIDMiddleware)

# Routers
app.include_router(health_router, prefix="/api/v1")


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
