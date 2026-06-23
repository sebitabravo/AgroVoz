"""Servicio OpenWeatherMap: consulta de clima actual.

Issue #17: get_weather(lat, lon) para Tool Calling del LLM.
MVP usa coordenadas fijas de Traiguén (-38.23, -72.68).
Plan gratuito: 60 calls/min. Cache en memoria con TTL 30 min.
"""

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# TTL del cache en segundos (30 min). Plan gratuito permite 60 calls/min,
# así que 30 min es conservador para un solo usuario.
_CACHE_TTL_SECONDS = 30 * 60

# Tamaño máximo del cache. Evita crecimiento no acotado si el LLM consulta
# muchas coordenadas distintas. Con 50 entradas sobra para MVP (1-2 ubicaciones).
_CACHE_MAX_SIZE = 50

# Timeout HTTP. La API de OpenWeatherMap responde en <1s típicamente.
_TIMEOUT_SECONDS = 10

# URL base de OpenWeatherMap Current Weather Data API (plan gratuito).
_OWM_API_URL = "https://api.openweathermap.org/data/2.5/weather"

# Coordenadas default para MVP: Traiguén, Región de La Araucanía, Chile.
DEFAULT_LAT = -38.23
DEFAULT_LON = -72.68

# Cliente HTTP compartido con connection pooling hacia OpenWeatherMap.
# Se inicializa lazy en _get_http_client(). Evita instanciar un
# AsyncClient nuevo por cada request.
_http_client: httpx.AsyncClient | None = None


@dataclass
class WeatherData:
    """Datos estructurados de clima extraídos de OpenWeatherMap.

    Punto único de extracción: aquí se aplican defaults, casts
    y decisiones de negocio (ej: lluvia = 0 se reporta como None).
    Tanto el endpoint REST como get_weather() del LLM consumen
    esta estructura.
    """

    lat: float
    lon: float
    location: str
    temperature_c: float
    feels_like_c: float
    humidity: int
    description: str
    wind_speed_ms: float | None
    rain_1h_mm: float | None
    texto: str


# Cache en memoria: {cache_key: (timestamp_monotonic, WeatherData)}.
_cache: dict[str, tuple[float, WeatherData]] = {}


def _cache_key(lat: float, lon: float) -> str:
    """Clave de cache para un par de coordenadas."""
    return f"{lat:.4f}:{lon:.4f}"


def _cache_get(lat: float, lon: float) -> WeatherData | None:
    """Devuelve WeatherData cacheado si la entrada existe y no expiró."""
    key = _cache_key(lat, lon)
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, wd = entry
    if time.monotonic() - ts > _CACHE_TTL_SECONDS:
        del _cache[key]
        return None
    logger.debug("Cache hit para %s", key)
    return wd


def _cache_set(lat: float, lon: float, wd: WeatherData) -> None:
    """Guarda WeatherData en el cache con timestamp actual.

    Si el cache excede _CACHE_MAX_SIZE, evicta la entrada más antigua
    para evitar crecimiento no acotado.
    """
    key = _cache_key(lat, lon)
    _cache[key] = (time.monotonic(), wd)
    if len(_cache) > _CACHE_MAX_SIZE:
        oldest_key = min(_cache, key=lambda k: _cache[k][0])
        del _cache[oldest_key]
        logger.debug("Cache evictado (oldest): %s", oldest_key)


def _clear_cache() -> None:
    """Limpia el cache completo. Útil para tests."""
    _cache.clear()


async def _get_http_client() -> httpx.AsyncClient:
    """Devuelve un AsyncClient compartido con connection pooling.

    Se inicializa lazy en la primera llamada y se reusa en requests
    subsiguientes. Evita el overhead de crear/destruir un cliente
    HTTP por cada consulta a OpenWeatherMap.
    """
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)
    return _http_client


async def _close_http_client() -> None:
    """Cierra el cliente HTTP compartido. Para tests y shutdown."""
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None


def _extract_weather_data(data: Mapping[str, object], lat: float, lon: float) -> WeatherData:
    """Extrae datos tipados de la respuesta JSON de OpenWeatherMap.

    Punto ÚNICO de extracción para todo el módulo. Aplica defaults,
    cast() y decisiones de negocio (umbral de lluvia, valores None).
    Tanto get_weather_full() como _format_weather() pasan por aquí.
    """
    main = cast(dict[str, object], data.get("main", {}))
    weather_list = cast(list[dict[str, object]], data.get("weather", []))
    weather = weather_list[0] if weather_list else {}
    wind = cast(dict[str, object], data.get("wind", {}))
    rain = cast(dict[str, object], data.get("rain", {}))
    coord = cast(dict[str, object], data.get("coord", {}))

    temp_val = cast(float, main.get("temp", 0.0))
    feels_val = cast(float, main.get("feels_like", 0.0))
    hum_val = cast(int, main.get("humidity", 0))
    desc_val = cast(str, weather.get("description", "sin datos"))
    coord_lat = cast(float, coord.get("lat", lat))
    coord_lon = cast(float, coord.get("lon", lon))
    loc_name = cast(str, data.get("name")) or "Desconocido"

    wind_val: float | None = None
    if "speed" in wind:
        wind_val = cast(float, wind["speed"])

    rain_val: float | None = None
    if rain:
        rain_1h = cast(float, rain.get("1h", rain.get("3h", 0.0)))
        if rain_1h > 0:
            rain_val = rain_1h

    # Valores para _format_weather: None cuando el campo está realmente
    # ausente del JSON, para que el texto refleje "no disponible" en vez
    # de un valor por defecto (ej: 0.0°C). Consistente con el viejo
    # comportamiento de _format_weather cuando parseaba el dict directo.
    temp_for_text: float | None = cast(float, main.get("temp"))
    hum_for_text: int | None = cast(int, main.get("humidity"))

    texto = _format_weather(
        temp=temp_for_text,
        humidity=hum_for_text,
        description=desc_val,
        wind_speed=wind_val,
        rain_mm=rain_val,
        location=loc_name,
    )

    return WeatherData(
        lat=coord_lat,
        lon=coord_lon,
        location=loc_name,
        temperature_c=temp_val,
        feels_like_c=feels_val,
        humidity=hum_val,
        description=desc_val,
        wind_speed_ms=wind_val,
        rain_1h_mm=rain_val,
        texto=texto,
    )


async def get_weather_full(
    lat: float = DEFAULT_LAT,
    lon: float = DEFAULT_LON,
) -> WeatherData:
    """Consulta clima y devuelve datos estructurados + texto natural.

    Función pública para el endpoint REST. Usa cache en memoria
    con TTL de 30 minutos. La extracción de campos, defaults y
    decisiones de negocio están centralizadas en _extract_weather_data().

    Args:
        lat: Latitud. Default: Traiguén (-38.23).
        lon: Longitud. Default: Traiguén (-72.68).

    Returns:
        WeatherData con todos los campos tipados.

    Raises:
        ValueError: API key no configurada.
        ConnectionError: Error de red.
        RuntimeError: Error de API (key inválida, rate limit, etc.).
    """
    cached = _cache_get(lat, lon)
    if cached is not None:
        return cached

    data = await _fetch_weather_data(lat, lon)
    wd = _extract_weather_data(data, lat, lon)
    _cache_set(lat, lon, wd)
    logger.info("Clima obtenido para (%.4f, %.4f): %s", lat, lon, wd.location)
    return wd


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
        client = await _get_http_client()
        response = await client.get(_OWM_API_URL, params=params)
        response.raise_for_status()
        data: dict[str, object] = response.json()
        return data
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
    except ValueError as exc:
        raise RuntimeError(
            f"Respuesta no-JSON de OpenWeatherMap: {exc}"
        ) from exc


def _format_weather(
    temp: float | None = None,
    humidity: int | None = None,
    description: str = "sin datos",
    wind_speed: float | None = None,
    rain_mm: float | None = None,
    location: str = "la zona consultada",
) -> str:
    """Formatea datos de clima a texto natural en español chileno.

    Recibe valores ya extraídos y tipados desde _extract_weather_data().
    Ningún campo se lee del dict crudo — la extracción ocurre UNA sola vez
    en _extract_weather_data(), punto único de verdad para todo el módulo.

    Args:
        temp: Temperatura en °C. None → "temperatura no disponible".
        humidity: Humedad relativa en %. None → se omite.
        description: Descripción del clima. "sin datos" → se omite.
        wind_speed: Velocidad del viento en m/s. None → se omite.
        rain_mm: Lluvia última hora en mm. None o 0 → se omite.
        location: Nombre de la ubicación según OpenWeatherMap.

    Returns:
        Texto natural listo para Piper TTS.
    """
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

    if rain_mm is not None and rain_mm > 0:
        partes.append(f", lluvia {rain_mm:.1f} mm")

    return "".join(partes) + "."


async def get_weather(
    lat: float = DEFAULT_LAT,
    lon: float = DEFAULT_LON,
) -> str:
    """Consulta el clima actual y devuelve texto natural en español chileno.

    Tool function para el LLM vía Tool Calling. Delega en get_weather_full()
    la consulta y extracción, y retorna solo el texto.

    Args:
        lat: Latitud. Default: Traiguén (-38.23).
        lon: Longitud. Default: Traiguén (-72.68).

    Returns:
        Texto natural listo para TTS. Ejemplo:
        "En Traiguén ahora: 18°C, cielo nublado, humedad 65%, viento 3.6 m/s."

        Si hay error, retorna un mensaje informativo en vez de lanzar
        excepción, para que el LLM pueda comunicarlo al agricultor.
    """
    # Validación de rango: misma defensa que el endpoint REST (Query ge/le).
    # get_weather() retorna mensajes informativos, no excepciones, para que
    # el LLM pueda comunicar el error al agricultor sin romper el diálogo.
    if not (-90.0 <= lat <= 90.0):
        return "La latitud debe estar entre -90° y 90°. ¿Me das otra coordenada?"
    if not (-180.0 <= lon <= 180.0):
        return "La longitud debe estar entre -180° y 180°. ¿Me das otra coordenada?"

    try:
        wd = await get_weather_full(lat, lon)
        return wd.texto
    except ValueError as exc:
        logger.warning("Configuración de clima incompleta: %s", exc)
        return "El servicio de clima no está configurado todavía."
    except ConnectionError as exc:
        logger.warning("Error de red al consultar clima: %s", exc)
        return "No pude consultar el clima ahora. ¿Probamos más tarde?"
    except RuntimeError as exc:
        logger.warning("Error de API al consultar clima: %s", exc)
        return "El servicio de clima no está disponible en este momento."
