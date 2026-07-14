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

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "examples": [
                {
                    "lat": -38.23,
                    "lon": -72.68,
                    "location": "Traiguén, Araucanía",
                    "temperature_c": 18.5,
                    "feels_like_c": 17.2,
                    "humidity": 75,
                    "description": "parcialmente nublado",
                    "wind_speed_ms": 3.6,
                    "rain_1h_mm": 0.0,
                    "texto": "En Traiguén hay 18 grados, parcialmente nublado, humedad 75%, viento 3.6 m/s.",
                }
            ]
        },
    }
