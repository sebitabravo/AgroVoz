"""Servicio OpenMeteo: clima actual, pronóstico e histórico sin API key.

Issue #50: get_weather(lat, lon) para Tool Calling del LLM.
Issue #247: resúmenes climáticos multianuales desde Archive API.
OpenMeteo es gratuita, sin API key, 10.000 requests/día.
MVP usa coordenadas fijas de Traiguén (-38.23, -72.68).
Cache en memoria con TTL 30 min para clima actual y 24 h para histórico.
"""

import asyncio
import datetime
import logging
import time
import unicodedata
from dataclasses import dataclass, replace

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_LAT",
    "DEFAULT_LON",
    "ForecastDay",
    "HistoricalYearSummary",
    "WeatherData",
    "clear_weather_cache",
    "fetch_historico",
    "get_clima_historico",
    "get_clima_historico_multianual",
    "get_weather",
    "get_weather_forecast_daily",
    "get_weather_full",
    "weather_cache_size",
]

# TTL del cache en segundos (30 min).
_CACHE_TTL_SECONDS = 30 * 60

# TTL del cache histórico en segundos (24h). El histórico no cambia nunca,
# pero mantener un TTL razonable permite renovar si OpenMeteo agrega datos.
_HISTORICAL_CACHE_TTL_SECONDS = 24 * 3600

# Tamaño máximo del cache histórico. Cada entrada puede tener varios años.
_HISTORICAL_CACHE_MAX_SIZE = 30

# OpenMeteo Archive documenta datos desde 1940. Acotamos el rango para evitar
# consultas accidentalmente enormes y mantener el tiempo de respuesta estable
# en el VPS con pocos recursos.
_HISTORICAL_MAX_YEARS = 5
_ARCHIVE_FIRST_YEAR = 1940

# Tamaño máximo del cache. Evita crecimiento no acotado.
_CACHE_MAX_SIZE = 50

# Timeout HTTP. OpenMeteo responde típicamente en <100ms.
_TIMEOUT_SECONDS = 10

# URL base de OpenMeteo Forecast API (sin API key, 10.000 req/día).
_OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

# URL base de OpenMeteo Archive API (sin API key, datos históricos desde 1940).
_OPENMETEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Coordenadas default para MVP: Traiguén, Región de La Araucanía, Chile.
DEFAULT_LAT = -38.23
DEFAULT_LON = -72.68

# Umbral para detectar coordenadas cercanas a Traiguén.
# Si lat y lon están a menos de esta distancia, usamos "Traiguén"
# como nombre de ubicación.
_TRAIGUEN_THRESHOLD = 0.05

# Diccionario simple de comunas chilenas a coordenadas para la tool
# get_clima_historico() y el fast-path de clima. Sin API de geocoding
# para mantener el MVP simple. Cobertura: piloto Traiguén + Araucanía
# cercana + Santiago (referencia).
_COMUNAS: dict[str, tuple[float, float]] = {
    # Piloto Traiguén y alrededores (mismas coords aproximadas)
    "traiguen": (-38.23, -72.68),
    "traiguén": (-38.23, -72.68),
    "lumaco": (-38.15, -72.90),
    "galvarino": (-38.41, -72.78),
    "cholchol": (-38.60, -72.84),
    # Temuco y conurbación
    "temuco": (-38.74, -72.59),
    "padre las casas": (-38.77, -72.61),
    "lautaro": (-38.53, -72.43),
    "nueva imperial": (-38.74, -72.95),
    "villarrica": (-39.28, -72.23),
    "pucón": (-39.28, -71.95),
    "pucon": (-39.28, -71.95),
    "angol": (-37.80, -72.71),
    "collipulli": (-37.96, -72.43),
    "pitrufquén": (-38.98, -72.64),
    "pitrufquen": (-38.98, -72.64),
    # Referencia nacional
    "santiago": (-33.45, -70.65),
}

# Temporadas meteorológicas usadas por la herramienta multianual. Verano
# cruza el año calendario: el verano de 2024 es diciembre de 2023 a febrero
# de 2024, mientras que las demás temporadas caben dentro del mismo año.
_SEASON_MONTHS: dict[str, tuple[int, ...]] = {
    "verano": (12, 1, 2),
    "otoño": (3, 4, 5),
    "invierno": (6, 7, 8),
    "primavera": (9, 10, 11),
}

_SEASON_ALIASES: dict[str, str] = {
    "verano": "verano",
    "otono": "otoño",
    "otoño": "otoño",
    "invierno": "invierno",
    "primavera": "primavera",
}

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
    stale_age_minutes: int | None = None


# Cache en memoria: {cache_key: (timestamp_monotonic, WeatherData)}.
_cache: dict[str, tuple[float, WeatherData]] = {}


def _ttl_get[V](cache: dict[str, tuple[float, V]], key: str, ttl_seconds: int, label: str) -> V | None:
    """Devuelve el valor cacheado si existe y no expiró. No elimina entradas vencidas.

    Helper compartido por el cache de clima actual y el histórico; cada uno pasa su TTL.
    """
    entry = cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts > ttl_seconds:
        return None
    logger.debug("Cache hit — tipo=%s", label)
    return value


def _evict_oldest_if_full[V](cache: dict[str, tuple[float, V]], max_size: int, label: str) -> None:
    """Evicta la entrada más antigua si el cache supera su tamaño máximo."""
    if len(cache) > max_size:
        oldest_key = min(cache, key=lambda k: cache[k][0])
        del cache[oldest_key]
        logger.debug("Cache evictado — tipo=%s estrategia=oldest", label)


def _cache_key(lat: float, lon: float) -> str:
    """Clave de cache para un par de coordenadas.

    Trunca a 2 decimales (~1.1 km de resolución) para agrupar requests
    con variaciones mínimas de coordenadas bajo la misma entrada del cache.

    Para clima, 1.1 km de resolución es más que suficiente — la temperatura
    y condiciones no varían significativamente a esa escala.
    """
    return f"{lat:.2f}:{lon:.2f}"


def _cache_get(lat: float, lon: float) -> WeatherData | None:
    """Devuelve WeatherData cacheado si la entrada existe y no expiró.

    Si la entrada expiró, no la elimina: se conserva para el fallback
    degradado cuando OpenMeteo falla (Issue #120).
    """
    return _ttl_get(_cache, _cache_key(lat, lon), _CACHE_TTL_SECONDS, "forecast")


def _cache_get_stale(lat: float, lon: float, max_age_seconds: int) -> tuple[int, WeatherData] | None:
    """Devuelve una entrada vencida si aún está dentro del tope de degradación.

    Args:
        lat: Latitud.
        lon: Longitud.
        max_age_seconds: Antigüedad máxima aceptable para fallback degradado.

    Returns:
        Tupla (age_seconds, WeatherData) si existe y no supera el tope.
        None si no existe o es demasiado vieja.
    """
    key = _cache_key(lat, lon)
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, wd = entry
    age_seconds = int(time.monotonic() - ts)
    if age_seconds > max_age_seconds:
        return None
    return age_seconds, wd


def _prune_stale_cache(max_age_seconds: int) -> None:
    """Elimina entradas más viejas que el tope de degradación.

    Llamada tras cada escritura para evitar que el cache en memoria
    acumule datos obsoletos indefinidamente.
    """
    now = time.monotonic()
    expired_keys = [k for k, (ts, _) in _cache.items() if now - ts > max_age_seconds]
    for key in expired_keys:
        del _cache[key]


def _cache_set(lat: float, lon: float, wd: WeatherData) -> None:
    """Guarda WeatherData en el cache con timestamp actual.

    Si el cache excede _CACHE_MAX_SIZE, evicta la entrada más antigua
    para evitar crecimiento no acotado. También limpia entradas más
    viejas que el tope de degradación configurado.
    """
    key = _cache_key(lat, lon)
    _cache[key] = (time.monotonic(), wd)
    _prune_stale_cache(settings.weather_stale_cache_max_age_hours * 3600)
    _evict_oldest_if_full(_cache, _CACHE_MAX_SIZE, "forecast")


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


# ── Cache de datos históricos ─────────────────────────────────


# Cache de datos históricos: {cache_key: (timestamp_monotonic, list[HistoricalYearSummary])}.
_historical_cache: dict[str, tuple[float, list["HistoricalYearSummary"]]] = {}


def _historical_cache_key(
    lat: float,
    lon: float,
    years: int,
    temporada: str | None = None,
    anio: int | None = None,
) -> str:
    """Construye una clave estable para un rango histórico consultado.

    La clave conserva el formato anterior para consultas anuales relativas,
    de modo que el cache existente siga siendo reutilizable después del
    despliegue de la herramienta multianual.
    """
    key = f"hist:{lat:.2f}:{lon:.2f}:y{years}"
    if temporada is not None:
        key += f":s{temporada}"
    if anio is not None:
        key += f":a{anio}"
    return key


def _historical_cache_get(
    lat: float,
    lon: float,
    years: int,
    temporada: str | None = None,
    anio: int | None = None,
) -> list["HistoricalYearSummary"] | None:
    """Devuelve datos históricos cacheados si la entrada existe y no expiró."""
    return _ttl_get(
        _historical_cache,
        _historical_cache_key(lat, lon, years, temporada, anio),
        _HISTORICAL_CACHE_TTL_SECONDS,
        "historico",
    )


def _historical_cache_set(
    lat: float,
    lon: float,
    years: int,
    data: list["HistoricalYearSummary"],
    temporada: str | None = None,
    anio: int | None = None,
) -> None:
    """Guarda datos históricos en el cache con timestamp actual."""
    key = _historical_cache_key(lat, lon, years, temporada, anio)
    _historical_cache[key] = (time.monotonic(), data)
    _evict_oldest_if_full(_historical_cache, _HISTORICAL_CACHE_MAX_SIZE, "historico")


def _clear_historical_cache() -> None:
    """Limpia el cache histórico. Para tests."""
    _historical_cache.clear()


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

    OpenMeteo no devuelve nombre de ciudad. Buscamos en ``_COMUNAS``
    (piloto Traiguén + Araucanía). Si no hay match, nombre genérico.
    """
    for _nombre, (clat, clon) in _COMUNAS.items():
        if abs(lat - clat) < _TRAIGUEN_THRESHOLD and abs(lon - clon) < _TRAIGUEN_THRESHOLD:
            bonita = max(
                (n for n in _COMUNAS if _COMUNAS[n] == (clat, clon)),
                key=lambda n: (any(c in n for c in "áéíóúñ"), len(n)),
            )
            return " ".join(parte.capitalize() for parte in bonita.split())
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

    Si OpenMeteo falla y existe un cache vencido dentro del tope de
    degradación configurado (WEATHER_STALE_CACHE_MAX_AGE_HOURS), entrega
    ese dato con una advertencia de antigüedad en el texto (Issue #120).

    No requiere API key — OpenMeteo es gratuito (10.000 req/día).

    Args:
        lat: Latitud. Default: Traiguén (-38.23).
        lon: Longitud. Default: Traiguén (-72.68).

    Returns:
        WeatherData con todos los campos tipados.

    Raises:
        ConnectionError: Error de red y sin cache degradado disponible.
        RuntimeError: Error de API o respuesta malformada y sin cache degradado.
    """
    cached = _cache_get(lat, lon)
    if cached is not None:
        return cached

    try:
        data = await _fetch_weather_data(lat, lon)
    except (ConnectionError, RuntimeError):
        stale = _cache_get_stale(lat, lon, settings.weather_stale_cache_max_age_hours * 3600)
        if stale is not None:
            age_seconds, wd = stale
            age_minutes = age_seconds // 60
            logger.warning("usando cache vencido por fallo de API, edad %dmin", age_minutes)
            texto_degradado = _format_weather(
                temp=wd.temperature_c,
                humidity=wd.humidity,
                description=wd.description,
                wind_speed=wd.wind_speed_ms,
                rain_mm=wd.rain_1h_mm,
                location=wd.location,
                stale_age_minutes=age_minutes,
            )
            return replace(wd, texto=texto_degradado, stale_age_minutes=age_minutes)
        raise

    wd = _parse_openmeteo_response(data, lat, lon)
    _cache_set(lat, lon, wd)
    logger.info("Clima obtenido — estado=ok")
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
        logger.warning(
            "Error de red al consultar OpenMeteo — error=%s",
            type(exc).__name__,
        )
        raise ConnectionError("Error de red al consultar OpenMeteo") from exc
    except ValueError as exc:
        # Captura json.JSONDecodeError
        raise RuntimeError(f"Respuesta de OpenMeteo malformada: {exc}") from exc


def _stale_age_phrase(age_minutes: int) -> str:
    """Convierte minutos de antigüedad a frase natural en español chileno.

    Usado para advertir al agricultor cuando el pronóstico proviene de un
    cache degradado por fallo de OpenMeteo.
    """
    if age_minutes < 60:
        return f"de hace {age_minutes} minutos"
    hours = age_minutes // 60
    if hours == 1:
        return "de hace 1 hora"
    if hours < 24:
        return f"de hace {hours} horas"
    return "de hace más de 1 día"


def _format_weather(
    temp: float | None = None,
    humidity: int | None = None,
    description: str = "sin datos",
    wind_speed: float | None = None,
    rain_mm: float | None = None,
    location: str = "la zona consultada",
    stale_age_minutes: int | None = None,
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
        stale_age_minutes: Si se indica, se antepone una advertencia de
            antigüedad al texto (cache degradado por fallo de API).

    Returns:
        Texto natural listo para Piper TTS. Termina con "según OpenMeteo"
        para citar la fuente del dato (Issue #95).
    """
    temp_str = f"{temp:.0f}°C" if temp is not None else "temperatura no disponible"

    prefijo = ""
    if stale_age_minutes is not None:
        prefijo = f"Pronóstico {_stale_age_phrase(stale_age_minutes)}: "

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
    return prefijo + "".join(partes) + ", según OpenMeteo."


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
        logger.warning(
            "Error de red al consultar clima — error=%s",
            type(exc).__name__,
        )
        return "No pude consultar el clima ahora. ¿Probamos más tarde?"
    except RuntimeError as exc:
        logger.warning(
            "Error de API al consultar clima — error=%s",
            type(exc).__name__,
        )
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
        logger.warning(
            "Error de red al consultar OpenMeteo — error=%s",
            type(exc).__name__,
        )
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


# ── Datos climáticos históricos (Issue #124) ────────────────────


@dataclass
class HistoricalYearSummary:
    """Resumen climático anual extraído de OpenMeteo Archive.

    Calculado a partir de datos diarios de temperatura máxima, mínima
    y precipitación acumulada.

    Atributos:
        year: Año del resumen (ej: 2025).
        temp_promedio: Temperatura promedio anual en °C (promedio de
            los promedios diarios de temp_max y temp_min).
        temp_max_promedio: Promedio anual de temperatura máxima en °C.
        temp_min_promedio: Promedio anual de temperatura mínima en °C.
        precipitacion_total_mm: Precipitación total anual en mm.
        dias_helada: Días con temperatura mínima < 0°C en el año.
    """

    year: int
    temp_promedio: float | None
    temp_max_promedio: float | None
    temp_min_promedio: float | None
    precipitacion_total_mm: float | None
    dias_helada: int | None
    temporada: str | None = None
    hasta_mes_dia: tuple[int, int] | None = None


def _normalizar_temporada(temporada: str | None) -> str | None:
    """Normaliza una temporada recibida desde texto o Tool Calling.

    La normalización permite que Whisper o el LLM omitan tildes sin crear
    claves de cache distintas para la misma consulta.
    """
    if temporada is None:
        return None
    if not isinstance(temporada, str) or not temporada.strip():
        raise ValueError("Temporada no reconocida; usa verano, otoño, invierno o primavera")
    sin_tildes = "".join(
        caracter
        for caracter in unicodedata.normalize("NFD", temporada.strip().lower())
        if unicodedata.category(caracter) != "Mn"
    )
    normalizada = _SEASON_ALIASES.get(sin_tildes)
    if normalizada is None:
        raise ValueError("Temporada no reconocida; usa verano, otoño, invierno o primavera")
    return normalizada


def _normalizar_metrica(metrica: str | None) -> str | None:
    """Normaliza la métrica opcional que se enfatiza en la respuesta hablada."""
    if metrica is None:
        return None
    if not isinstance(metrica, str) or not metrica.strip():
        return None
    sin_tildes = "".join(
        caracter
        for caracter in unicodedata.normalize("NFD", metrica.strip().lower())
        if unicodedata.category(caracter) != "Mn"
    )
    aliases = {
        "temperatura": "temperatura",
        "temperaturas": "temperatura",
        "lluvia": "lluvia",
        "lluvias": "lluvia",
        "precipitacion": "lluvia",
        "precipitaciones": "lluvia",
        "helada": "heladas",
        "heladas": "heladas",
    }
    return aliases.get(sin_tildes)


def _historical_year_bounds(
    years: int,
    anio: int | None,
    *,
    today: datetime.date | None = None,
) -> tuple[int, int]:
    """Resuelve el rango de años completos solicitado.

    Sin año explícito se consultan los últimos años completos. Con ``anio``
    se interpreta ese valor como el año final, lo que permite comparar, por
    ejemplo, 2023 y 2024 sin depender de la fecha actual del servidor.
    """
    years = min(max(years, 1), _HISTORICAL_MAX_YEARS)
    current_year = (today or datetime.date.today()).year
    end_year = current_year - 1 if anio is None else anio
    if end_year < _ARCHIVE_FIRST_YEAR or end_year > current_year:
        raise ValueError(f"Año fuera de rango; debe estar entre {_ARCHIVE_FIRST_YEAR} y {current_year}")
    start_year = end_year - years + 1
    if start_year < _ARCHIVE_FIRST_YEAR:
        raise ValueError(f"El rango histórico no puede comenzar antes de {_ARCHIVE_FIRST_YEAR}")
    return start_year, end_year


def _historical_date_range(
    start_year: int,
    end_year: int,
    temporada: str | None,
) -> tuple[datetime.date, datetime.date]:
    """Convierte años y temporada en fechas inclusivas para Archive API."""
    if temporada is None:
        return datetime.date(start_year, 1, 1), datetime.date(end_year, 12, 31)
    if temporada == "verano":
        return datetime.date(start_year - 1, 12, 1), datetime.date(end_year, 2, 29 if _es_bisiesto(end_year) else 28)
    meses = _SEASON_MONTHS[temporada]
    return (
        datetime.date(start_year, meses[0], 1),
        datetime.date(end_year, meses[-1], _ultimo_dia_mes(end_year, meses[-1])),
    )


def _es_bisiesto(year: int) -> bool:
    """Indica si un año es bisiesto para cerrar el verano correctamente."""
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _ultimo_dia_mes(year: int, month: int) -> int:
    """Retorna el último día de un mes sin dependencias externas."""
    if month == 2:
        return 29 if _es_bisiesto(year) else 28
    if month in (4, 6, 9, 11):
        return 30
    return 31


def _resolver_comuna(comuna: str) -> tuple[float, float] | None:
    """Resuelve nombre de comuna chilena a coordenadas.

    Usa el diccionario _COMUNAS (sin API de geocoding).
    Búsqueda case-insensitive.

    Args:
        comuna: Nombre de la comuna (ej: "Traiguén", "temuco").

    Returns:
        Tupla (lat, lon) si la comuna está en el diccionario, None si no.
    """
    return _COMUNAS.get(comuna.strip().lower())


def resolver_comuna(comuna: str) -> tuple[float, float] | None:
    """API pública: resuelve comuna a (lat, lon). Ver ``_resolver_comuna``."""
    return _resolver_comuna(comuna)


def extraer_comuna_de_consulta(query: str) -> str | None:
    """Busca una comuna conocida dentro del texto de la consulta.

    Preferir el match más largo ("padre las casas" antes que un token
    parcial). Si hay variantes con/sin tilde, usa la forma con tilde
    para que el TTS diga "Traiguén" y no "traiguen".

    Args:
        query: Texto de la consulta del productor.

    Returns:
        Nombre de comuna capitalizado, o None si no aparece ninguna.
    """
    q = query.strip().lower()
    for nombre in sorted(_COMUNAS.keys(), key=len, reverse=True):
        if nombre not in q:
            continue
        coords = _COMUNAS[nombre]
        bonita = max(
            (n for n in _COMUNAS if _COMUNAS[n] == coords),
            key=lambda n: (any(c in n for c in "áéíóúñ"), len(n)),
        )
        return " ".join(parte.capitalize() for parte in bonita.split())
    return None


async def fetch_historico(
    lat: float = DEFAULT_LAT,
    lon: float = DEFAULT_LON,
    years: int = 1,
    temporada: str | None = None,
    anio: int | None = None,
    today: datetime.date | None = None,
) -> list[HistoricalYearSummary]:
    """Consulta el histórico climático de OpenMeteo Archive.

    Obtiene datos diarios de temperatura máxima, mínima y precipitación
    para los últimos `years` años completos. Calcula resúmenes anuales
    con promedios y totales. ``temporada`` permite limitar el cálculo a
    verano, otoño, invierno o primavera; ``anio`` fija el año más reciente
    para que la consulta sea reproducible.

    OpenMeteo Archive no requiere API key. Cache en memoria con TTL 24h
    porque los datos históricos no cambian.

    Args:
        lat: Latitud. Default: Traiguén (-38.23).
        lon: Longitud. Default: Traiguén (-72.68).
        years: Cantidad de años completos hacia atrás (1-5). Default: 1.
        temporada: Temporada opcional a resumir.
        anio: Año final opcional del rango (1940 hasta el último año completo).

    Returns:
        Lista de HistoricalYearSummary ordenada por año ascendente.

    Raises:
        ValueError: Si las coordenadas, el año o la temporada no son válidos.
        ConnectionError: Error de red al consultar OpenMeteo.
        RuntimeError: Error de API o respuesta malformada.
    """
    if not (-90.0 <= lat <= 90.0):
        raise ValueError("Latitud fuera de rango")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError("Longitud fuera de rango")

    years = min(max(years, 1), _HISTORICAL_MAX_YEARS)
    temporada_normalizada = _normalizar_temporada(temporada)
    effective_today = today or datetime.date.today()
    start_year, end_year = _historical_year_bounds(years, anio, today=effective_today)

    cached = _historical_cache_get(
        lat,
        lon,
        years,
        temporada_normalizada,
        anio,
    )
    if cached is not None:
        return cached

    start_date, end_date = _historical_date_range(
        start_year,
        end_year,
        temporada_normalizada,
    )
    period_end: tuple[int, int] | None = None
    if end_year == effective_today.year:
        archive_cutoff = effective_today - datetime.timedelta(days=5)
        if archive_cutoff.year != end_year:
            raise ValueError("El archivo histórico aún no tiene datos del año actual")
        if archive_cutoff < start_date:
            raise ValueError("El periodo actual todavía no tiene datos históricos disponibles")
        if archive_cutoff < end_date:
            end_date = archive_cutoff
            period_end = (end_date.month, end_date.day)

    data = await _fetch_historical_data(lat, lon, start_date, end_date)
    summaries = _parse_historical_response(
        data,
        start_year,
        end_year,
        temporada=temporada_normalizada,
        period_end=period_end,
    )

    _historical_cache_set(
        lat,
        lon,
        years,
        summaries,
        temporada_normalizada,
        anio,
    )
    logger.info(
        "Histórico obtenido — years_count=%d rango_inicio=%d rango_fin=%d temporada=%s",
        len(summaries),
        start_year,
        end_year,
        temporada_normalizada or "anual",
    )
    return summaries


async def _fetch_historical_data(
    lat: float,
    lon: float,
    start_date: datetime.date,
    end_date: datetime.date,
) -> dict[str, object]:
    """Obtiene datos diarios históricos de OpenMeteo Archive.

    Args:
        lat: Latitud.
        lon: Longitud.
        start_date: Fecha de inicio (inclusive).
        end_date: Fecha de fin (inclusive).

    Returns:
        Dict con la respuesta JSON de OpenMeteo Archive.

    Raises:
        ConnectionError: Error de red.
        RuntimeError: Error de API o respuesta malformada.
    """
    params: dict[str, str | float | int] = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": "auto",
    }

    try:
        client = await _get_http_client()
        response = await client.get(_OPENMETEO_ARCHIVE_URL, params=params)
        response.raise_for_status()
        data: dict[str, object] = response.json()
        return data
    except httpx.HTTPStatusError as exc:
        logger.warning("OpenMeteo Archive respondió HTTP %s", exc.response.status_code)
        raise RuntimeError(f"OpenMeteo Archive respondió HTTP {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        logger.warning(
            "Error de red al consultar OpenMeteo Archive — error=%s",
            type(exc).__name__,
        )
        raise ConnectionError("Error de red al consultar OpenMeteo Archive") from exc
    except ValueError as exc:
        raise RuntimeError(f"Respuesta de OpenMeteo Archive malformada: {exc}") from exc


def _parse_historical_response(
    data: dict[str, object],
    start_year: int,
    end_year: int,
    temporada: str | None = None,
    period_end: tuple[int, int] | None = None,
) -> list[HistoricalYearSummary]:
    """Parsea la respuesta de OpenMeteo Archive a resúmenes anuales.

    Agrupa datos diarios por año y calcula promedios y totales.

    Args:
        data: JSON parseado de OpenMeteo Archive.
        start_year: Primer año del rango consultado.
        end_year: Último año del rango consultado.
        temporada: Temporada opcional; sirve para filtrar meses y rotular
            el resumen. ``verano`` asigna diciembre al año siguiente.

    Returns:
        Lista de HistoricalYearSummary ordenada por año.
    """
    daily: dict[str, object] = {}
    if isinstance(data.get("daily"), dict):
        daily = data["daily"]  # type: ignore[assignment]

    fechas_raw = daily.get("time", [])
    maxs_raw = daily.get("temperature_2m_max", [])
    mins_raw = daily.get("temperature_2m_min", [])
    precips_raw = daily.get("precipitation_sum", [])

    if not isinstance(fechas_raw, list):
        raise RuntimeError("OpenMeteo Archive: daily.time no es una lista")

    temporada_normalizada = _normalizar_temporada(temporada)
    meses_temporada = set(_SEASON_MONTHS[temporada_normalizada]) if temporada_normalizada is not None else None

    yearly_data: dict[int, dict[str, list[float]]] = {}
    for i, fecha_str in enumerate(fechas_raw):
        try:
            fecha = datetime.date.fromisoformat(str(fecha_str))
        except ValueError:
            continue

        if meses_temporada is not None and fecha.month not in meses_temporada:
            continue
        if period_end is not None and (fecha.month, fecha.day) > period_end:
            continue

        # En verano, diciembre pertenece al verano del año siguiente.
        year = fecha.year + 1 if temporada_normalizada == "verano" and fecha.month == 12 else fecha.year
        if year < start_year or year > end_year:
            continue

        if year not in yearly_data:
            yearly_data[year] = {
                "temp_max": [],
                "temp_min": [],
                "temp_avg": [],
                "precip": [],
            }

        tmax = _safe_float(maxs_raw[i]) if isinstance(maxs_raw, list) and i < len(maxs_raw) else None
        tmin = _safe_float(mins_raw[i]) if isinstance(mins_raw, list) and i < len(mins_raw) else None
        precip = _safe_float(precips_raw[i]) if isinstance(precips_raw, list) and i < len(precips_raw) else None

        if tmax is not None:
            yearly_data[year]["temp_max"].append(tmax)
        if tmin is not None:
            yearly_data[year]["temp_min"].append(tmin)
        if tmax is not None and tmin is not None:
            yearly_data[year]["temp_avg"].append((tmax + tmin) / 2.0)
        if precip is not None:
            yearly_data[year]["precip"].append(precip)

    summaries: list[HistoricalYearSummary] = []
    for year in sorted(yearly_data.keys()):
        yd = yearly_data[year]
        tmax_list = yd["temp_max"]
        tmin_list = yd["temp_min"]
        temp_avg_list = yd["temp_avg"]
        precip_list = yd["precip"]

        temp_promedio = sum(temp_avg_list) / len(temp_avg_list) if temp_avg_list else None

        temp_max_promedio = sum(tmax_list) / len(tmax_list) if tmax_list else None
        temp_min_promedio = sum(tmin_list) / len(tmin_list) if tmin_list else None
        precipitacion_total = sum(precip_list) if precip_list else None

        dias_helada = sum(1 for t in tmin_list if t < 0) if tmin_list else None

        summaries.append(
            HistoricalYearSummary(
                year=year,
                temp_promedio=temp_promedio,
                temp_max_promedio=temp_max_promedio,
                temp_min_promedio=temp_min_promedio,
                precipitacion_total_mm=precipitacion_total,
                dias_helada=dias_helada,
                temporada=temporada_normalizada,
                hasta_mes_dia=period_end,
            )
        )

    return summaries


def _periodo_historico_label(summary: HistoricalYearSummary) -> str:
    """Devuelve la etiqueta hablada de un año o temporada."""
    label = (
        f"el {summary.temporada} de {summary.year}"
        if summary.temporada is not None
        else f"el año {summary.year}"
    )
    if summary.hasta_mes_dia is not None:
        month, day = summary.hasta_mes_dia
        label += f" hasta el {day:02d}/{month:02d}"
    return label


def _comparar_lluvia(
    summaries: list[HistoricalYearSummary],
) -> str | None:
    """Resume el cambio de lluvia entre los dos años más recientes."""
    if len(summaries) < 2:
        return None
    anterior, actual = summaries[-2:]
    lluvia_anterior = anterior.precipitacion_total_mm
    lluvia_actual = actual.precipitacion_total_mm
    if lluvia_anterior is None or lluvia_actual is None:
        return None
    if lluvia_anterior == 0:
        if lluvia_actual == 0:
            return "La lluvia fue la misma en ambos periodos, según OpenMeteo."
        return (
            f"En {_periodo_historico_label(actual)} cayeron {lluvia_actual:.0f}mm; "
            f"no hay porcentaje comparable porque {_periodo_historico_label(anterior)} no registró lluvia."
        )
    porcentaje = round((lluvia_actual - lluvia_anterior) / lluvia_anterior * 100)
    if porcentaje == 0:
        return "La lluvia fue prácticamente igual en ambos periodos, según OpenMeteo."
    relacion = "más" if porcentaje > 0 else "menos"
    return (
        f"En {_periodo_historico_label(actual)} llovió {abs(porcentaje)}% {relacion} "
        f"que en {_periodo_historico_label(anterior)}, según OpenMeteo."
    )


def _format_historico_text(
    summaries: list[HistoricalYearSummary],
    comuna: str,
    metrica: str | None = None,
) -> str:
    """Formatea resúmenes multianuales a texto natural en español chileno.

    La comparación porcentual se calcula solo sobre los dos periodos más
    recientes y nunca agrega interpretación agronómica.
    """
    if not summaries:
        return f"No hay datos históricos disponibles para {comuna}, según OpenMeteo."

    metrica_normalizada = _normalizar_metrica(metrica)
    sorted_sums = sorted(summaries, key=lambda s: s.year)

    partes: list[str] = []
    for summary in sorted_sums:
        detalles: list[str] = []

        if metrica_normalizada in (None, "temperatura"):
            if summary.temp_promedio is not None:
                detalles.append(f"temperatura promedio de {summary.temp_promedio:.0f}°C")
            if summary.temp_max_promedio is not None:
                detalles.append(f"máxima promedio de {summary.temp_max_promedio:.0f}°C")
            if summary.temp_min_promedio is not None:
                detalles.append(f"mínima promedio de {summary.temp_min_promedio:.0f}°C")

        if metrica_normalizada in (None, "lluvia") and summary.precipitacion_total_mm is not None:
            if summary.precipitacion_total_mm >= 1000:
                detalles.append(f"precipitación total de {summary.precipitacion_total_mm / 1000:.1f} metros")
            else:
                detalles.append(f"{summary.precipitacion_total_mm:.0f}mm de lluvia")

        if metrica_normalizada in (None, "heladas") and summary.dias_helada is not None:
            if summary.dias_helada == 0:
                detalles.append("sin días de helada")
            elif summary.dias_helada == 1:
                detalles.append("1 día de helada")
            else:
                detalles.append(f"{summary.dias_helada} días de helada")

        if detalles:
            partes.append(f"{_periodo_historico_label(summary)} tuvo " + ", ".join(detalles))

    if not partes:
        return f"No hay datos climáticos históricos disponibles para {comuna}, según OpenMeteo."

    texto = f"En {comuna}, " + ", y ".join(partes) + ", según OpenMeteo."
    if metrica_normalizada in (None, "lluvia"):
        comparacion = _comparar_lluvia(sorted_sums)
        if comparacion is not None:
            texto += f" {comparacion}"
    return texto


def _format_pronostico_text(dias: list[ForecastDay], comuna: str) -> str:
    """Arma el texto hablado del pronostico para el LLM y el TTS.

    Formato pensado para voz: sin tablas ni simbolos que Piper deletree.
    La lluvia se menciona solo si se espera algo, para no llenar la respuesta
    de "0 milimetros" cuando no viene agua.

    SOLO INFORMA. No dice si regar, sembrar ni cosechar.

    Args:
        dias: Pronostico diario ya consultado, ordenado por fecha.
        comuna: Nombre de la comuna para nombrarla en la respuesta.

    Returns:
        Texto natural en espanol chileno.
    """
    if not dias:
        return f"No tengo pronóstico disponible para {comuna} ahora."

    etiquetas = ["Mañana", "Pasado mañana"]
    partes: list[str] = []

    for i, dia in enumerate(dias):
        cuando = etiquetas[i] if i < len(etiquetas) else f"El {dia.fecha.strftime('%d/%m')}"

        tramos: list[str] = []
        if dia.temp_max_c is not None and dia.temp_min_c is not None:
            tramos.append(f"máxima de {round(dia.temp_max_c)} grados y mínima de {round(dia.temp_min_c)}")
        elif dia.temp_max_c is not None:
            tramos.append(f"máxima de {round(dia.temp_max_c)} grados")

        # La lluvia se nombra siempre: "no llueve" tambien es la respuesta a
        # "va a llover manana?", y omitirla dejaria la pregunta sin contestar.
        lluvia = dia.precipitation_sum_mm
        if lluvia is not None and lluvia >= 0.1:
            mm = f"{lluvia:.1f}".replace(".", ",")
            tramos.append(f"{mm} milímetros de lluvia")
        elif lluvia is not None:
            tramos.append("sin lluvia")

        if tramos:
            partes.append(f"{cuando} en {comuna}: {', '.join(tramos)}")

    if not partes:
        return f"No tengo pronóstico disponible para {comuna} ahora."

    return f"{'. '.join(partes)}. Según OpenMeteo."


async def get_pronostico(comuna: str, dias: int = 2) -> str:
    """Tool function para el LLM: pronostico de los proximos dias.

    Responde preguntas como "va a llover manana?" o "como viene el tiempo?".
    Antes de esta tool el LLM solo tenia clima ACTUAL (get_weather) e
    historico, asi que una pregunta sobre manana no se podia contestar con
    datos: es la consulta mas comun del productor antes de decidir si cosecha.

    SOLO INFORMA DATOS. Sin recomendaciones agronomicas: entrega milimetros y
    temperaturas, no dice si regar o cosechar.

    Args:
        comuna: Nombre de la comuna (ej: "Traiguen", "Temuco", "Santiago").
        dias: Cuantos dias de pronostico (1 a 3). Por defecto 2.

    Returns:
        Texto natural en espanol chileno para TTS.
    """
    coords = _resolver_comuna(comuna)
    if coords is None:
        return (
            f"Disculpa, no reconozco la comuna '{comuna}'. "
            "Puedo consultar Traiguén, Temuco, Padre Las Casas, Lautaro, "
            "Villarrica y otras de la Araucanía, o Santiago. "
            "¿Cuál te interesa?"
        )

    lat, lon = coords
    dias_pedidos = min(max(dias, 1), 3)

    try:
        pronostico = await get_weather_forecast_daily(lat, lon, days=dias_pedidos)
        return _format_pronostico_text(pronostico, comuna)
    except (ConnectionError, RuntimeError, ValueError) as exc:
        logger.warning(
            "Error al consultar pronóstico — error=%s",
            type(exc).__name__,
        )
        return f"No pude consultar el pronóstico de {comuna} ahora. ¿Probamos más tarde?"


async def get_clima_historico_multianual(
    comuna: str,
    anos: int = 3,
    temporada: str | None = None,
    anio: int | None = None,
    metrica: str | None = None,
) -> str:
    """Consulta resúmenes climáticos de varios años para una comuna.

    La consulta usa únicamente datos diarios de OpenMeteo Archive y calcula
    de forma determinista temperatura media, lluvia acumulada y días con
    temperatura mínima bajo cero. No entrega recomendaciones agronómicas.

    Args:
        comuna: Nombre de la comuna chilena.
        anos: Cantidad de años a comparar (1 a 5).
        temporada: Verano, otoño, invierno o primavera.
        anio: Año final opcional del rango consultado.
        metrica: Métrica a enfatizar en la respuesta hablada.
    """
    coords = _resolver_comuna(comuna)
    if coords is None:
        return (
            f"Disculpa, no reconozco la comuna '{comuna}'. "
            "Puedo consultar Traiguén, Temuco, Padre Las Casas, Lautaro, "
            "Villarrica y otras de la Araucanía, o Santiago. "
            "¿Cuál te interesa?"
        )

    lat, lon = coords
    try:
        anos_pedidos = min(max(int(anos), 1), _HISTORICAL_MAX_YEARS)
        anio_pedido = int(anio) if anio is not None else None
    except (TypeError, ValueError):
        return "Los años de consulta deben ser números válidos."
    try:
        summaries = await fetch_historico(
            lat,
            lon,
            years=anos_pedidos,
            temporada=temporada,
            anio=anio_pedido,
        )
    except (ConnectionError, RuntimeError, ValueError) as exc:
        logger.warning(
            "Error al consultar histórico multianual — error=%s",
            type(exc).__name__,
        )
        return f"No pude consultar el histórico climático de {comuna} ahora. ¿Probamos más tarde?"
    if not summaries:
        return f"No hay datos históricos disponibles para {comuna}, según OpenMeteo."
    return _format_historico_text(summaries, comuna, metrica=metrica)


async def get_clima_historico(comuna: str, metrica: str | None = None) -> str:
    """Tool function para el LLM: consulta clima histórico de una comuna.

    Recibe nombre de comuna chilena y métrica opcional. Resuelve la comuna
    a coordenadas, consulta OpenMeteo Archive (último año) y devuelve texto
    natural en español chileno.

    SOLO INFORMA DATOS. Sin recomendaciones agronómicas.

    Args:
        comuna: Nombre de la comuna (ej: "Traiguén", "Temuco", "Santiago").
        metrica: Métrica a enfatizar: "temperatura", "lluvia", "heladas",
            o None para resumen completo.

    Returns:
        Texto natural en español chileno para TTS.
    """
    coords = _resolver_comuna(comuna)
    if coords is None:
        return (
            f"Disculpa, no reconozco la comuna '{comuna}'. "
            "Puedo consultar Traiguén, Temuco, Padre Las Casas, Lautaro, "
            "Villarrica y otras de la Araucanía, o Santiago. "
            "¿Cuál te interesa?"
        )

    lat, lon = coords

    try:
        summaries = await fetch_historico(lat, lon, years=1)
        if not summaries:
            return f"No hay datos históricos disponibles para {comuna} en el último año, según OpenMeteo."
        return _format_historico_text(summaries, comuna, metrica=metrica)
    except (ConnectionError, RuntimeError) as exc:
        logger.warning(
            "Error al consultar histórico — error=%s",
            type(exc).__name__,
        )
        return f"No pude consultar el histórico climático de {comuna} ahora. ¿Probamos más tarde?"
