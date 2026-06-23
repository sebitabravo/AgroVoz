"""Rate limiting específico para el endpoint de clima OpenWeatherMap.

Defensa en profundidad contra agotamiento del plan gratuito de OWM (60 req/min):
  - Capa 1: cache con TTL 30 min y clave truncada a .2f (~1.1 km)
  - Capa 2: WeatherSlidingWindow — 30 req/min por IP (FastAPI Depends)

A diferencia de RateLimitMiddleware (global, 60/min/IP), este rate limiter
es específico para /weather y usa una ventana más restrictiva porque OWM
es el único recurso externo con cuota limitada en el MVP.

Thread-safe con threading.Lock() para requests concurrentes.
"""

import logging
import threading
import time

from fastapi import HTTPException, Request

from app.core.config import settings

logger = logging.getLogger(__name__)

# Ventana de rate limiting en segundos (1 minuto). Configurable via settings.
# Usar un valor más bajo que el default de 60 para dar margen de seguridad
# frente a otros consumidores de la API key (ej: otro entorno de dev).
_DEFAULT_WINDOW_SECONDS = 60


class WeatherSlidingWindow:
    """Rate limiter con sliding window por IP para el endpoint de clima.

    Ventana deslizante de 60s. Si una IP acumula >= weather_rate_limit_per_minute
    requests dentro de la ventana, el request N+1 recibe HTTP 429.

    La limpieza periódica de IPs inactivas evita crecimiento no acotado
    del diccionario en memoria.

    Usa time.monotonic() para ser inmune a ajustes de reloj del sistema.
    """

    def __init__(self) -> None:
        self._requests: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._last_cleanup: float = 0.0

    def check(self, ip: str, now: float | None = None) -> float | None:
        """Verifica si la IP excede el límite.

        Args:
            ip: Dirección IP del cliente.
            now: Timestamp monotónico (inyectable para tests determinísticos).

        Returns:
            None si el request está permitido.
            float con segundos restantes hasta que la IP pueda volver a
            consultar (para header Retry-After) si el límite fue excedido.
        """
        if now is None:
            now = time.monotonic()

        limit = settings.weather_rate_limit_per_minute
        window = _DEFAULT_WINDOW_SECONDS

        with self._lock:
            # Limpieza periódica de IPs inactivas (cada 60s).
            if now - self._last_cleanup >= 60:
                dead_ips = [
                    ip_key for ip_key, timestamps in self._requests.items()
                    if not any(now - t < window for t in timestamps)
                ]
                for ip_key in dead_ips:
                    del self._requests[ip_key]
                self._last_cleanup = now

            # Limpiar timestamps fuera de la ventana para esta IP.
            self._requests.setdefault(ip, [])
            self._requests[ip] = [t for t in self._requests[ip] if now - t < window]

            # Poda si quedó vacía.
            if not self._requests[ip]:
                del self._requests[ip]

            # Rate-limit check: si ya alcanzó el límite, rechazar.
            current_count = len(self._requests.get(ip, []))
            if current_count >= limit:
                # Calcular cuánto falta para que el timestamp más antiguo
                # salga de la ventana (para Retry-After).
                oldest = min(self._requests[ip])
                retry_after = window - (now - oldest)
                return max(retry_after, 1.0)

            # Request permitido: registrar timestamp.
            self._requests.setdefault(ip, []).append(now)

        return None

    def reset(self) -> None:
        """Limpia todos los contadores. Para tests."""
        with self._lock:
            self._requests.clear()
            self._last_cleanup = 0.0


# Instancia global compartida entre requests.
# No usa slowapi/redis para mantener MVP sin dependencias externas.
_weather_limiter = WeatherSlidingWindow()


async def check_weather_rate_limit(request: Request) -> None:
    """Dependencia FastAPI: rate limiting para GET /api/v1/weather.

    Rechaza con HTTP 429 si la IP excede weather_rate_limit_per_minute
    requests por minuto. Incluye header Retry-After con los segundos
    restantes para que el cliente sepa cuándo reintentar.

    Usa _get_client_ip() (misma función que RateLimitMiddleware) para
    consistencia en la extracción de IP detrás de Traefik.

    Raises:
        HTTPException 429: Límite de requests por minuto excedido.
    """
    from app.core.security import _get_client_ip

    ip = _get_client_ip(request)
    retry_after = _weather_limiter.check(ip)
    if retry_after is not None:
        logger.warning("Rate limit excedido para /weather — IP=%s", ip)
        raise HTTPException(
            status_code=429,
            detail="Demasiadas consultas de clima. Intenta de nuevo en un minuto.",
            headers={"Retry-After": str(int(retry_after))},
        )
