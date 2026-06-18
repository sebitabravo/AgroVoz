"""Aplicación principal de FastAPI.

Punto de entrada del backend. Registra routers, middlewares y handlers.
"""

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

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


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Inyecta X-Request-ID en cada request para trazabilidad en logs.

    Si el request ya tiene un header X-Request-ID, lo reusa.
    Si no, genera un UUID7-like (basado en tiempo para orden en logs).
    El header se agrega a la respuesta para trazabilidad end-to-end.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        """Procesa request inyectando X-Request-ID."""
        import re
        import time

        request_id = request.headers.get("X-Request-ID")
        # Sanitizar: solo alfanumérico + guiones, max 64 chars.
        # Previene log injection vía headers maliciosos.
        if request_id and not re.match(r"^[a-zA-Z0-9\-]{1,64}$", request_id):
            request_id = None
        if not request_id:
            request_id = f"{int(time.time() * 1000):x}-{uuid.uuid4().hex[:8]}"

        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Maneja el ciclo de vida de la aplicación.

    Al iniciar: configura logging, valida configuración crítica en producción.
    Al cerrar: libera conexiones del pool SQLite.
    """
    # Configurar logging según entorno
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        if settings.app_env == "production"
        else "%(asctime)s [%(levelname)s] %(name)s %(filename)s:%(lineno)d: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Validar configuración crítica en producción
    if settings.app_env == "production" and not settings.openweathermap_api_key:
        raise ValueError(
            "OPENWEATHERMAP_API_KEY es requerida en producción. "
            "Defínela en el entorno o .env."
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
# RequestIDMiddleware va primero (más externo) para que todos los middlewares
# internos tengan acceso a request_id en los logs.
# SecurityHeadersMiddleware envuelve a RateLimitMiddleware para que las
# respuestas 429 también reciban headers de seguridad.
app.add_middleware(RequestIDMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

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
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc)},
        )
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno del servidor."},
    )
