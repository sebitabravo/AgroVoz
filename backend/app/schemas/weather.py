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


class HistoricalYearSchema(BaseModel):
    """Resumen climático de un año específico."""

    year: int = Field(description="Año del resumen climático")
    temp_promedio: float | None = Field(
        default=None, description="Temperatura promedio anual en °C"
    )
    temp_max_promedio: float | None = Field(
        default=None, description="Promedio anual de temperatura máxima en °C"
    )
    temp_min_promedio: float | None = Field(
        default=None, description="Promedio anual de temperatura mínima en °C"
    )
    precipitacion_total_mm: float | None = Field(
        default=None, description="Precipitación total anual en mm"
    )
    dias_helada: int | None = Field(
        default=None, description="Días con temperatura mínima bajo 0°C"
    )


class HistoricalWeatherResponse(BaseModel):
    """Respuesta de histórico climático para coordenadas específicas.

    Incluye resúmenes anuales y una versión en texto natural para TTS.
    """

    lat: float = Field(description="Latitud consultada")
    lon: float = Field(description="Longitud consultada")
    years_solicitados: int = Field(
        default=1, description="Cantidad de años solicitados"
    )
    resumenes: list[HistoricalYearSchema] = Field(
        description="Lista de resúmenes climáticos anuales"
    )
    texto: str = Field(
        description="Texto natural en español chileno para TTS"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "lat": -38.23,
                    "lon": -72.68,
                    "years_solicitados": 1,
                    "resumenes": [
                        {
                            "year": 2025,
                            "temp_promedio": 12.5,
                            "temp_max_promedio": 18.2,
                            "temp_min_promedio": 6.8,
                            "precipitacion_total_mm": 850.0,
                            "dias_helada": 15,
                        }
                    ],
                    "texto": (
                        "En Traiguén, el año 2025 tuvo temperatura promedio de 12°C, "
                        "máxima promedio de 18°C, mínima promedio de 7°C, "
                        "850mm de lluvia, 15 días de helada, según OpenMeteo."
                    ),
                }
            ]
        },
    }
