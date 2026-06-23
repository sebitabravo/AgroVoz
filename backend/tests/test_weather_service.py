"""Tests para app.services.weather_service: consulta clima OpenWeatherMap.

Cobertura: _format_weather (respuesta completa, parcial, lluvia, sin viento),
_extract_weather_data con OWMResponse (null main, rain 3h, coord fallback,
weather vacío), get_weather con mock httpx (happy path, cache, errores),
validación lat/lon, cache eviction, Pydantic validation rejection,
y _clear_cache. Sin red real: MockTransport simula respuestas de OpenWeatherMap.
"""

from collections.abc import Callable

import httpx
import pytest

from app.schemas.openweathermap import (
    OWMCoord,
    OWMMain,
    OWMResponse,
    OWMWeatherItem,
    OWMWind,
)
from app.services.weather_service import (
    _clear_cache,
    _extract_weather_data,
    _format_weather,
    get_weather,
    get_weather_full,
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


# ── Tests: _extract_weather_data con OWMResponse ───────────────


class TestExtractWeatherData:
    """Extracción desde OWMResponse validado por Pydantic a WeatherData."""

    def test_extraccion_respuesta_completa(self) -> None:
        """OWMResponse con todos los campos produce WeatherData completo."""
        owm = OWMResponse(
            coord=OWMCoord(lat=-38.23, lon=-72.68),
            weather=[OWMWeatherItem(id=804, main="Clouds", description="nublado")],
            main=OWMMain(temp=18.5, feels_like=17.2, humidity=65),
            wind=OWMWind(speed=3.6, deg=180),
            rain={"1h": 0.5},
            name="Traiguén",
        )

        wd = _extract_weather_data(owm, -38.23, -72.68)

        assert wd.lat == -38.23
        assert wd.lon == -72.68
        assert wd.location == "Traiguén"
        assert wd.temperature_c == 18.5
        assert wd.feels_like_c == 17.2
        assert wd.humidity == 65
        assert wd.description == "nublado"
        assert wd.wind_speed_ms == 3.6
        assert wd.rain_1h_mm == 0.5
        assert "18°C" in wd.texto
        assert "nublado" in wd.texto

    def test_main_none_produce_nulls(self) -> None:
        """OWMResponse con main=None → temp, feels_like, humidity son None."""
        owm = OWMResponse(
            weather=[OWMWeatherItem(description="niebla")],
            main=None,
            name="Lonquimay",
        )

        wd = _extract_weather_data(owm, -38.5, -72.0)

        assert wd.temperature_c is None
        assert wd.feels_like_c is None
        assert wd.humidity is None
        assert "temperatura no disponible" in wd.texto

    def test_weather_list_vacia_produce_sin_datos(self) -> None:
        """weather=[] → description="sin datos"."""
        owm = OWMResponse(
            weather=[],
            main=OWMMain(temp=15.0, humidity=50),
            name="Vacío",
        )

        wd = _extract_weather_data(owm, -33.0, -70.0)

        assert wd.description == "sin datos"

    def test_weather_description_none_produce_sin_datos(self) -> None:
        """weather[0].description=None → fallback a "sin datos"."""
        owm = OWMResponse(
            weather=[OWMWeatherItem(id=800, description=None)],
            main=OWMMain(temp=15.0, humidity=50),
            name="Nublado",
        )

        wd = _extract_weather_data(owm, -33.0, -70.0)

        assert wd.description == "sin datos"

    def test_coord_null_usa_fallback(self) -> None:
        """coord=None → usa lat/lon del argumento (fallback)."""
        owm = OWMResponse(
            coord=None,
            weather=[OWMWeatherItem(description="soleado")],
            main=OWMMain(temp=20.0, humidity=40),
            name="SinCoord",
        )

        wd = _extract_weather_data(owm, -40.0, -73.0)

        assert wd.lat == -40.0
        assert wd.lon == -73.0

    def test_coord_lat_none_usa_fallback(self) -> None:
        """coord.lat=None → usa lat del argumento."""
        owm = OWMResponse(
            coord=OWMCoord(lat=None, lon=-72.68),
            weather=[OWMWeatherItem(description="nublado")],
            main=OWMMain(temp=18.0, humidity=60),
            name="SinLat",
        )

        wd = _extract_weather_data(owm, -38.23, -72.68)

        assert wd.lat == -38.23
        assert wd.lon == -72.68

    def test_wind_none_produce_null(self) -> None:
        """wind=None → wind_speed_ms=None."""
        owm = OWMResponse(
            weather=[OWMWeatherItem(description="calma")],
            main=OWMMain(temp=22.0, humidity=30),
            wind=None,
            name="SinViento",
        )

        wd = _extract_weather_data(owm, -33.0, -70.0)

        assert wd.wind_speed_ms is None

    def test_wind_speed_none_produce_null(self) -> None:
        """wind.speed=None → wind_speed_ms=None."""
        owm = OWMResponse(
            weather=[OWMWeatherItem(description="ventoso")],
            main=OWMMain(temp=15.0, humidity=55),
            wind=OWMWind(speed=None, deg=180),
            name="VientoNull",
        )

        wd = _extract_weather_data(owm, -33.0, -70.0)

        assert wd.wind_speed_ms is None

    def test_rain_none_produce_null(self) -> None:
        """rain=None → rain_1h_mm=None."""
        owm = OWMResponse(
            weather=[OWMWeatherItem(description="seco")],
            main=OWMMain(temp=28.0, humidity=20),
            rain=None,
            name="SinLluvia",
        )

        wd = _extract_weather_data(owm, -33.0, -70.0)

        assert wd.rain_1h_mm is None

    def test_rain_con_3h_en_vez_de_1h(self) -> None:
        """rain solo tiene key '3h' → extrae de '3h'."""
        owm = OWMResponse(
            weather=[OWMWeatherItem(description="lluvioso")],
            main=OWMMain(temp=14.0, humidity=80),
            rain={"3h": 2.5},
            name="Lluvia3h",
        )

        wd = _extract_weather_data(owm, -38.0, -72.0)

        assert wd.rain_1h_mm == 2.5
        assert "2.5 mm" in wd.texto

    def test_rain_cero_no_se_reporta(self) -> None:
        """rain={'1h': 0.0} → rain_1h_mm=None (umbral > 0)."""
        owm = OWMResponse(
            weather=[OWMWeatherItem(description="nublado")],
            main=OWMMain(temp=16.0, humidity=60),
            rain={"1h": 0.0},
            name="Traiguén",
        )

        wd = _extract_weather_data(owm, -38.23, -72.68)

        assert wd.rain_1h_mm is None
        assert "mm" not in wd.texto  # sin mención de lluvia

    def test_name_none_produce_desconocido(self) -> None:
        """name=None → location='Desconocido'."""
        owm = OWMResponse(
            weather=[OWMWeatherItem(description="soleado")],
            main=OWMMain(temp=20.0, humidity=50),
            name=None,
        )

        wd = _extract_weather_data(owm, -35.0, -71.0)

        assert wd.location == "Desconocido"


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

    async def test_respuesta_null_en_rain_es_rechazada(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OWM responde rain={"1h": null} → Pydantic ValidationError → RuntimeError."""
        monkeypatch.setattr(
            "app.services.weather_service.settings.openweathermap_api_key", "test-key"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"rain": {"1h": None}})

        mock_client = _install_mock_client(monkeypatch, handler)
        try:
            texto = await get_weather()
            assert "no está disponible" in texto
        finally:
            await mock_client.aclose()

    async def test_respuesta_rain_malformado_es_rechazado(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OWM responde rain={"1h": "mucho"} → str no es float → Pydantic ValidationError."""
        monkeypatch.setattr(
            "app.services.weather_service.settings.openweathermap_api_key", "test-key"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "main": {"temp": 18.0, "humidity": 50},
                "weather": [{"description": "nublado"}],
                "rain": {"1h": "mucho"},  # string donde se espera float
            })

        mock_client = _install_mock_client(monkeypatch, handler)
        try:
            texto = await get_weather()
            assert "no está disponible" in texto
        finally:
            await mock_client.aclose()


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

    async def test_cache_eviction_excede_max_size(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cache con > _CACHE_MAX_SIZE entradas evicta la más antigua."""
        monkeypatch.setattr(
            "app.services.weather_service.settings.openweathermap_api_key", "test-key"
        )
        # Forzar _CACHE_MAX_SIZE a 3 para el test.
        monkeypatch.setattr("app.services.weather_service._CACHE_MAX_SIZE", 3)

        def handler(request: httpx.Request) -> httpx.Response:
            lat = float(request.url.params["lat"])
            lon = float(request.url.params["lon"])
            return httpx.Response(200, json={
                "coord": {"lon": lon, "lat": lat},
                "weather": [{"description": "nublado"}],
                "main": {"temp": 18.0, "humidity": 60},
                "name": f"Lugar {lat}",
            })

        mock_client = _install_mock_client(monkeypatch, handler)
        try:
            import app.services.weather_service as ws

            # Insertar 4 entradas con coordenadas distintas.
            # Cada _cache_key usa 6 decimales de precisión.
            await get_weather_full(-38.23, -72.68)  # Traiguén (1ª entrada → será evictada)
            await get_weather_full(-33.45, -70.65)  # Santiago (2ª)
            await get_weather_full(-36.82, -73.05)  # Concepción (3ª)
            await get_weather_full(-53.15, -70.90)  # Punta Arenas (4ª → evicta 1ª)

            assert len(ws._cache) == 3

            # La clave de Traiguén fue evictada por ser la más antigua.
            traiguen_key = ws._cache_key(-38.23, -72.68)
            assert traiguen_key not in ws._cache

            # Las otras 3 permanecen.
            assert ws._cache_key(-33.45, -70.65) in ws._cache
            assert ws._cache_key(-36.82, -73.05) in ws._cache
            assert ws._cache_key(-53.15, -70.90) in ws._cache
        finally:
            await mock_client.aclose()

    async def test_cache_ttl_expira_y_refresca(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Entrada de cache expira tras _CACHE_TTL_SECONDS y se refresca vía API."""
        monkeypatch.setattr(
            "app.services.weather_service.settings.openweathermap_api_key", "test-key"
        )

        # Reloj fake: lista mutable para que el handler y _cache_* compartan
        # la misma referencia. time.monotonic() retorna t[0].
        t = [1000.0]
        monkeypatch.setattr("app.services.weather_service.time.monotonic", lambda: t[0])

        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=_OWM_RESPUESTA_COMPLETA)

        mock_client = _install_mock_client(monkeypatch, handler)
        try:
            # 1ª llamada: cache miss → API
            await get_weather()
            assert call_count == 1

            # 2ª llamada inmediata: cache hit → sin API
            await get_weather()
            assert call_count == 1

            # Avanzar 31 minutos → TTL expirado (30 min)
            t[0] = 1000.0 + 31 * 60

            # 3ª llamada: cache expirado → nueva API
            await get_weather()
            assert call_count == 2
        finally:
            await mock_client.aclose()
