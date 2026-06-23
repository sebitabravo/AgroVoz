"""Endpoint REST de consulta de clima OpenWeatherMap.

Issue #17: GET /api/v1/weather?lat=X&lon=Y
"""

import logging
from typing import cast

from fastapi import APIRouter, HTTPException, Query

from app.schemas.weather import WeatherResponse
from app.services.weather_service import DEFAULT_LAT, DEFAULT_LON, _cache_set, _fetch_weather_data, _format_weather

logger = logging.getLogger(__name__)

router = APIRouter(tags=["weather"])


@router.get("/weather", response_model=WeatherResponse)
async def get_weather_endpoint(
    lat: float = Query(DEFAULT_LAT, ge=-90.0, le=90.0, description="Latitud"),
    lon: float = Query(DEFAULT_LON, ge=-180.0, le=180.0, description="Longitud"),
) -> WeatherResponse:
    """Consulta el clima actual para coordenadas específicas.

    Devuelve datos crudos + texto natural en español chileno listo para TTS.
    Usa cache en memoria con TTL de 30 minutos.

    Sin parámetros, usa las coordenadas default de Traiguén (-38.23, -72.68).
    """
    try:
        data = await _fetch_weather_data(lat, lon)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ConnectionError, RuntimeError) as exc:
        logger.warning("Error al obtener clima para (%.4f, %.4f): %s", lat, lon, exc)
        raise HTTPException(
            status_code=502, detail="Servicio de clima no disponible"
        ) from exc

    main = cast(dict[str, object], data.get("main", {}))
    weather_list = cast(list[dict[str, object]], data.get("weather", []))
    weather = weather_list[0] if weather_list else {}
    wind = cast(dict[str, object], data.get("wind", {}))
    rain = cast(dict[str, object], data.get("rain", {}))
    coord = cast(dict[str, object], data.get("coord", {}))

    texto = _format_weather(data)

    # Poblar cache para que get_weather() del LLM reutilice el dato.
    _cache_set(lat, lon, texto)

    # Extraer valores con cast: la respuesta de OWM tiene tipos predecibles.
    temp_val = cast(float, main.get("temp", 0.0))
    feels_val = cast(float, main.get("feels_like", 0.0))
    hum_val = cast(int, main.get("humidity", 0))
    desc_val = cast(str, weather.get("description", "sin datos"))
    coord_lat = cast(float, coord.get("lat", lat))
    coord_lon = cast(float, coord.get("lon", lon))
    loc_name = cast(str, data.get("name")) or "Desconocido"
    wind_val = cast(float, wind["speed"]) if "speed" in wind else None
    rain_val: float | None = None
    if rain:
        rain_1h = cast(float, rain.get("1h", rain.get("3h", 0.0)))
        rain_val = rain_1h if rain_1h > 0 else None

    return WeatherResponse(
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
