"""Endpoint REST de consulta de clima OpenWeatherMap.

Issue #17: GET /api/v1/weather?lat=X&lon=Y
Issue #124: GET /api/v1/weather/history?lat=X&lon=Y&years=1

Defensa en profundidad contra agotamiento de cuota OWM:
  - Cache con TTL 30 min + clave truncada a .2f (~1.1 km)
  - Rate limiter 30 req/min por IP (check_weather_rate_limit)
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.rate_limiter import check_weather_rate_limit
from app.schemas.weather import HistoricalWeatherResponse, HistoricalYearSchema, WeatherResponse
from app.services.weather_service import (
    DEFAULT_LAT,
    DEFAULT_LON,
    _format_historico_text,
    _location_name,
    fetch_historico,
    get_weather_full,
)

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


@router.get("/weather/history", response_model=HistoricalWeatherResponse)
async def get_weather_history_endpoint(
    lat: float = Query(DEFAULT_LAT, ge=-90.0, le=90.0, description="Latitud"),
    lon: float = Query(DEFAULT_LON, ge=-180.0, le=180.0, description="Longitud"),
    years: int = Query(1, ge=1, le=5, description="Años completos hacia atrás (1-5)"),
    _rate_limit: None = Depends(check_weather_rate_limit),
) -> HistoricalWeatherResponse:
    """Consulta el histórico climático para coordenadas específicas.

    Devuelve resúmenes anuales con temperatura promedio, máxima, mínima,
    precipitación total y días de helada, más texto natural para TTS.

    Usa OpenMeteo Archive (sin API key). Cache en memoria con TTL 24h
    porque los datos históricos no cambian.

    Args:
        lat: Latitud. Default: Traiguén (-38.23).
        lon: Longitud. Default: Traiguén (-72.68).
        years: Años completos hacia atrás (1-5). Default: 1.
    """
    try:
        summaries = await fetch_historico(lat, lon, years)
    except ValueError as exc:
        logger.warning("Parámetros inválidos para histórico: %s", exc)
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc
    except (ConnectionError, RuntimeError) as exc:
        logger.warning(
            "Error al obtener histórico para (%.4f, %.4f, y=%d): %s",
            lat, lon, years, exc,
        )
        raise HTTPException(
            status_code=502,
            detail="Servicio de clima histórico no disponible",
            headers={"Retry-After": "120"},
        ) from exc

    location = _location_name(lat, lon)

    resumenes = [
        HistoricalYearSchema(
            year=s.year,
            temp_promedio=s.temp_promedio,
            temp_max_promedio=s.temp_max_promedio,
            temp_min_promedio=s.temp_min_promedio,
            precipitacion_total_mm=s.precipitacion_total_mm,
            dias_helada=s.dias_helada,
        )
        for s in summaries
    ]

    texto = _format_historico_text(summaries, location)

    return HistoricalWeatherResponse(
        lat=lat,
        lon=lon,
        years_solicitados=years,
        resumenes=resumenes,
        texto=texto,
    )
