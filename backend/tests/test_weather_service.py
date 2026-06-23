"""Tests para app.services.weather_service: consulta clima OpenWeatherMap.

Cobertura: _format_weather (respuesta completa, parcial, lluvia, sin viento),
get_weather con mock httpx (happy path, cache, errores), validación lat/lon,
y _clear_cache. Sin red real: MockTransport simula respuestas de OpenWeatherMap.
"""

from collections.abc import Callable

import httpx
import pytest

from app.services.weather_service import (
    _clear_cache,
    _format_weather,
    get_weather,
)

# ── Fixtures de datos OpenWeatherMap ──────────────────────────

# Respuesta completa de OpenWeatherMap para Traiguén.
# Incluye temp, humedad, viento, lluvia, descripción en español.
_OWM_RESPUESTA_COMPLETA = {
    "coord": {"lon": -72.68, "lat": -38.23},
    "weather": [{"id": 804, "main": "Clouds", "description": "nublado"}],
    "main": {
        "temp": 18.5,
        "feels_like": 17.2,
        "temp_min": 15.0,
        "temp_max": 22.0,
        "humidity": 65,
    },
    "wind": {"speed": 3.6, "deg": 180},
    "rain": {"1h": 0.5},
    "clouds": {"all": 90},
    "dt": 1719000000,
    "name": "Traiguén",
}

# Respuesta sin lluvia y sin viento (campos ausentes).
_OWM_SIN_LLUVIA_NI_VIENTO = {
    "coord": {"lon": -72.68, "lat": -38.23},
    "weather": [{"id": 800, "main": "Clear", "description": "cielo claro"}],
    "main": {
        "temp": 22.0,
        "feels_like": 21.0,
        "temp_min": 20.0,
        "temp_max": 25.0,
        "humidity": 40,
    },
    "name": "Traiguén",
}

# Respuesta con datos mínimos (temp y humidity faltantes).
_OWM_DATOS_MINIMOS = {
    "weather": [{"description": "niebla"}],
    "main": {},
    "name": "Lonquimay",
}


# ── Helpers para mock httpx ───────────────────────────────────


def _install_mock_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    """Instala un cliente HTTP mockeado en _http_client.

    Usa MockTransport para simular respuestas de OpenWeatherMap sin red.
    El caller debe cerrar el cliente devuelto al terminar el test.
    """
    transport = httpx.MockTransport(handler)
    mock_client = httpx.AsyncClient(transport=transport)
    monkeypatch.setattr(
        "app.services.weather_service._http_client", mock_client
    )
    return mock_client


# ── Tests: _format_weather ────────────────────────────────────


class TestFormatWeather:
    """Formateo de respuesta JSON OpenWeatherMap a texto natural."""

    def test_respuesta_completa(self) -> None:
        """Con todos los campos: temp, humedad, viento, lluvia."""
        texto = _format_weather(
            temp=18.5, humidity=65, description="nublado",
            wind_speed=3.6, rain_mm=0.5, location="Traiguén",
        )
        assert "Traiguén" in texto
        assert "18°C" in texto
        assert "nublado" in texto
        assert "65%" in texto
        assert "3.6 m/s" in texto
        assert "0.5 mm" in texto

    def test_sin_lluvia_ni_viento(self) -> None:
        """Respuesta sin campos rain ni wind."""
        texto = _format_weather(
            temp=22.0, humidity=40, description="cielo claro",
            location="Traiguén",
        )
        assert "Traiguén" in texto
        assert "22°C" in texto
        assert "cielo claro" in texto
        assert "40%" in texto
        assert "m/s" not in texto  # sin viento
        assert "mm" not in texto  # sin lluvia

    def test_temperatura_redondeada(self) -> None:
        """18.5°C -> '18°C' en el texto."""
        texto = _format_weather(
            temp=18.5, humidity=65, description="nublado",
            location="Traiguén",
        )
        assert "18°C" in texto
        assert "18.5°C" not in texto

    def test_datos_minimos(self) -> None:
        """Respuesta degradada: sin temp ni humidity."""
        texto = _format_weather(
            temp=None, humidity=None, description="niebla",
            location="Lonquimay",
        )
        assert "Lonquimay" in texto
        assert "niebla" in texto

    def test_lista_weather_vacia(self) -> None:
        """weather: [] — texto no incluye descripción de clima."""
        texto = _format_weather(
            temp=12.0, humidity=55, description="sin datos",
            location="Vacio",
        )
        # Solo temp y humedad, sin descripción de clima (weather vacío → description="sin datos" → se omite).
        assert "Vacio" in texto
        assert "12°C" in texto
        assert "55%" in texto
        assert "nublado" not in texto
        assert "soleado" not in texto


# ── Tests: get_weather ────────────────────────────────────────


class TestGetWeather:
    """get_weather con httpx mockeado y cache."""

    def setup_method(self) -> None:
        """Limpia cache y cliente HTTP entre tests."""
        _clear_cache()
        import app.services.weather_service as ws
        ws._http_client = None

    def _mock_client(self, monkeypatch: pytest.MonkeyPatch, json_body: dict[str, object]) -> httpx.AsyncClient:
        """Helper: instala mock de cliente HTTP que responde con json_body."""
        monkeypatch.setattr(
            "app.services.weather_service.settings.openweathermap_api_key", "test-key"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=json_body)

        return _install_mock_client(monkeypatch, handler)

    async def test_get_weather_traiguen_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Consulta con coordenadas default devuelve texto natural."""
        mock_client = self._mock_client(monkeypatch, _OWM_RESPUESTA_COMPLETA)
        try:
            texto = await get_weather()
            assert "Traiguén" in texto
            assert "18°C" in texto
        finally:
            await mock_client.aclose()

    async def test_get_weather_coordenadas_personalizadas(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Consulta con coordenadas de Santiago."""
        mock_client = self._mock_client(monkeypatch, {
            "coord": {"lon": -70.65, "lat": -33.45},
            "weather": [{"description": "soleado"}],
            "main": {"temp": 25.0, "humidity": 30},
            "name": "Santiago",
        })
        try:
            texto = await get_weather(-33.45, -70.65)
            assert "Santiago" in texto
            assert "25°C" in texto
        finally:
            await mock_client.aclose()

    async def test_cache_evita_llamada_repetida(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Segunda llamada con mismas coordenadas usa cache, no llama API."""
        monkeypatch.setattr(
            "app.services.weather_service.settings.openweathermap_api_key", "test-key"
        )
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=_OWM_RESPUESTA_COMPLETA)

        mock_client = _install_mock_client(monkeypatch, handler)
        try:
            texto1 = await get_weather()
            texto2 = await get_weather()
            # Mismo resultado ambas veces.
            assert "Traiguén" in texto1
            assert texto1 == texto2
            # Solo 1 llamada a la API.
            assert call_count == 1
        finally:
            await mock_client.aclose()

    async def test_cache_por_coordenadas_distintas(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Coordenadas distintas = llamadas distintas, sin compartir cache."""
        monkeypatch.setattr(
            "app.services.weather_service.settings.openweathermap_api_key", "test-key"
        )
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            lat = float(request.url.params["lat"])
            if abs(lat - (-38.23)) < 0.01:
                return httpx.Response(200, json=_OWM_RESPUESTA_COMPLETA)
            return httpx.Response(200, json={
                "coord": {"lon": -70.65, "lat": -33.45},
                "weather": [{"description": "soleado"}],
                "main": {"temp": 25.0, "humidity": 30},
                "name": "Santiago",
            })

        mock_client = _install_mock_client(monkeypatch, handler)
        try:
            texto_tgn = await get_weather(-38.23, -72.68)
            texto_stgo = await get_weather(-33.45, -70.65)
            assert "Traiguén" in texto_tgn
            assert "Santiago" in texto_stgo
            assert call_count == 2
        finally:
            await mock_client.aclose()


class TestGetWeatherErrores:
    """get_weather con errores de API. Sin red real."""

    def setup_method(self) -> None:
        _clear_cache()
        import app.services.weather_service as ws
        ws._http_client = None

    def _install_mock(
        self,
        monkeypatch: pytest.MonkeyPatch,
        status: int,
        json_body: dict[str, object] | None = None,
        exc: type[Exception] | None = None,
    ) -> httpx.AsyncClient:
        """Instala mock que responde con status o lanza excepción."""
        monkeypatch.setattr(
            "app.services.weather_service.settings.openweathermap_api_key", "test-key"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if exc:
                raise exc("error simulado")
            return httpx.Response(status, json=json_body or {})

        return _install_mock_client(monkeypatch, handler)

    async def test_api_key_invalida_devuelve_mensaje(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HTTP 401 → mensaje informativo, no excepción."""
        mock_client = self._install_mock(monkeypatch, 401, {"cod": 401, "message": "Invalid API key"})
        try:
            texto = await get_weather()
            assert "no está disponible" in texto
        finally:
            await mock_client.aclose()

    async def test_rate_limit_devuelve_mensaje(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HTTP 429 → mensaje informativo."""
        mock_client = self._install_mock(monkeypatch, 429)
        try:
            texto = await get_weather()
            assert "no está disponible" in texto
        finally:
            await mock_client.aclose()

    async def test_error_red_devuelve_mensaje(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Error de conexión (RequestError) → mensaje amigable."""
        mock_client = self._install_mock(
            monkeypatch, 200, exc=httpx.ConnectError
        )
        try:
            texto = await get_weather()
            assert "No pude consultar el clima" in texto
        finally:
            await mock_client.aclose()

    async def test_respuesta_no_json_devuelve_mensaje(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HTTP 200 con body no-JSON (ej: HTML de proxy/CDN) → mensaje informativo."""
        monkeypatch.setattr(
            "app.services.weather_service.settings.openweathermap_api_key", "test-key"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>502 Proxy Error</html>")

        mock_client = _install_mock_client(monkeypatch, handler)
        try:
            texto = await get_weather()
            assert "no está disponible" in texto
        finally:
            await mock_client.aclose()

    async def test_api_key_no_configurada_devuelve_mensaje(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """API key vacía → ValueError → mensaje informativo."""
        monkeypatch.setattr(
            "app.services.weather_service.settings.openweathermap_api_key", ""
        )
        # Limpiar cache por si otro test guardó algo.
        _clear_cache()
        texto = await get_weather()
        assert "no está configurado" in texto

    # ── Validación de rango lat/lon (defensa en profundidad) ────

    async def test_latitud_invalida_devuelve_mensaje(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Latitud > 90° retorna mensaje sin llamar a la API."""
        monkeypatch.setattr(
            "app.services.weather_service.settings.openweathermap_api_key", "test-key"
        )
        texto = await get_weather(lat=91.0, lon=-70.0)
        assert "latitud" in texto.lower()

    async def test_longitud_invalida_devuelve_mensaje(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Longitud > 180° retorna mensaje sin llamar a la API."""
        monkeypatch.setattr(
            "app.services.weather_service.settings.openweathermap_api_key", "test-key"
        )
        texto = await get_weather(lat=-33.0, lon=181.0)
        assert "longitud" in texto.lower()


class TestClearCache:
    """_clear_cache para aislamiento de tests."""

    def setup_method(self) -> None:
        _clear_cache()
        import app.services.weather_service as ws
        ws._http_client = None

    async def test_clear_cache_funciona(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Después de clear_cache, la siguiente llamada va a API."""
        monkeypatch.setattr(
            "app.services.weather_service.settings.openweathermap_api_key", "test-key"
        )
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=_OWM_RESPUESTA_COMPLETA)

        mock_client = _install_mock_client(monkeypatch, handler)
        try:
            await get_weather()
            assert call_count == 1
            _clear_cache()
            await get_weather()
            assert call_count == 2  # cache limpio → nueva llamada
        finally:
            await mock_client.aclose()
