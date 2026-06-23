"""Endpoint REST de consulta de clima OpenWeatherMap.

Issue #17: GET /api/v1/weather?lat=X&lon=Y
"""

import logging

from fastapi import APIRouter, HTTPException, Query

from app.schemas.weather import WeatherResponse
from app.services.weather_service import DEFAULT_LAT, DEFAULT_LON, _fetch_weather_data, get_weather

logger = logging.getLogger(__name__)

router = APIRouter(tags=["weather"])


@router.get("/weather", response_model=WeatherResponse)
async def get_weather_endpoint(
    lat: float = Query(DEFAULT_LAT, description="Latitud"),
    lon: float = Query(DEFAULT_LON, description="Longitud"),
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
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    main = data.get("main", {})
    weather_list = data.get("weather", [])
    weather = weather_list[0] if weather_list else {}
    wind = data.get("wind", {})
    rain = data.get("rain", {})

    texto = await get_weather(lat, lon)

    return WeatherResponse(
        lat=data.get("coord", {}).get("lat", lat),
        lon=data.get("coord", {}).get("lon", lon),
        location=data.get("name", "Desconocido"),
        temperature_c=float(main.get("temp", 0)),
        feels_like_c=float(main.get("feels_like", 0)),
        humidity=int(main.get("humidity", 0)),
        description=weather.get("description", "sin datos"),
        wind_speed_ms=float(wind["speed"]) if "speed" in wind else None,
        rain_1h_mm=(
            float(rain.get("1h", rain.get("3h", 0)))
            if rain
            else None
        ),
        texto=texto,
    )
