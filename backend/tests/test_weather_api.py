"""Tests para el endpoint GET /api/v1/weather.

Cobertura: 200 con coordenadas default, 200 con coordenadas personalizadas,
502 por error de red/API, 503 por API key faltante.
Mockea _fetch_weather_data y get_weather del servicio (testeado aparte).
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

# ── Fixture de datos JSON que devuelve _fetch_weather_data ─────

_OWM_DATA = {
    "coord": {"lon": -72.68, "lat": -38.23},
    "weather": [{"id": 804, "main": "Clouds", "description": "nublado"}],
    "main": {
        "temp": 18.5,
        "feels_like": 17.2,
        "humidity": 65,
    },
    "wind": {"speed": 3.6, "deg": 180},
    "rain": {"1h": 0.5},
    "clouds": {"all": 90},
    "name": "Traiguén",
}

_TEXTO_ESPERADO = (
    "En Traiguén ahora: 18°C, nublado, humedad 65%, viento 3.6 m/s, lluvia 0.5 mm."
)


# ── AsyncClient helper ─────────────────────────────────────────


@pytest.fixture
def client() -> AsyncClient:
    """Cliente HTTP asíncrono para testear la app FastAPI."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


# ── Tests: GET /api/v1/weather ─────────────────────────────────


class TestWeatherEndpoint:
    """Happy path y errores mapeados a HTTP."""

    async def test_get_weather_default_coords(
        self, client: AsyncClient
    ) -> None:
        """GET sin parámetros usa Traiguén y devuelve WeatherResponse."""
        with (
            patch(
                "app.api.weather._fetch_weather_data",
                AsyncMock(return_value=_OWM_DATA),
            ),
            patch(
                "app.api.weather.get_weather",
                AsyncMock(return_value=_TEXTO_ESPERADO),
            ),
        ):
            response = await client.get("/api/v1/weather")

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
        self, client: AsyncClient
    ) -> None:
        """GET con coordenadas de Santiago consulta esa ubicación."""
        santiago_data = {
            "coord": {"lon": -70.65, "lat": -33.45},
            "weather": [{"description": "soleado"}],
            "main": {"temp": 25.0, "feels_like": 24.0, "humidity": 30},
            "name": "Santiago",
        }
        texto_stgo = "En Santiago ahora: 25°C, soleado, humedad 30%."

        with (
            patch(
                "app.api.weather._fetch_weather_data",
                AsyncMock(return_value=santiago_data),
            ),
            patch(
                "app.api.weather.get_weather",
                AsyncMock(return_value=texto_stgo),
            ),
        ):
            response = await client.get(
                "/api/v1/weather?lat=-33.45&lon=-70.65"
            )

        assert response.status_code == 200
        body = response.json()
        assert body["lat"] == -33.45
        assert body["lon"] == -70.65
        assert body["location"] == "Santiago"
        assert body["texto"] == texto_stgo

    async def test_missing_api_key_returns_503(
        self, client: AsyncClient
    ) -> None:
        """ValueError → HTTP 503 (servicio no configurado)."""
        with patch(
            "app.api.weather._fetch_weather_data",
            AsyncMock(side_effect=ValueError("API key no configurada")),
        ):
            response = await client.get("/api/v1/weather")

        assert response.status_code == 503
        assert "API key" in response.json()["detail"]

    async def test_connection_error_returns_502(
        self, client: AsyncClient
    ) -> None:
        """ConnectionError → HTTP 502 (error de red)."""
        with patch(
            "app.api.weather._fetch_weather_data",
            AsyncMock(side_effect=ConnectionError("Timeout")),
        ):
            response = await client.get("/api/v1/weather")

        assert response.status_code == 502
        assert "Timeout" in response.json()["detail"]

    async def test_runtime_error_returns_502(
        self, client: AsyncClient
    ) -> None:
        """RuntimeError (ej: API key inválida) → HTTP 502."""
        with patch(
            "app.api.weather._fetch_weather_data",
            AsyncMock(side_effect=RuntimeError("API key inválida")),
        ):
            response = await client.get("/api/v1/weather")

        assert response.status_code == 502
        assert "API key inválida" in response.json()["detail"]

    async def test_wind_and_rain_null_when_missing(
        self, client: AsyncClient
    ) -> None:
        """Sin campos wind ni rain → wind_speed_ms y rain_1h_mm son None."""
        data_sin_viento_lluvia = {
            "coord": {"lon": -72.68, "lat": -38.23},
            "weather": [{"description": "cielo claro"}],
            "main": {"temp": 22.0, "feels_like": 21.0, "humidity": 40},
            "name": "Traiguén",
        }

        with (
            patch(
                "app.api.weather._fetch_weather_data",
                AsyncMock(return_value=data_sin_viento_lluvia),
            ),
            patch(
                "app.api.weather.get_weather",
                AsyncMock(return_value="En Traiguén ahora: 22°C, cielo claro, humedad 40%."),
            ),
        ):
            response = await client.get("/api/v1/weather")

        assert response.status_code == 200
        body = response.json()
        assert body["wind_speed_ms"] is None
        assert body["rain_1h_mm"] is None
        assert body["description"] == "cielo claro"
