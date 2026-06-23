"""Modelo Pydantic v2 para la respuesta de OpenWeatherMap Current Weather Data API.

Issue #17: valida y tipa la respuesta JSON de OWM antes de extraerla a WeatherData.
Modelo INTERNO — no se expone en la API pública. Reemplaza el Mapping[str, object]
con cast() que se usaba antes, eliminando riesgos de null-propagación silenciosa.

Campos que OWM puede enviar como null (main, weather, wind, rain, coord) se validan
con Optional — si OWM cambia y empieza a mandar null en vez de omitir, Pydantic lo
rechaza en vez de propagar None al downstream.
"""

from pydantic import BaseModel, Field


class OWMCoord(BaseModel):
    """Coordenadas geográficas de la estación meteorológica."""

    lat: float | None = None
    lon: float | None = None


class OWMWeatherItem(BaseModel):
    """Un item dentro del array weather[] (puede haber varios)."""

    id: int | None = None
    main: str | None = None
    description: str | None = None
    icon: str | None = None


class OWMMain(BaseModel):
    """Bloque main: temperatura, humedad, presión atmosférica."""

    temp: float | None = None
    feels_like: float | None = None
    temp_min: float | None = None
    temp_max: float | None = None
    humidity: int | None = None
    pressure: int | None = None
    sea_level: int | None = None
    grnd_level: int | None = None


class OWMWind(BaseModel):
    """Bloque wind: velocidad, dirección, ráfagas de viento."""

    speed: float | None = None
    deg: int | None = None
    gust: float | None = None


class OWMResponse(BaseModel):
    """Respuesta completa de OpenWeatherMap Current Weather Data API.

    Valida estructura y tipos antes de que _extract_weather_data() extraiga
    los campos a WeatherData. Campos desconocidos (dt, sys, clouds, timezone,
    id, cod) se ignoran con extra="ignore".

    Los dict[str, float] de rain garantizan que Pydantic rechace valores null
    dentro del dict (ej: {"1h": null}) — si OWM cambiara el formato, la
    validación falla con ValidationError, que _fetch_weather_data captura y
    convierte en RuntimeError, en vez de propagar None silenciosamente.
    """

    coord: OWMCoord | None = None
    weather: list[OWMWeatherItem] = Field(default_factory=list)
    main: OWMMain | None = None
    wind: OWMWind | None = None
    rain: dict[str, float] | None = None  # keys "1h" o "3h", valores siempre float
    name: str | None = None

    model_config = {"extra": "ignore"}
