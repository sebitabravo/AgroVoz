"""Middleware de seguridad para FastAPI.

Encapsula rate limiting, headers de seguridad HTTP, validación HMAC
de webhooks de Open-WA y constantes de hardening.
"""

import hashlib
import hmac as hmac_mod
import json
import logging
import threading
import time
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.config import settings

logger = logging.getLogger(__name__)

# Tamaño máximo de body para webhooks (10 MB). Previene DoS por RAM bombing.
MAX_WEBHOOK_BODY_SIZE = 10 * 1024 * 1024

# Hosts permitidos para TrustedHostMiddleware.
# El middleware se registra en main.py con esta lista.
# Hosts extra (como la IP de Docker para desarrollo local) se cargan desde
# settings.extra_allowed_hosts para que no queden hardcodeados en produccion.
_base_hosts: list[str] = [
    "localhost",
    "127.0.0.1",
    "testserver",  # Requerido por Starlette TestClient en tests
    "agrovoz.cl",
    ".agrovoz.cl",  # Leading dot: Starlette TrustedHostMiddleware espera ".domain.com", no "*.domain.com"
]
_base_hosts.extend(h.strip() for h in settings.extra_allowed_hosts.split(",") if h.strip())
ALLOWED_HOSTS: tuple[str, ...] = tuple(_base_hosts)


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
        # HSTS solo sobre HTTPS. Detrás de Traefik/Dokploy, la conexión al
        # backend es HTTP plano — usamos X-Forwarded-Proto que Traefik setea
        # con el protocolo real que usó el cliente (https o http).
        scheme = request.headers.get("X-Forwarded-Proto", request.url.scheme)
        if scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["Permissions-Policy"] = "microphone=(), camera=(), geolocation=()"
        return response


def _get_client_ip(request: Request) -> str:
    """Extrae la IP real del cliente, considerando proxy reverso (Traefik/Nginx).

    En Docker detrás de Traefik, request.client NO es None — es la IP del
    contenedor Traefik (ej: 172.18.0.2). Por eso, SIEMPRE revisamos
    X-Forwarded-For primero. Usa el ÚLTIMO valor (agregado por el proxy edge),
    no el primero (que el cliente puede spoofear).

    En local sin proxy (request.client es la IP real y no hay X-Forwarded-For),
    usa request.client.host como fallback.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    parts = [p.strip() for p in forwarded.split(",") if p.strip()]
    if parts:
        # El último valor es el que agrega nuestro proxy de confianza (Traefik).
        # Los valores anteriores pueden ser spoofeados por el cliente.
        return parts[-1]
    if request.client is not None:
        return request.client.host
    return "unknown"


# Referencia a la instancia activa de RateLimitMiddleware.
# Se setea en __init__ para que los tests puedan resetear el contador
# entre ejecuciones: el middleware vive en el singleton `app` y su estado
# `_requests` persiste entre tests si no se limpia, saturando el contador
# global cuando la suite completa acumula más de rate_limit_per_minute.
_active_rate_limiter: "RateLimitMiddleware | None" = None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting básico en memoria (MVP).

    Límite configurable vía settings.rate_limit_per_minute (default: 60/min por IP).
    Protegido con threading.Lock() para requests concurrentes (sync endpoints usan
    ThreadPoolExecutor, lo que expone self._requests a múltiples hilos).
    Para producción, delegar a Traefik/Nginx o usar slowapi.
    """

    def __init__(self, app: "ASGIApp") -> None:
        super().__init__(app)
        self._requests: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._last_cleanup: float = 0.0  # Timestamp de la última limpieza global
        # Registrar instancia para que los tests puedan resetear el estado.
        global _active_rate_limiter
        _active_rate_limiter = self

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        client_ip: str = _get_client_ip(request)
        now = time.time()
        window = 60  # 1 minuto

        with self._lock:
            # Barrido global de IPs inactivas cada 60s para evitar memory leak.
            if now - self._last_cleanup >= 60:
                dead_ips = [
                    ip for ip, timestamps in self._requests.items()
                    if not any(now - t < window for t in timestamps)
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

            # Rate-limit check
            if len(self._requests.get(client_ip, [])) >= settings.rate_limit_per_minute:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Demasiadas solicitudes. Intenta de nuevo en un minuto."},
                )

            self._requests.setdefault(client_ip, []).append(now)

        return await call_next(request)


def reset_rate_limiter_for_tests() -> None:
    """Limpia los contadores del RateLimitMiddleware activo.

    Pensado para tests: el middleware es un singleton dentro de `app` y su
    estado `_requests` persiste entre tests, lo que satura el contador global
    cuando la suite completa acumula requests. Igual patrón que
    `monitor_service.reset_uptime_for_tests`.
    """
    if _active_rate_limiter is not None:
        with _active_rate_limiter._lock:
            _active_rate_limiter._requests.clear()
            _active_rate_limiter._last_cleanup = 0.0


# ── Validación HMAC de webhooks de Open-WA ──────────────────────


def validate_openwa_hmac(body: bytes, signature: str, secret: str) -> bool:
    """Valida la firma HMAC-SHA256 de un webhook de Open-WA.

    Open-WA envía el header X-OpenWA-Signature con el HMAC-SHA256
    del body del request, calculado con WEBHOOK_SECRET como clave.
    El formato del header es "sha256=<hex_digest>".

    Usa hmac.compare_digest() para prevenir timing attacks.

    Args:
        body: Cuerpo crudo del request HTTP (bytes).
        signature: Valor del header X-OpenWA-Signature (formato: "sha256=...").
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

    # OpenWA firma con formato "sha256=<hex>". Extraemos solo el hex.
    # Case-insensitive: puede llegar como sha256= o SHA256=.
    signature_lower = signature.lower()
    if signature_lower.startswith("sha256="):
        signature = signature_lower[len("sha256=") :]

    expected = hmac_mod.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac_mod.compare_digest(expected, signature)


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

    # Validar tamaño máximo de payload para prevenir DoS por RAM bombing.
    if len(body) > MAX_WEBHOOK_BODY_SIZE:
        logger.warning("Webhook rechazado: body excede tamaño máximo — size_bytes=%d", len(body))
        raise HTTPException(status_code=413, detail="Payload demasiado grande")

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
    try:
        payload: dict[str, object] = json.loads(body)
    except json.JSONDecodeError as err:
        logger.warning("Webhook rechazado: body no es JSON válido")
        raise HTTPException(status_code=400, detail="Body debe ser JSON válido") from err
    return payload
