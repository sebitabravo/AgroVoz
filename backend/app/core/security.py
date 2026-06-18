"""Middleware de seguridad para FastAPI.

Encapsula rate limiting, headers de seguridad HTTP y constantes
de hardening que usa la capa HTTP en main.py.
"""

import time
from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.config import settings

# Hosts permitidos para TrustedHostMiddleware.
# El middleware se registra en main.py con esta lista.
ALLOWED_HOSTS: tuple[str, ...] = (
    "localhost",
    "127.0.0.1",
    "test",
    "testserver",
    "agrovoz.cl",
    "*.agrovoz.cl",
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware que inyecta headers de seguridad en todas las respuestas.

    Headers aplicados:
    - X-Content-Type-Options: previene MIME sniffing
    - X-Frame-Options: previene clickjacking
    - Referrer-Policy: controla cuánta info de origen se envía
    - Strict-Transport-Security: HSTS (solo en HTTPS)
    - Content-Security-Policy: política CSP restrictiva
    - Permissions-Policy: deshabilita micrófono, cámara, geolocalización
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # HSTS solo sobre HTTPS. Browsers ignoran HSTS sobre HTTP plano.
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["Permissions-Policy"] = (
            "microphone=(), camera=(), geolocation=()"
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting básico en memoria (MVP).

    Límite configurable vía settings.rate_limit_per_minute (default: 60/min por IP).
    Para producción, delegar a Traefik/Nginx o usar slowapi.
    """

    def __init__(self, app: "ASGIApp") -> None:
        super().__init__(app)
        self._requests: dict[str, list[float]] = {}

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        client_ip: str = request.client.host if request.client else "unknown"
        now = time.time()
        window = 60  # 1 minuto

        # Limpiar timestamps fuera de la ventana de 1 minuto
        self._requests.setdefault(client_ip, [])
        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if now - t < window
        ]

        # Poda entradas vacías para evitar memory leak de IPs inactivas
        if not self._requests[client_ip]:
            del self._requests[client_ip]

        # Rate-limit check: límite configurable vía settings.rate_limit_per_minute
        if len(self._requests.get(client_ip, [])) >= settings.rate_limit_per_minute:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Demasiadas solicitudes. Intenta de nuevo en un minuto."
                },
            )

        self._requests.setdefault(client_ip, []).append(now)
        return await call_next(request)
