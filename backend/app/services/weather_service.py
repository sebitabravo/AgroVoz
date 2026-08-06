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
from dataclasses import dataclass, replace

import httpx
from sqlalchemy.exc import SQLAlchemyError

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

def _ttl_get[V](
    cache: dict[str, tuple[float, V]], key: str, ttl_seconds: int, label: str
) -> V | None:
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


def _evict_oldest_if_full[V](
    cache: dict[str, tuple[float, V]], max_size: int, label: str
) -> None:
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


def _cache_get_stale(
    lat: float, lon: float, max_age_seconds: int
) -> tuple[int, WeatherData] | None:
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


def _historical_cache_key(lat: float, lon: float, years: int) -> str:
    """Clave de cache para histórico truncando coordenadas a 2 decimales."""
    return f"hist:{lat:.2f}:{lon:.2f}:y{years}"


def _historical_cache_get(lat: float, lon: float, years: int) -> list["HistoricalYearSummary"] | None:
    """Devuelve datos históricos cacheados si la entrada existe y no expiró."""
    return _ttl_get(
        _historical_cache,
        _historical_cache_key(lat, lon, years),
        _HISTORICAL_CACHE_TTL_SECONDS,
        "historico",
    )


def _historical_cache_set(lat: float, lon: float, years: int, data: list["HistoricalYearSummary"]) -> None:
    """Guarda datos históricos en el cache con timestamp actual."""
    key = _historical_cache_key(lat, lon, years)
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


async def _get_user_coordinates(phone_hash: str | None) -> tuple[float, float] | None:
    """Carga coordenadas compartidas sin bloquear el event loop de FastAPI."""
    if not phone_hash:
        return None

    from app.services.location_service import get_user_location

    try:
        location = await asyncio.to_thread(get_user_location, phone_hash)
    except (SQLAlchemyError, OSError, RuntimeError, ValueError):
        # Una falla de preferencias no debe impedir el fallback por comuna.
        logger.warning("No se pudo leer ubicación preferida; se usa la comuna")
        return None
    if location is None:
        return None
    return location.lat, location.lng


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
        stale = _cache_get_stale(
            lat, lon, settings.weather_stale_cache_max_age_hours * 3600
        )
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
    # OpenMeteo solo necesita el grid aproximado; no enviar la coordenada
    # precisa evita exponer el pin del productor a un tercero.
    params: dict[str, str | float] = {
        "latitude": round(lat, 2),
        "longitude": round(lon, 2),
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
    lat: float | None = None,
    lon: float | None = None,
    phone_hash: str | None = None,
    comuna: str | None = None,
) -> str:
    """Consulta el clima actual y devuelve texto natural en español chileno.

    Tool function para el LLM vía Tool Calling. Delega en get_weather_full()
    la consulta y extracción, y retorna solo el texto.

    OpenMeteo no requiere API key.

    Args:
        lat: Latitud explícita solicitada por el usuario, o ``None`` si no
            especificó una ubicación.
        lon: Longitud explícita solicitada por el usuario, o ``None`` si no
            especificó una ubicación.
        phone_hash: Hash HMAC de la identidad; si tiene GPS guardado,
            reemplaza las coordenadas default.
        comuna: Comuna de fallback cuando no hay GPS guardado.

    Returns:
        Texto natural listo para TTS. Ejemplo:
        "En Traiguén ahora: 18°C, cielo nublado, humedad 65%, viento 3.6 m/s,
         según OpenMeteo."

        Si hay error, retorna un mensaje informativo en vez de lanzar
        excepción, para que el LLM pueda comunicarlo al agricultor.
    """
    explicit_coords = lat is not None and lon is not None
    if not explicit_coords:
        user_coords = await _get_user_coordinates(phone_hash)
        if user_coords is not None:
            lat, lon = user_coords
        elif comuna:
            comuna_coords = _resolver_comuna(comuna)
            if comuna_coords is not None:
                lat, lon = comuna_coords
        else:
            lat, lon = DEFAULT_LAT, DEFAULT_LON

    if lat is None or lon is None:
        lat, lon = DEFAULT_LAT, DEFAULT_LON

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
        "latitude": round(lat, 2),
        "longitude": round(lon, 2),
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
) -> list[HistoricalYearSummary]:
    """Consulta el histórico climático de OpenMeteo Archive.

    Obtiene datos diarios de temperatura máxima, mínima y precipitación
    para los últimos `years` años completos. Calcula resúmenes anuales
    con promedios y totales.

    OpenMeteo Archive no requiere API key. Cache en memoria con TTL 24h
    porque los datos históricos no cambian.

    Args:
        lat: Latitud. Default: Traiguén (-38.23).
        lon: Longitud. Default: Traiguén (-72.68).
        years: Cantidad de años completos hacia atrás (1-5). Default: 1.

    Returns:
        Lista de HistoricalYearSummary ordenada por año ascendente.

    Raises:
        ValueError: Si years está fuera de rango (1-5).
        ConnectionError: Error de red al consultar OpenMeteo.
        RuntimeError: Error de API o respuesta malformada.
    """
    years = min(max(years, 1), 5)
    if not (-90.0 <= lat <= 90.0):
        raise ValueError("Latitud fuera de rango")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError("Longitud fuera de rango")

    cached = _historical_cache_get(lat, lon, years)
    if cached is not None:
        return cached

    today = datetime.date.today()
    current_year = today.year
    start_year = current_year - years
    end_year = current_year - 1

    if start_year >= current_year:
        logger.warning("No hay años completos para years=%d", years)
        return []

    start_date = datetime.date(start_year, 1, 1)
    end_date = datetime.date(end_year, 12, 31)

    data = await _fetch_historical_data(lat, lon, start_date, end_date)
    summaries = _parse_historical_response(data, start_year, end_year)

    _historical_cache_set(lat, lon, years, summaries)
    logger.info(
        "Histórico obtenido — years_count=%d rango_inicio=%d rango_fin=%d",
        len(summaries),
        start_year,
        end_year,
    )
    return summaries


async def _fetch_historical_data(
    lat: float, lon: float, start_date: datetime.date, end_date: datetime.date,
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
        "latitude": round(lat, 2),
        "longitude": round(lon, 2),
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
) -> list[HistoricalYearSummary]:
    """Parsea la respuesta de OpenMeteo Archive a resúmenes anuales.

    Agrupa datos diarios por año y calcula promedios y totales.

    Args:
        data: JSON parseado de OpenMeteo Archive.
        start_year: Primer año del rango consultado.
        end_year: Último año del rango consultado.

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

    yearly_data: dict[int, dict[str, list[float]]] = {}
    for i, fecha_str in enumerate(fechas_raw):
        try:
            fecha = datetime.date.fromisoformat(str(fecha_str))
        except ValueError:
            continue

        year = fecha.year
        if year < start_year or year > end_year:
            continue

        if year not in yearly_data:
            yearly_data[year] = {"temp_max": [], "temp_min": [], "precip": []}

        tmax = _safe_float(maxs_raw[i]) if isinstance(maxs_raw, list) and i < len(maxs_raw) else None
        tmin = _safe_float(mins_raw[i]) if isinstance(mins_raw, list) and i < len(mins_raw) else None
        precip = _safe_float(precips_raw[i]) if isinstance(precips_raw, list) and i < len(precips_raw) else None

        if tmax is not None:
            yearly_data[year]["temp_max"].append(tmax)
        if tmin is not None:
            yearly_data[year]["temp_min"].append(tmin)
        if precip is not None:
            yearly_data[year]["precip"].append(precip)

    summaries: list[HistoricalYearSummary] = []
    for year in sorted(yearly_data.keys()):
        yd = yearly_data[year]
        tmax_list = yd["temp_max"]
        tmin_list = yd["temp_min"]
        precip_list = yd["precip"]

        if tmax_list and tmin_list:
            n = min(len(tmax_list), len(tmin_list))
            daily_avgs = [(tmax_list[i] + tmin_list[i]) / 2.0 for i in range(n)]
            temp_promedio = sum(daily_avgs) / n
        else:
            temp_promedio = None

        temp_max_promedio = sum(tmax_list) / len(tmax_list) if tmax_list else None
        temp_min_promedio = sum(tmin_list) / len(tmin_list) if tmin_list else None
        precipitacion_total = sum(precip_list) if precip_list else None

        dias_helada = sum(1 for t in tmin_list if t < 0) if tmin_list else None

        summaries.append(HistoricalYearSummary(
            year=year,
            temp_promedio=temp_promedio,
            temp_max_promedio=temp_max_promedio,
            temp_min_promedio=temp_min_promedio,
            precipitacion_total_mm=precipitacion_total,
            dias_helada=dias_helada,
        ))

    return summaries


def _format_historico_text(
    summaries: list[HistoricalYearSummary],
    comuna: str,
    metrica: str | None = None,
) -> str:
    """Formatea resúmenes anuales a texto natural en español chileno.

    Args:
        summaries: Lista de HistoricalYearSummary ordenada por año.
        comuna: Nombre de la comuna consultada.
        metrica: Métrica opcional ("temperatura", "lluvia", "heladas").

    Returns:
        Texto natural para TTS. Ejemplo:
        "En Traiguén, el año 2025 tuvo temperatura promedio de 12°C,
         con 850mm de lluvia y 15 días de helada, según OpenMeteo."
    """
    if not summaries:
        return f"No hay datos históricos disponibles para {comuna}, según OpenMeteo."

    sorted_sums = sorted(summaries, key=lambda s: s.year)

    partes: list[str] = []
    for s in sorted_sums:
        year_part = f"el año {s.year} tuvo"

        detalles: list[str] = []

        if metrica in (None, "temperatura"):
            if s.temp_promedio is not None:
                detalles.append(f"temperatura promedio de {s.temp_promedio:.0f}°C")
            if s.temp_max_promedio is not None:
                detalles.append(f"máxima promedio de {s.temp_max_promedio:.0f}°C")
            if s.temp_min_promedio is not None:
                detalles.append(f"mínima promedio de {s.temp_min_promedio:.0f}°C")

        if metrica in (None, "lluvia") and s.precipitacion_total_mm is not None:
            if s.precipitacion_total_mm >= 1000:
                detalles.append(
                    f"precipitación total de {s.precipitacion_total_mm / 1000:.1f} metros"
                )
            else:
                detalles.append(f"{s.precipitacion_total_mm:.0f}mm de lluvia")

        if metrica in (None, "heladas") and s.dias_helada is not None:
            if s.dias_helada == 0:
                detalles.append("sin días de helada")
            elif s.dias_helada == 1:
                detalles.append("1 día de helada")
            else:
                detalles.append(f"{s.dias_helada} días de helada")

        if detalles:
            year_text = year_part + " " + ", ".join(detalles)
            partes.append(year_text)

    if not partes:
        return f"No hay datos climáticos históricos disponibles para {comuna}, según OpenMeteo."

    texto = f"En {comuna}, " + ", y ".join(partes) + ", según OpenMeteo."
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
            tramos.append(
                f"máxima de {round(dia.temp_max_c)} grados y mínima de {round(dia.temp_min_c)}"
            )
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


async def get_pronostico(
    comuna: str | None = None,
    dias: int = 2,
    phone_hash: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> str:
    """Tool function para el LLM: pronostico de los proximos dias.

    Responde preguntas como "va a llover manana?" o "como viene el tiempo?".
    Antes de esta tool el LLM solo tenia clima ACTUAL (get_weather) e
    historico, asi que una pregunta sobre manana no se podia contestar con
    datos: es la consulta mas comun del productor antes de decidir si cosecha.

    SOLO INFORMA DATOS. Sin recomendaciones agronomicas: entrega milimetros y
    temperaturas, no dice si regar o cosechar.

    Args:
        comuna: Nombre de la comuna explícita solicitada por el usuario, o
            ``None`` si no especificó ubicación.
        dias: Cuantos dias de pronostico (1 a 3). Por defecto 2.
        phone_hash: Hash HMAC de la identidad; si no se especificó comuna y
            tiene GPS guardado, se consulta esa ubicación.
        lat: Latitud explícita opcional, usada si no hay comuna ni GPS.
        lon: Longitud explícita opcional, usada si no hay comuna ni GPS.

    Returns:
        Texto natural en espanol chileno para TTS.
    """
    coords: tuple[float, float] | None
    if comuna is not None:
        coords = _resolver_comuna(comuna)
        nombre_ubicacion = comuna
    else:
        user_coords = await _get_user_coordinates(phone_hash)
        if user_coords is not None:
            coords = user_coords
            nombre_ubicacion = "tu parcela"
        elif lat is not None and lon is not None:
            coords = (lat, lon)
            nombre_ubicacion = _location_name(lat, lon)
        else:
            coords = (DEFAULT_LAT, DEFAULT_LON)
            nombre_ubicacion = "Traiguén"

    if coords is None:
        unknown_comuna = comuna or "esa ubicación"
        return (
            f"Disculpa, no reconozco la comuna '{unknown_comuna}'. "
            "Puedo consultar Traiguén, Temuco, Padre Las Casas, Lautaro, "
            "Villarrica y otras de la Araucanía, o Santiago. "
            "¿Cuál te interesa?"
        )

    lat, lon = coords
    dias_pedidos = min(max(dias, 1), 3)

    try:
        pronostico = await get_weather_forecast_daily(lat, lon, days=dias_pedidos)
        return _format_pronostico_text(pronostico, nombre_ubicacion)
    except (ConnectionError, RuntimeError, ValueError) as exc:
        logger.warning(
            "Error al consultar pronóstico — error=%s",
            type(exc).__name__,
        )
        return (
            f"No pude consultar el pronóstico de {nombre_ubicacion} ahora. "
            "¿Probamos más tarde?"
        )


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
            return (
                f"No hay datos históricos disponibles para {comuna} "
                "en el último año, según OpenMeteo."
            )
        return _format_historico_text(summaries, comuna, metrica=metrica)
    except (ConnectionError, RuntimeError) as exc:
        logger.warning(
            "Error al consultar histórico — error=%s",
            type(exc).__name__,
        )
        return (
            f"No pude consultar el histórico climático de {comuna} ahora. "
            "¿Probamos más tarde?"
        )
