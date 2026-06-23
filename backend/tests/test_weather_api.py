"""Tests para el endpoint GET /api/v1/weather.

Cobertura: 200 con coordenadas default, 200 con coordenadas personalizadas,
502 por error de red/API, 503 por API key faltante.
Mockea get_weather_full del servicio (testeado aparte).
"""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.weather_service import WeatherData

# ── Fixture de datos estructurados que devuelve get_weather_full ─

_TEXTO_ESPERADO = (
    "En Traiguén ahora: 18°C, nublado, humedad 65%, viento 3.6 m/s, lluvia 0.5 mm."
)


def _weather_data_para(
    lat: float = -38.23,
    lon: float = -72.68,
    location: str = "Traiguén",
    temperature_c: float | None = 18.5,
    feels_like_c: float | None = 17.2,
    humidity: int | None = 65,
    description: str = "nublado",
    wind_speed_ms: float | None = 3.6,
    rain_1h_mm: float | None = 0.5,
    texto: str = _TEXTO_ESPERADO,
) -> WeatherData:
    """Helper: construye un WeatherData con defaults de test.

    Usa texto precomputado como default — NO reimplementa _format_weather().
    Tests que necesiten un texto distinto lo pasan explícitamente.
    """
    return WeatherData(
        lat=lat,
        lon=lon,
        location=location,
        temperature_c=temperature_c,
        feels_like_c=feels_like_c,
        humidity=humidity,
        description=description,
        wind_speed_ms=wind_speed_ms,
        rain_1h_mm=rain_1h_mm,
        texto=texto,
    )


# ── AsyncClient helper ─────────────────────────────────────────


@pytest_asyncio.fixture
async def weather_client() -> AsyncIterator[AsyncClient]:
    """Cliente HTTP asíncrono para testear la app FastAPI. Nombrado distinto
    del fixture client en conftest.py para evitar colisión."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


# ── Tests: GET /api/v1/weather ─────────────────────────────────


class TestWeatherEndpoint:
    """Happy path y errores mapeados a HTTP."""

    async def test_get_weather_default_coords(
        self, weather_client: AsyncClient
    ) -> None:
        """GET sin parámetros usa Traiguén y devuelve WeatherResponse."""
        wd = _weather_data_para()

        with patch(
            "app.api.weather.get_weather_full",
            AsyncMock(return_value=wd),
        ):
            response = await weather_client.get("/api/v1/weather")

        assert response.status_code == 200
        body = response.json()
        assert body["lat"] == -38.23
        assert body["lon"] == -72.68
        assert body["location"] == "Traiguén"
        assert body["temperature_c"] == 18.5
        assert body["feels_like_c"] == 17.2
        assert body["humidity"] == 65
        assert body["description"] == "nublado"
        assert body["wind_speed_ms"] == 3.6
        assert body["rain_1h_mm"] == 0.5
        assert body["texto"] == _TEXTO_ESPERADO

    async def test_get_weather_custom_coords(
        self, weather_client: AsyncClient
    ) -> None:
        """GET con coordenadas de Santiago consulta esa ubicación."""
        texto_stgo = "En Santiago ahora: 25°C, soleado, humedad 30%."

        wd = _weather_data_para(
            lat=-33.45,
            lon=-70.65,
            location="Santiago",
            temperature_c=25.0,
            feels_like_c=24.0,
            humidity=30,
            description="soleado",
            wind_speed_ms=None,
            rain_1h_mm=None,
            texto=texto_stgo,
        )

        with patch(
            "app.api.weather.get_weather_full",
            AsyncMock(return_value=wd),
        ):
            response = await weather_client.get(
                "/api/v1/weather?lat=-33.45&lon=-70.65"
            )

        assert response.status_code == 200
        body = response.json()
        assert body["lat"] == -33.45
        assert body["lon"] == -70.65
        assert body["location"] == "Santiago"
        assert body["texto"] == texto_stgo

    async def test_missing_api_key_returns_503(
        self, weather_client: AsyncClient
    ) -> None:
        """ValueError → HTTP 503 (servicio no configurado)."""
        with patch(
            "app.api.weather.get_weather_full",
            AsyncMock(side_effect=ValueError("API key no configurada")),
        ):
            response = await weather_client.get("/api/v1/weather")

        assert response.status_code == 503
        assert "no disponible" in response.json()["detail"]

    async def test_connection_error_returns_502(
        self, weather_client: AsyncClient
    ) -> None:
        """ConnectionError → HTTP 502 (error de red)."""
        with patch(
            "app.api.weather.get_weather_full",
            AsyncMock(side_effect=ConnectionError("Timeout")),
        ):
            response = await weather_client.get("/api/v1/weather")

        assert response.status_code == 502
        assert "no disponible" in response.json()["detail"]

    async def test_runtime_error_returns_502(
        self, weather_client: AsyncClient
    ) -> None:
        """RuntimeError (ej: API key inválida) → HTTP 502."""
        with patch(
            "app.api.weather.get_weather_full",
            AsyncMock(side_effect=RuntimeError("API key inválida")),
        ):
            response = await weather_client.get("/api/v1/weather")

        assert response.status_code == 502
        assert "no disponible" in response.json()["detail"]

    async def test_wind_and_rain_null_when_missing(
        self, weather_client: AsyncClient
    ) -> None:
        """Sin campos wind ni rain → wind_speed_ms y rain_1h_mm son None."""
        wd = _weather_data_para(
            temperature_c=22.0,
            feels_like_c=21.0,
            humidity=40,
            description="cielo claro",
            wind_speed_ms=None,
            rain_1h_mm=None,
        )

        with patch(
            "app.api.weather.get_weather_full",
            AsyncMock(return_value=wd),
        ):
            response = await weather_client.get("/api/v1/weather")

        assert response.status_code == 200
        body = response.json()
        assert body["wind_speed_ms"] is None
        assert body["rain_1h_mm"] is None
        assert body["description"] == "cielo claro"
