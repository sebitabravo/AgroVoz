"""Endpoint REST de consulta de clima OpenWeatherMap.

Issue #17: GET /api/v1/weather?lat=X&lon=Y

Defensa en profundidad contra agotamiento de cuota OWM:
  - Cache con TTL 30 min + clave truncada a .2f (~1.1 km)
  - Rate limiter 30 req/min por IP (check_weather_rate_limit)
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.rate_limiter import check_weather_rate_limit
from app.schemas.weather import WeatherResponse
from app.services.weather_service import DEFAULT_LAT, DEFAULT_LON, get_weather_full

logger = logging.getLogger(__name__)

router = APIRouter(tags=["weather"])


@router.get("/weather", response_model=WeatherResponse)
async def get_weather_endpoint(
    lat: float = Query(DEFAULT_LAT, ge=-90.0, le=90.0, description="Latitud"),
    lon: float = Query(DEFAULT_LON, ge=-180.0, le=180.0, description="Longitud"),
    _rate_limit: None = Depends(check_weather_rate_limit),
) -> WeatherResponse:
    """Consulta el clima actual para coordenadas específicas.

    Devuelve datos crudos + texto natural en español chileno listo para TTS.
    Usa cache en memoria con TTL de 30 minutos.

    Sin parámetros, usa las coordenadas default de Traiguén (-38.23, -72.68).
    La lógica de extracción, defaults y decisiones de negocio está centralizada
    en el servicio (get_weather_full).

    Rate limited a 30 req/min por IP para proteger la cuota gratuita de
    OpenWeatherMap (60 req/min). El cache con TTL 30 min y clave truncada
    a 2 decimales (~1.1 km) reduce llamadas reales a OWM aún más.
    """
    try:
        wd = await get_weather_full(lat, lon)
    except ValueError as exc:
        logger.warning("Configuración de clima incompleta: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Servicio de clima no disponible",
            headers={"Retry-After": "3600"},  # 1 hora: requiere config
        ) from exc
    except (ConnectionError, RuntimeError) as exc:
        logger.warning("Error al obtener clima para (%.4f, %.4f): %s", lat, lon, exc)
        raise HTTPException(
            status_code=502,
            detail="Servicio de clima no disponible",
            headers={"Retry-After": "120"},  # 2 min: reintentar pronto
        ) from exc

    return WeatherResponse.model_validate(wd)
