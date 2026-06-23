"""Servicio OpenWeatherMap: consulta de clima actual.

Issue #17: get_weather(lat, lon) para Tool Calling del LLM.
MVP usa coordenadas fijas de Traiguén (-38.23, -72.68).
Plan gratuito: 60 calls/min. Cache en memoria con TTL 30 min.
"""

import logging
import time
from collections.abc import Mapping
from typing import cast

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# TTL del cache en segundos (30 min). Plan gratuito permite 60 calls/min,
# así que 30 min es conservador para un solo usuario.
_CACHE_TTL_SECONDS = 30 * 60

# Timeout HTTP. La API de OpenWeatherMap responde en <1s típicamente.
_TIMEOUT_SECONDS = 10

# URL base de OpenWeatherMap Current Weather Data API (plan gratuito).
_OWM_API_URL = "https://api.openweathermap.org/data/2.5/weather"

# Coordenadas default para MVP: Traiguén, Región de La Araucanía, Chile.
DEFAULT_LAT = -38.23
DEFAULT_LON = -72.68

# Cache en memoria: {cache_key: (timestamp_monotonic, texto_formateado)}.
_cache: dict[str, tuple[float, str]] = {}


def _cache_key(lat: float, lon: float) -> str:
    """Clave de cache para un par de coordenadas."""
    return f"{lat:.4f}:{lon:.4f}"


def _cache_get(lat: float, lon: float) -> str | None:
    """Devuelve texto cacheado si la entrada existe y no expiró."""
    key = _cache_key(lat, lon)
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, text = entry
    if time.monotonic() - ts > _CACHE_TTL_SECONDS:
        del _cache[key]
        return None
    logger.debug("Cache hit para %s", key)
    return text


def _cache_set(lat: float, lon: float, text: str) -> None:
    """Guarda texto en el cache con timestamp actual."""
    key = _cache_key(lat, lon)
    _cache[key] = (time.monotonic(), text)


def _clear_cache() -> None:
    """Limpia el cache completo. Útil para tests."""
    _cache.clear()


async def _fetch_weather_data(lat: float, lon: float) -> dict[str, object]:
    """Obtiene datos crudos de OpenWeatherMap Current Weather API.

    Args:
        lat: Latitud.
        lon: Longitud.

    Returns:
        Diccionario con la respuesta JSON de la API.

    Raises:
        ValueError: API key no configurada.
        ConnectionError: Error de red (DNS, timeout, conexión rechazada).
        RuntimeError: Error de API (key inválida, rate limit, etc.).
    """
    api_key = (settings.openweathermap_api_key or "").strip()
    if not api_key:
        raise ValueError("OPENWEATHERMAP_API_KEY no está configurada")

    params: Mapping[str, str | float] = {
        "lat": lat,
        "lon": lon,
        "appid": api_key,
        "units": "metric",
        "lang": "es",
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.get(_OWM_API_URL, params=params)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            raise RuntimeError("API key de OpenWeatherMap inválida") from exc
        if exc.response.status_code == 429:
            raise RuntimeError(
                "Rate limit de OpenWeatherMap excedido (60/min)"
            ) from exc
        raise RuntimeError(
            f"OpenWeatherMap respondió HTTP {exc.response.status_code}"
        ) from exc
    except httpx.RequestError as exc:
        raise ConnectionError(
            f"Error de red al consultar OpenWeatherMap: {exc}"
        ) from exc

    data: dict[str, object] = response.json()
    return data


def _format_weather(data: Mapping[str, object]) -> str:
    """Formatea la respuesta JSON en texto natural español chileno.

    Formato: "En Traiguén ahora: 18°C, cielo nublado, humedad 65%,
    viento 3.6 m/s."

    Args:
        data: Respuesta JSON de OpenWeatherMap Current Weather API.

    Returns:
        Texto natural listo para Piper TTS.
    """
    main = cast(dict[str, object], data.get("main", {}))
    weather_list = cast(list[dict[str, object]], data.get("weather", []))
    weather = weather_list[0] if weather_list else {}
    wind = cast(dict[str, object], data.get("wind", {}))
    rain = cast(dict[str, object], data.get("rain", {}))

    temp = main.get("temp")
    humidity = main.get("humidity")
    description = weather.get("description", "sin datos")
    wind_speed = wind.get("speed")
    location = cast(str, data.get("name")) or "la zona consultada"

    # Temperatura: redondear a entero para texto natural.
    temp_str = f"{temp:.0f}°C" if temp is not None else "temperatura no disponible"

    partes: list[str] = []

    if temp is not None and humidity is not None and description != "sin datos":
        partes.append(
            f"En {location} ahora: {temp_str}, {description}, "
            f"humedad {humidity}%"
        )
    else:
        # Respuesta degradada: incluir lo que tengamos.
        partes.append(f"En {location} ahora: {temp_str}")
        if description != "sin datos":
            partes.append(f", {description}")
        if humidity is not None:
            partes.append(f", humedad {humidity}%")

    if wind_speed is not None:
        partes.append(f", viento {wind_speed:.1f} m/s")

    # Lluvia: OpenWeatherMap devuelve rain.1h (mm última hora) o rain.3h.
    if rain:
        rain_mm = cast(float, rain.get("1h", rain.get("3h", 0.0)))
        if rain_mm > 0:
            partes.append(f", lluvia {rain_mm:.1f} mm")

    return "".join(partes) + "."


async def get_weather(
    lat: float = DEFAULT_LAT,
    lon: float = DEFAULT_LON,
) -> str:
    """Consulta el clima actual y devuelve texto natural en español chileno.

    Tool function para el LLM vía Tool Calling. Usa cache en memoria
    con TTL de 30 minutos para no exceder el rate limit del plan gratuito
    (60 calls/min).

    Args:
        lat: Latitud. Default: Traiguén (-38.23).
        lon: Longitud. Default: Traiguén (-72.68).

    Returns:
        Texto natural listo para TTS. Ejemplo:
        "En Traiguén ahora: 18°C, cielo nublado, humedad 65%, viento 3.6 m/s."

        Si hay error, retorna un mensaje informativo en vez de lanzar
        excepción, para que el LLM pueda comunicarlo al agricultor.
    """
    # Cache: si la respuesta ya está en memoria y no expiró, la reusamos.
    cached = _cache_get(lat, lon)
    if cached is not None:
        return cached

    try:
        data = await _fetch_weather_data(lat, lon)
        text = _format_weather(data)
        _cache_set(lat, lon, text)
        logger.info("Clima obtenido para (%.4f, %.4f): %s", lat, lon, data.get("name", "?"))
        return text
    except ValueError as exc:
        logger.warning("Configuración de clima incompleta: %s", exc)
        return "El servicio de clima no está configurado todavía."
    except ConnectionError as exc:
        logger.warning("Error de red al consultar clima: %s", exc)
        return "No pude consultar el clima ahora. ¿Probamos más tarde?"
    except RuntimeError as exc:
        logger.warning("Error de API al consultar clima: %s", exc)
        return "El servicio de clima no está disponible en este momento."
