"""Tests para WeatherSlidingWindow y check_weather_rate_limit.

Cobertura:
  - Unit: permite 30 req, bloquea 31, buckets independientes por IP,
    limpieza de IPs inactivas, reset, Retry-After correcto.
  - Integracion: 31 requests al endpoint -> ultimo retorna 429 con Retry-After.
"""

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.rate_limiter import WeatherSlidingWindow, _weather_limiter, check_weather_rate_limit
from app.main import app
from app.services.weather_service import WeatherData


# ── Fixtures ───────────────────────────────────────────────────────


def _weather_data_mock() -> WeatherData:
    """WeatherData de prueba para mockear get_weather_full."""
    return WeatherData(
        lat=-38.23,
        lon=-72.68,
        location="Traiguen",
        temperature_c=18.5,
        feels_like_c=17.2,
        humidity=65,
        description="nublado",
        wind_speed_ms=3.6,
        rain_1h_mm=0.5,
        texto="En Traiguen ahora: 18C, nublado, humedad 65%, viento 3.6 m/s, lluvia 0.5 mm.",
    )


@pytest.fixture
def _desactivar_override() -> Iterator[None]:
    """Elimina dependency_overrides para que el rate limiter funcione.

    test_weather_api.py sobreescribe check_weather_rate_limit con lambda: None
    a nivel de modulo. Esta fixture restaura el rate limiter real y resetea
    los contadores entre tests.
    """
    saved = app.dependency_overrides.pop(check_weather_rate_limit, None)
    _weather_limiter.reset()
    yield
    if saved is not None:
        app.dependency_overrides[check_weather_rate_limit] = saved


# ── Unit tests: WeatherSlidingWindow ───────────────────────────────


class TestWeatherSlidingWindow:
    """Tests unitarios del rate limiter con sliding window por IP."""

    def test_permite_primeros_30_requests(self) -> None:
        """30 requests desde la misma IP deben ser permitidos."""
        limiter = WeatherSlidingWindow()
        now = 1000.0
        for i in range(30):
            retry = limiter.check("10.0.0.1", now=now + i * 0.1)
            assert retry is None, f"Request {i + 1} fue bloqueado inesperadamente"

    def test_bloquea_request_31(self) -> None:
        """El 31er request desde la misma IP debe ser bloqueado con Retry-After."""
        limiter = WeatherSlidingWindow()
        now = 1000.0
        for i in range(30):
            retry = limiter.check("10.0.0.2", now=now + i * 0.1)
            assert retry is None, f"Request {i + 1} fue bloqueado inesperadamente"

        retry_after = limiter.check("10.0.0.2", now=now + 30 * 0.1)
        assert retry_after is not None, "El request 31 deberia haber sido bloqueado"
        assert retry_after > 0, f"Retry-After debe ser > 0, fue {retry_after}"

    def test_ips_independientes(self) -> None:
        """Dos IPs distintas tienen buckets independientes."""
        limiter = WeatherSlidingWindow()
        now = 1000.0

        # IP 1: 30 requests -> todos permitidos
        for i in range(30):
            assert limiter.check("10.0.0.1", now=now + i * 0.1) is None

        # IP 2: 30 requests -> tambien permitidos (bucket separado)
        for i in range(30):
            assert limiter.check("10.0.0.2", now=now + i * 0.1) is None

        # IP 1: 31er -> bloqueado
        assert limiter.check("10.0.0.1", now=now + 30.0) is not None

    def test_ventana_deslizante_libera_slots(self) -> None:
        """Timestamps viejos salen de la ventana y liberan slots."""
        limiter = WeatherSlidingWindow()
        # 30 requests en t=1000
        for i in range(30):
            assert limiter.check("10.0.0.1", now=1000.0 + i * 0.01) is None

        # El 31er es bloqueado
        assert limiter.check("10.0.0.1", now=1000.3) is not None

        # Avanzamos 61 segundos -> la ventana de 60s ya paso, todos los slots libres
        assert limiter.check("10.0.0.1", now=1061.0) is None

    def test_retry_after_correcto(self) -> None:
        """Retry-After debe ser el tiempo hasta que el request mas antiguo expire."""
        limiter = WeatherSlidingWindow()
        # 30 requests en t=1000, espaciados 0.5s -> ultimo en t=1014.5
        now = 1000.0
        for i in range(30):
            assert limiter.check("10.0.0.3", now=now + i * 0.5) is None

        # Bloqueado en t=1015.0. El mas antiguo es de t=1000.0 -> expira en t=1060.0
        # Retry-After = 1060.0 - 1015.0 = 45.0
        retry_after = limiter.check("10.0.0.3", now=1015.0)
        assert retry_after is not None
        # ~45s con margen de +-1s por rounding
        assert 44.0 <= retry_after <= 46.0, f"Retry-After esperado ~45.0, fue {retry_after}"

    def test_limpieza_ips_inactivas(self) -> None:
        """IPs sin actividad en la ventana son eliminadas del diccionario."""
        limiter = WeatherSlidingWindow()
        limiter.check("10.0.0.1", now=1000.0)
        limiter.check("10.0.0.2", now=1000.0)

        assert "10.0.0.1" in limiter._requests
        assert "10.0.0.2" in limiter._requests

        # Avanzar 61s. La IP 10.0.0.1 hace otro request (mantiene entrada viva).
        # La IP 10.0.0.2 no hace nada -> debe ser limpiada.
        limiter.check("10.0.0.1", now=1061.0)

        assert "10.0.0.1" in limiter._requests
        assert "10.0.0.2" not in limiter._requests, "IP inactiva deberia haber sido limpiada"

    def test_reset_limpia_todo(self) -> None:
        """reset() debe vaciar contadores y timestamp de limpieza."""
        limiter = WeatherSlidingWindow()
        for i in range(15):
            limiter.check("10.0.0.1", now=1000.0 + i)
        limiter._last_cleanup = 5000.0

        limiter.reset()

        assert len(limiter._requests) == 0
        assert limiter._last_cleanup == 0.0

    def test_usa_time_monotonic_por_defecto(self) -> None:
        """Si no se inyecta `now`, debe usar time.monotonic()."""
        limiter = WeatherSlidingWindow()
        retry = limiter.check("10.0.0.99")
        assert retry is None


# ── Tests de integracion con FastAPI ───────────────────────────────


class TestWeatherRateLimitIntegration:
    """Tests de integracion: rate limiter en el endpoint real."""

    async def test_30_requests_permitidos_31_bloqueado(
        self, _desactivar_override: None
    ) -> None:
        """30 requests -> 200 OK. El 31 -> 429 con Retry-After."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            with patch(
                "app.api.weather.get_weather_full",
                AsyncMock(return_value=_weather_data_mock()),
            ):
                # 30 requests -> todos 200
                for i in range(30):
                    response = await client.get("/api/v1/weather")
                    assert response.status_code == 200, (
                        f"Request {i + 1} esperaba 200, obtuvo {response.status_code}"
                    )

                # Request 31 -> 429
                response = await client.get("/api/v1/weather")
                assert response.status_code == 429, (
                    f"Request 31 esperaba 429, obtuvo {response.status_code}"
                )
                assert "Retry-After" in response.headers
                retry_after = int(response.headers["Retry-After"])
                assert retry_after > 0
