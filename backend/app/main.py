"""Aplicación principal de FastAPI.

Punto de entrada del backend. Registra routers, middlewares y handlers.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Maneja el ciclo de vida de la aplicación.

    Al iniciar: valida configuración crítica en producción.
    Al cerrar: libera conexiones del pool SQLite.
    """
    if settings.app_env == "production" and not settings.openweathermap_api_key:
        raise ValueError(
            "OPENWEATHERMAP_API_KEY es requerida en producción. "
            "Defínela en el entorno o .env."
        )

    yield

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
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

# Routers
app.include_router(health_router, prefix="/api/v1")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Manejador global de excepciones.

    En desarrollo: expone el mensaje real para debug.
    En producción: mensaje genérico para no leakear información interna.
    """
    if settings.app_env == "development":
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc)},
        )
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno del servidor."},
    )
