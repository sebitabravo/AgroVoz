"""Endpoint REST de consulta de clima OpenWeatherMap.

Issue #17: GET /api/v1/weather?lat=X&lon=Y
"""

import logging

from fastapi import APIRouter, HTTPException, Query

from app.schemas.weather import WeatherResponse
from app.services.weather_service import DEFAULT_LAT, DEFAULT_LON, get_weather_full

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
    La lógica de extracción, defaults y decisiones de negocio está centralizada
    en el servicio (get_weather_full).
    """
    try:
        wd = await get_weather_full(lat, lon)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ConnectionError, RuntimeError) as exc:
        logger.warning("Error al obtener clima para (%.4f, %.4f): %s", lat, lon, exc)
        raise HTTPException(
            status_code=502, detail="Servicio de clima no disponible"
        ) from exc

    return WeatherResponse(
        lat=wd.lat,
        lon=wd.lon,
        location=wd.location,
        temperature_c=wd.temperature_c,
        feels_like_c=wd.feels_like_c,
        humidity=wd.humidity,
        description=wd.description,
        wind_speed_ms=wd.wind_speed_ms,
        rain_1h_mm=wd.rain_1h_mm,
        texto=wd.texto,
    )
