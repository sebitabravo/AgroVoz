"""Middleware de seguridad para FastAPI.

Encapsula rate limiting, headers de seguridad HTTP, validación HMAC
de webhooks de Open-WA y constantes de hardening.
"""

import hashlib
import hmac as hmac_mod
import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.config import settings

logger = logging.getLogger(__name__)

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

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # HSTS solo sobre HTTPS. Browsers ignoran HSTS sobre HTTP plano.
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["Permissions-Policy"] = "microphone=(), camera=(), geolocation=()"
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting básico en memoria (MVP).

    Límite configurable vía settings.rate_limit_per_minute (default: 60/min por IP).
    Para producción, delegar a Traefik/Nginx o usar slowapi.
    """

    def __init__(self, app: "ASGIApp") -> None:
        super().__init__(app)
        self._requests: dict[str, list[float]] = {}
        self._last_cleanup: float = 0.0  # Timestamp de la última limpieza global

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        client_ip: str = request.client.host if request.client else "unknown"
        now = time.time()
        window = 60  # 1 minuto

        # Barrido global de IPs inactivas cada 60s para evitar memory leak.
        # Sin esto, IPs que hacen 1 request y no vuelven acumulan entradas
        # con timestamps expirados que nunca se limpian (scanners, bots).
        if now - self._last_cleanup >= 60:
            dead_ips = [
                ip for ip, timestamps in self._requests.items() if not [t for t in timestamps if now - t < window]
            ]
            for ip in dead_ips:
                del self._requests[ip]
            self._last_cleanup = now

        # Limpiar timestamps fuera de la ventana de 1 minuto para la IP actual
        self._requests.setdefault(client_ip, [])
        self._requests[client_ip] = [t for t in self._requests[client_ip] if now - t < window]

        # Poda la IP actual si quedó vacía después de limpiar
        if not self._requests[client_ip]:
            del self._requests[client_ip]

        # Rate-limit check: límite configurable vía settings.rate_limit_per_minute
        if len(self._requests.get(client_ip, [])) >= settings.rate_limit_per_minute:
            return JSONResponse(
                status_code=429,
                content={"detail": "Demasiadas solicitudes. Intenta de nuevo en un minuto."},
            )

        self._requests.setdefault(client_ip, []).append(now)
        return await call_next(request)


# ── Validación HMAC de webhooks de Open-WA ──────────────────────


def validate_openwa_hmac(body: bytes, signature: str, secret: str) -> bool:
    """Valida la firma HMAC-SHA256 de un webhook de Open-WA.

    Open-WA envía el header X-OpenWA-Signature con el HMAC-SHA256
    del body del request, calculado con WEBHOOK_SECRET como clave.

    Usa hmac.compare_digest() para prevenir timing attacks.

    Args:
        body: Cuerpo crudo del request HTTP (bytes).
        signature: Valor del header X-OpenWA-Signature.
        secret: Clave secreta compartida (WEBHOOK_SECRET).

    Returns:
        True si la firma es válida o si no hay secret configurado (dev).
        False si la firma no coincide.
    """
    if not secret:
        logger.warning(
            "OPENWA_WEBHOOK_SECRET no configurado — webhooks aceptados sin validación. Esto es inseguro en producción."
        )
        return True

    expected = hmac_mod.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac_mod.compare_digest(expected, signature.lower())


async def verify_openwa_webhook(request: Request) -> dict[str, object]:
    """Dependencia de FastAPI: valida HMAC del webhook y retorna el payload parseado.

    Lee el body crudo del request, valida la firma HMAC contra
    OPENWA_WEBHOOK_SECRET, y parsea el JSON.

    Si OPENWA_WEBHOOK_SECRET no está configurado (vacío), se aceptan
    webhooks sin validación HMAC. Esto es seguro solo en desarrollo local.

    Returns:
        Payload JSON del webhook como diccionario.

    Raises:
        HTTPException 401: Si la firma HMAC está ausente o es inválida.
        HTTPException 400: Si el body no es JSON válido.
    """
    body = await request.body()

    # Dev mode: sin secret configurado, aceptar sin validación HMAC.
    if not settings.openwa_webhook_secret:
        logger.warning("OPENWA_WEBHOOK_SECRET no configurado — webhooks aceptados sin validación HMAC")
    else:
        signature = request.headers.get("X-OpenWA-Signature", "")
        if not signature:
            logger.warning("Webhook rechazado: header X-OpenWA-Signature ausente")
            raise HTTPException(status_code=401, detail="Firma HMAC requerida")

        if not validate_openwa_hmac(body, signature, settings.openwa_webhook_secret):
            logger.warning("Webhook rechazado: firma HMAC inválida")
            raise HTTPException(status_code=401, detail="Firma HMAC inválida")

    # El body ya fue leído. Devolvemos el dict parseado para que el endpoint lo use.
    import json

    try:
        payload: dict[str, object] = json.loads(body)
    except json.JSONDecodeError as err:
        logger.warning("Webhook rechazado: body no es JSON válido")
        raise HTTPException(status_code=400, detail="Body debe ser JSON válido") from err
    return payload
