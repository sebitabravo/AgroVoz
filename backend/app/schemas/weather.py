"""Schemas Pydantic para el endpoint de clima OpenWeatherMap.

Issue #17: GET /api/v1/weather?lat=X&lon=Y
"""

from pydantic import BaseModel, Field


class WeatherResponse(BaseModel):
    """Respuesta de clima para coordenadas específicas.

    Incluye datos crudos para el dashboard admin y una versión
    en texto natural lista para ser leída por Piper TTS.
    """

    lat: float = Field(description="Latitud consultada")
    lon: float = Field(description="Longitud consultada")
    location: str = Field(description="Nombre de la ubicación según OpenWeatherMap")
    temperature_c: float | None = Field(
        default=None, description="Temperatura actual en grados Celsius"
    )
    feels_like_c: float | None = Field(
        default=None, description="Sensación térmica en grados Celsius"
    )
    humidity: int | None = Field(
        default=None, description="Humedad relativa en porcentaje (0-100)"
    )
    description: str = Field(description="Descripción del clima en español")
    wind_speed_ms: float | None = Field(
        default=None, description="Velocidad del viento en m/s"
    )
    rain_1h_mm: float | None = Field(
        default=None, description="Lluvia última hora en mm"
    )
    texto: str = Field(description="Texto natural en español chileno para TTS")

    model_config = {"from_attributes": True}
