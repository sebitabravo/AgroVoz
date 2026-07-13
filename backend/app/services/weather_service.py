"""Servicio OpenMeteo: consulta de clima actual sin API key.

Issue #50: get_weather(lat, lon) para Tool Calling del LLM.
OpenMeteo es gratuita, sin API key, 10.000 requests/día.
MVP usa coordenadas fijas de Traiguén (-38.23, -72.68).
Cache en memoria con TTL 30 min.
"""

import asyncio
import datetime
import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_LAT",
    "DEFAULT_LON",
    "ForecastDay",
    "WeatherData",
    "get_weather",
    "get_weather_forecast_daily",
    "get_weather_full",
]

# TTL del cache en segundos (30 min).
_CACHE_TTL_SECONDS = 30 * 60

# Tamaño máximo del cache. Evita crecimiento no acotado.
_CACHE_MAX_SIZE = 50

# Timeout HTTP. OpenMeteo responde típicamente en <100ms.
_TIMEOUT_SECONDS = 10

# URL base de OpenMeteo Forecast API (sin API key, 10.000 req/día).
_OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

# Coordenadas default para MVP: Traiguén, Región de La Araucanía, Chile.
DEFAULT_LAT = -38.23
DEFAULT_LON = -72.68

# Umbral para detectar coordenadas cercanas a Traiguén.
# Si lat y lon están a menos de esta distancia, usamos "Traiguén"
# como nombre de ubicación.
_TRAIGUEN_THRESHOLD = 0.05

# Mapa de códigos WMO (World Meteorological Organization) a descripciones
# en español chileno. OpenMeteo devuelve weather_code según estándar WMO.
# Códigos: 0-3 = cielo, 45-48 = niebla, 51-57 = llovizna,
# 61-67 = lluvia, 71-77 = nieve, 80-86 = chubascos, 95-99 = tormenta.
_WMO_CODES: dict[int, str] = {
    0: "cielo despejado",
    1: "mayormente despejado",
    2: "parcialmente nublado",
    3: "nublado",
    45: "neblina",
    48: "niebla con escarcha",
    51: "llovizna ligera",
    53: "llovizna moderada",
    55: "llovizna densa",
    56: "llovizna helada ligera",
    57: "llovizna helada densa",
    61: "lluvia ligera",
    63: "lluvia moderada",
    65: "lluvia fuerte",
    66: "lluvia helada ligera",
    67: "lluvia helada fuerte",
    71: "nevada ligera",
    73: "nevada moderada",
    75: "nevada fuerte",
    77: "granos de nieve",
    80: "chubascos ligeros",
    81: "chubascos moderados",
    82: "chubascos violentos",
    85: "chubascos de nieve ligeros",
    86: "chubascos de nieve fuertes",
    95: "tormenta eléctrica",
    96: "tormenta con granizo ligero",
    99: "tormenta con granizo fuerte",
}

# Cliente HTTP compartido con connection pooling.
_http_client: httpx.AsyncClient | None = None
_http_client_lock = asyncio.Lock()


@dataclass
class WeatherData:
    """Datos estructurados de clima extraídos de OpenMeteo.

    Punto único de extracción: aquí se aplican defaults, casts
    y decisiones de negocio (ej: lluvia = 0 se reporta como None).
    Tanto el endpoint REST como get_weather() del LLM consumen
    esta estructura.
    """

    lat: float
    lon: float
    location: str
    temperature_c: float | None
    feels_like_c: float | None
    humidity: int | None
    description: str
    wind_speed_ms: float | None
    rain_1h_mm: float | None
    texto: str


# Cache en memoria: {cache_key: (timestamp_monotonic, WeatherData)}.
_cache: dict[str, tuple[float, WeatherData]] = {}


def _cache_key(lat: float, lon: float) -> str:
    """Clave de cache para un par de coordenadas.

    Trunca a 2 decimales (~1.1 km de resolución) para agrupar requests
    con variaciones mínimas de coordenadas bajo la misma entrada del cache.

    Para clima, 1.1 km de resolución es más que suficiente — la temperatura
    y condiciones no varían significativamente a esa escala.
    """
    return f"{lat:.2f}:{lon:.2f}"


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


def clear_weather_cache() -> int:
    """Limpia el cache de clima en memoria. Retorna el número de entradas eliminadas.

    Función pública para el dashboard admin. No requiere parámetros:
    el cache es global al módulo.
    """
    count = len(_cache)
    _cache.clear()
    return count


def weather_cache_size() -> int:
    """Retorna el número de entradas activas en el cache de clima."""
    return len(_cache)


async def _get_http_client() -> httpx.AsyncClient:
    """Devuelve un AsyncClient compartido con connection pooling.

    Se inicializa lazy en la primera llamada y se reusa en requests
    subsiguientes. Evita el overhead de crear/destruir un cliente
    HTTP por cada consulta a OpenMeteo.

    Usa double-checked locking con asyncio.Lock para evitar race
    condition cuando dos corutinas concurrentes crean el cliente.
    """
    global _http_client
    if _http_client is None or _http_client.is_closed:
        async with _http_client_lock:
            if _http_client is None or _http_client.is_closed:
                _http_client = httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)
    return _http_client


async def _close_http_client() -> None:
    """Cierra el cliente HTTP compartido. Para tests y shutdown."""
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None


def _location_name(lat: float, lon: float) -> str:
    """Determina el nombre de ubicación según las coordenadas.

    OpenMeteo no devuelve nombre de ciudad, así que usamos un
    nombre genérico. Si las coordenadas están cerca de Traiguén
    (default MVP), lo llamamos por su nombre.
    """
    if abs(lat - DEFAULT_LAT) < _TRAIGUEN_THRESHOLD and abs(lon - DEFAULT_LON) < _TRAIGUEN_THRESHOLD:
        return "Traiguén"
    return "la zona consultada"


def _wmo_description(code: int | None) -> str:
    """Traduce un código WMO a descripción textual en español.

    Si el código no está en el mapa, devuelve "sin datos".
    """
    if code is None:
        return "sin datos"
    return _WMO_CODES.get(code, "sin datos")


def _parse_openmeteo_response(data: dict[str, object], lat: float, lon: float) -> WeatherData:
    """Parsea la respuesta JSON de OpenMeteo a WeatherData.

    La respuesta de OpenMeteo tiene esta estructura:
    {
        "current": {
            "temperature_2m": 4.1,
            "relative_humidity_2m": 98,
            "apparent_temperature": 1.2,
            "weather_code": 3,
            "wind_speed_10m": 9.4,
            "rain": 0.0
        }
    }

    Args:
        data: JSON parseado de la respuesta de OpenMeteo.
        lat: Latitud consultada (fallback si no viene en respuesta).
        lon: Longitud consultada (fallback si no viene en respuesta).

    Returns:
        WeatherData con todos los campos tipados.
    """
    current: dict[str, object] = {}
    if isinstance(data.get("current"), dict):
        current = data["current"]  # type: ignore[assignment]

    temp_val: float | None = _safe_float(current.get("temperature_2m"))
    feels_val: float | None = _safe_float(current.get("apparent_temperature"))
    hum_val: int | None = _safe_int(current.get("relative_humidity_2m"))
    wind_val: float | None = _safe_float(current.get("wind_speed_10m"))
    rain_val_raw: float | None = _safe_float(current.get("rain"))

    # weather_code es int, puede venir como None
    wmo_code: int | None = _safe_int(current.get("weather_code"))
    desc_val: str = _wmo_description(wmo_code)

    # Lluvia: solo reportar si > 0
    rain_val: float | None = None
    if rain_val_raw is not None and rain_val_raw > 0:
        rain_val = rain_val_raw

    loc_name: str = _location_name(lat, lon)

    texto = _format_weather(
        temp=temp_val,
        humidity=hum_val,
        description=desc_val,
        wind_speed=wind_val,
        rain_mm=rain_val,
        location=loc_name,
    )

    return WeatherData(
        lat=lat,
        lon=lon,
        location=loc_name,
        temperature_c=temp_val,
        feels_like_c=feels_val,
        humidity=hum_val,
        description=desc_val,
        wind_speed_ms=wind_val,
        rain_1h_mm=rain_val,
        texto=texto,
    )


def _safe_float(value: object) -> float | None:
    """Convierte un valor a float de forma segura.

    Retorna None si el valor es None o no se puede convertir.
    Solo acepta valores numéricos (int, float) o strings convertibles.
    """
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
    return None


def _safe_int(value: object) -> int | None:
    """Convierte un valor a int de forma segura.

    Retorna None si el valor es None o no se puede convertir.
    Solo acepta valores numéricos (int, float) o strings convertibles.
    """
    if value is None:
        return None
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
    return None


async def get_weather_full(
    lat: float = DEFAULT_LAT,
    lon: float = DEFAULT_LON,
) -> WeatherData:
    """Consulta clima y devuelve datos estructurados + texto natural.

    Función pública para el endpoint REST. Usa cache en memoria
    con TTL de 30 minutos. La extracción de campos, defaults y
    decisiones de negocio están centralizadas en _parse_openmeteo_response().

    No requiere API key — OpenMeteo es gratuito (10.000 req/día).

    Args:
        lat: Latitud. Default: Traiguén (-38.23).
        lon: Longitud. Default: Traiguén (-72.68).

    Returns:
        WeatherData con todos los campos tipados.

    Raises:
        ConnectionError: Error de red.
        RuntimeError: Error de API o respuesta malformada.
    """
    cached = _cache_get(lat, lon)
    if cached is not None:
        return cached

    data = await _fetch_weather_data(lat, lon)
    wd = _parse_openmeteo_response(data, lat, lon)
    _cache_set(lat, lon, wd)
    logger.info("Clima obtenido para (%.4f, %.4f): %s", lat, lon, wd.location)
    logger.debug(
        "OpenMeteo raw — temp=%s hum=%s code=%s wind=%s rain=%s",
        wd.temperature_c,
        wd.humidity,
        wd.description,
        wd.wind_speed_ms,
        wd.rain_1h_mm,
    )
    return wd


async def _fetch_weather_data(lat: float, lon: float) -> dict[str, object]:
    """Obtiene datos de OpenMeteo Forecast API.

    OpenMeteo no requiere API key. Usamos current weather variables:
    temperature_2m, relative_humidity_2m, apparent_temperature,
    weather_code (WMO), wind_speed_10m, rain.

    Args:
        lat: Latitud.
        lon: Longitud.

    Returns:
        Dict con la respuesta JSON de OpenMeteo.

    Raises:
        ConnectionError: Error de red (DNS, timeout, conexión rechazada).
        RuntimeError: Error de API o respuesta malformada.
    """
    params: dict[str, str | float] = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,rain",
        "timezone": "auto",
        "forecast_days": 1,
        "windspeed_unit": "ms",
    }

    try:
        client = await _get_http_client()
        response = await client.get(_OPENMETEO_URL, params=params)
        response.raise_for_status()
        data: dict[str, object] = response.json()
        return data
    except httpx.HTTPStatusError as exc:
        logger.warning("OpenMeteo respondió HTTP %s", exc.response.status_code)
        raise RuntimeError(f"OpenMeteo respondió HTTP {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        logger.warning("Error de red al consultar OpenMeteo: %s", exc)
        raise ConnectionError("Error de red al consultar OpenMeteo") from exc
    except ValueError as exc:
        # Captura json.JSONDecodeError
        raise RuntimeError(f"Respuesta de OpenMeteo malformada: {exc}") from exc


def _format_weather(
    temp: float | None = None,
    humidity: int | None = None,
    description: str = "sin datos",
    wind_speed: float | None = None,
    rain_mm: float | None = None,
    location: str = "la zona consultada",
) -> str:
    """Formatea datos de clima a texto natural en español chileno.

    Recibe valores ya extraídos y tipados desde _parse_openmeteo_response().
    Ningún campo se lee del dict crudo — la extracción ocurre UNA sola vez
    en _parse_openmeteo_response(), punto único de verdad para todo el módulo.

    Args:
        temp: Temperatura en °C. None → "temperatura no disponible".
        humidity: Humedad relativa en %. None → se omite.
        description: Descripción del clima. "sin datos" → se omite.
        wind_speed: Velocidad del viento en m/s. None → se omite.
        rain_mm: Lluvia última hora en mm. None o 0 → se omite.
        location: Nombre de la ubicación.

    Returns:
        Texto natural listo para Piper TTS. Termina con "según OpenMeteo"
        para citar la fuente del dato (Issue #95).
    """
    temp_str = f"{temp:.0f}°C" if temp is not None else "temperatura no disponible"

    partes: list[str] = []

    if temp is not None and humidity is not None and description != "sin datos":
        partes.append(f"En {location} ahora: {temp_str}, {description}, humedad {humidity}%")
    else:
        # Respuesta degradada: incluir lo que tengamos.
        partes.append(f"En {location} ahora: {temp_str}")
        if description != "sin datos":
            partes.append(f", {description}")
        if humidity is not None:
            partes.append(f", humedad {humidity}%")

    if wind_speed is not None and wind_speed > 0:
        partes.append(f", viento {wind_speed:.1f} m/s")

    if rain_mm is not None and rain_mm > 0:
        partes.append(f", lluvia {rain_mm:.1f} mm")

    # Cita "según OpenMeteo" incluida SIEMPRE en el dato retornado (capa determinista).
    # El system prompt refuerza que el LLM la conserve si reformula.
    # Diseño deliberado de defensa en profundidad (Issue #95):
    # - Capa 1 (determinista): hardcode en esta función garantiza la presencia.
    # - Capa 2 (LLM): instrucción del prompt previene que sea borrada.
    # Sin ambas, el LLM 3B podría descartar la fuente buscando ser "conciso".
    return "".join(partes) + ", según OpenMeteo."


async def get_weather(
    lat: float = DEFAULT_LAT,
    lon: float = DEFAULT_LON,
) -> str:
    """Consulta el clima actual y devuelve texto natural en español chileno.

    Tool function para el LLM vía Tool Calling. Delega en get_weather_full()
    la consulta y extracción, y retorna solo el texto.

    OpenMeteo no requiere API key.

    Args:
        lat: Latitud. Default: Traiguén (-38.23).
        lon: Longitud. Default: Traiguén (-72.68).

    Returns:
        Texto natural listo para TTS. Ejemplo:
        "En Traiguén ahora: 18°C, cielo nublado, humedad 65%, viento 3.6 m/s,
         según OpenMeteo."

        Si hay error, retorna un mensaje informativo en vez de lanzar
        excepción, para que el LLM pueda comunicarlo al agricultor.
    """
    # Validación de rango: misma defensa que el endpoint REST (Query ge/le).
    if not (-90.0 <= lat <= 90.0):
        return "La latitud debe estar entre -90° y 90°. ¿Me das otra coordenada?"
    if not (-180.0 <= lon <= 180.0):
        return "La longitud debe estar entre -180° y 180°. ¿Me das otra coordenada?"

    try:
        wd = await get_weather_full(lat, lon)
        return wd.texto
    except ConnectionError as exc:
        logger.warning("Error de red al consultar clima: %s", exc)
        return "No pude consultar el clima ahora. ¿Probamos más tarde?"
    except RuntimeError as exc:
        logger.warning("Error de API al consultar clima: %s", exc)
        return "El servicio de clima no está disponible en este momento."


# ── Pronostico diario para alertas proactivas (issue #88) ─────────


@dataclass
class ForecastDay:
    """Un dia de pronostico climatico diario."""

    fecha: datetime.date
    temp_min_c: float | None
    temp_max_c: float | None
    precipitation_sum_mm: float | None


async def get_weather_forecast_daily(
    lat: float = DEFAULT_LAT,
    lon: float = DEFAULT_LON,
    days: int = 3,
) -> list[ForecastDay]:
    """Consulta el pronostico diario de OpenMeteo.

    Retorna los proximos `days` dias con temperatura minima/maxima y
    precipitacion acumulada. Usado por el servicio de alertas climaticas.

    Args:
        lat: Latitud.
        lon: Longitud.
        days: Cantidad de dias de pronostico (1-7).

    Returns:
        Lista de ForecastDay ordenada por fecha.

    Raises:
        ConnectionError: Error de red.
        RuntimeError: Error de API o respuesta malformada.
    """
    if not (-90.0 <= lat <= 90.0):
        raise ValueError("Latitud fuera de rango")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError("Longitud fuera de rango")
    days = min(max(days, 1), 7)

    params: dict[str, str | float | int] = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_min,temperature_2m_max,precipitation_sum",
        "timezone": "auto",
        "forecast_days": days,
    }

    try:
        client = await _get_http_client()
        response = await client.get(_OPENMETEO_URL, params=params)
        response.raise_for_status()
        data: dict[str, object] = response.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("OpenMeteo respondio HTTP %s", exc.response.status_code)
        raise RuntimeError(f"OpenMeteo respondio HTTP {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        logger.warning("Error de red al consultar OpenMeteo: %s", exc)
        raise ConnectionError("Error de red al consultar OpenMeteo") from exc
    except ValueError as exc:
        raise RuntimeError(f"Respuesta de OpenMeteo malformada: {exc}") from exc

    return _parse_forecast_daily(data)


def _parse_forecast_daily(data: dict[str, object]) -> list[ForecastDay]:
    """Parsea la respuesta de OpenMeteo a una lista de ForecastDay."""
    daily: dict[str, object] = {}
    if isinstance(data.get("daily"), dict):
        daily = data["daily"]  # type: ignore[assignment]

    fechas_raw = daily.get("time", [])
    mins_raw = daily.get("temperature_2m_min", [])
    maxs_raw = daily.get("temperature_2m_max", [])
    precips_raw = daily.get("precipitation_sum", [])

    if not isinstance(fechas_raw, list):
        raise RuntimeError("OpenMeteo: daily.time no es una lista")

    dias: list[ForecastDay] = []
    for i, fecha_str in enumerate(fechas_raw):
        try:
            fecha = datetime.date.fromisoformat(str(fecha_str))
        except ValueError as exc:
            raise RuntimeError(f"Fecha de pronostico invalida: {fecha_str}") from exc
        temp_min = _safe_float(mins_raw[i]) if isinstance(mins_raw, list) and i < len(mins_raw) else None
        temp_max = _safe_float(maxs_raw[i]) if isinstance(maxs_raw, list) and i < len(maxs_raw) else None
        precip = _safe_float(precips_raw[i]) if isinstance(precips_raw, list) and i < len(precips_raw) else None
        dias.append(
            ForecastDay(
                fecha=fecha,
                temp_min_c=temp_min,
                temp_max_c=temp_max,
                precipitation_sum_mm=precip,
            )
        )
    return dias
