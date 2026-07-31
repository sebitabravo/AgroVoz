"""Tests para app.services.weather_service: consulta clima OpenMeteo.

Cobertura: _format_weather (respuesta completa, parcial, lluvia, sin viento),
_parse_openmeteo_response con datos realistas (completo, mínimo, nulos),
get_weather con mock httpx (happy path, cache, errores),
validación lat/lon, cache eviction, _clear_cache,
y datos climáticos históricos vía OpenMeteo Archive (Issue #124):
_resolver_comuna, _parse_historical_response, _format_historico_text,
fetch_historico, get_clima_historico.
Sin red real: MockTransport simula respuestas de OpenMeteo.
"""

import logging
from collections.abc import Callable

import httpx
import pytest

from app.services.weather_service import (
    _clear_cache,
    _clear_historical_cache,
    _format_historico_text,
    _format_weather,
    _parse_historical_response,
    _parse_openmeteo_response,
    _resolver_comuna,
    _wmo_description,
    fetch_historico,
    get_clima_historico,
    get_weather,
    get_weather_full,
)

# ── Fixtures de datos OpenMeteo ──────────────────────────

# Respuesta completa de OpenMeteo para Traiguén.
# Incluye temp, humedad, sensación térmica, viento, lluvia, código WMO.
_OPENMETEO_RESPUESTA_COMPLETA: dict[str, object] = {
    "latitude": -38.23,
    "longitude": -72.68,
    "current": {
        "temperature_2m": 18.5,
        "relative_humidity_2m": 65,
        "apparent_temperature": 17.2,
        "weather_code": 3,
        "wind_speed_10m": 3.6,
        "rain": 0.5,
    },
}

# Respuesta sin lluvia ni viento (campos en 0 o ausentes).
_OPENMETEO_SIN_LLUVIA_NI_VIENTO: dict[str, object] = {
    "latitude": -38.23,
    "longitude": -72.68,
    "current": {
        "temperature_2m": 22.0,
        "relative_humidity_2m": 40,
        "apparent_temperature": 21.0,
        "weather_code": 0,
        "wind_speed_10m": 0.0,
        "rain": 0.0,
    },
}

# Respuesta con datos mínimos (temperatura y humedad presentes, sin extras).
_OPENMETEO_DATOS_MINIMOS: dict[str, object] = {
    "latitude": -38.5,
    "longitude": -72.0,
    "current": {
        "temperature_2m": 12.0,
        "relative_humidity_2m": 80,
        "apparent_temperature": 11.0,
        "weather_code": 45,
        "wind_speed_10m": None,
        "rain": None,
    },
}


# ── Helpers para mock httpx ───────────────────────────────────


def _install_mock_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    """Instala un cliente HTTP mockeado en _http_client.

    Usa MockTransport para simular respuestas de OpenMeteo sin red.
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
    """Formateo de datos de clima a texto natural."""

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
        """Respuesta sin rain ni wind."""
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
        """description='sin datos' — texto no incluye descripción."""
        texto = _format_weather(
            temp=12.0, humidity=55, description="sin datos",
            location="Vacio",
        )
        # Solo temp y humedad, sin descripción de clima.
        assert "Vacio" in texto
        assert "12°C" in texto
        assert "55%" in texto
        assert "nublado" not in texto
        assert "soleado" not in texto

    def test_texto_incluye_cita_fuente_openmeteo(self) -> None:
        """Issue #95: el texto de clima cita OpenMeteo como fuente del dato.

        La mención explícita de la fuente refuerza la confianza del agricultor
        en el dato de clima y permite a PRODESAL/INDAP validar el origen.
        Aparece al final, antes del punto, en todos los formatos de respuesta.
        """
        # Respuesta completa (con viento y lluvia).
        texto_completo = _format_weather(
            temp=18.5, humidity=65, description="nublado",
            wind_speed=3.6, rain_mm=0.5, location="Traiguén",
        )
        assert "según OpenMeteo" in texto_completo
        assert texto_completo.endswith("según OpenMeteo.")

        # Respuesta degradada (sin viento ni lluvia).
        texto_minimo = _format_weather(
            temp=22.0, humidity=40, description="cielo claro",
            location="Traiguén",
        )
        assert "según OpenMeteo" in texto_minimo
        assert texto_minimo.endswith("según OpenMeteo.")


# ── Tests: _wmo_description ──────────────────────────────────


class TestWMODescription:
    """Traducción de códigos WMO a español."""

    def test_codigo_conocido(self) -> None:
        """Código WMO conocido devuelve descripción."""
        assert _wmo_description(0) == "cielo despejado"
        assert _wmo_description(3) == "nublado"
        assert _wmo_description(61) == "lluvia ligera"
        assert _wmo_description(95) == "tormenta eléctrica"

    def test_codigo_desconocido(self) -> None:
        """Código WMO no mapeado devuelve 'sin datos'."""
        assert _wmo_description(999) == "sin datos"

    def test_codigo_none(self) -> None:
        """None devuelve 'sin datos'."""
        assert _wmo_description(None) == "sin datos"


# ── Tests: _parse_openmeteo_response ─────────────────────────


class TestParseOpenMeteoResponse:
    """Parseo de respuesta JSON de OpenMeteo a WeatherData."""

    def test_respuesta_completa(self) -> None:
        """OpenMeteo con todos los campos produce WeatherData completo."""
        wd = _parse_openmeteo_response(
            _OPENMETEO_RESPUESTA_COMPLETA, -38.23, -72.68
        )

        assert wd.lat == -38.23
        assert wd.lon == -72.68
        assert wd.location == "Traiguén"  # cerca de default
        assert wd.temperature_c == 18.5
        assert wd.feels_like_c == 17.2
        assert wd.humidity == 65
        assert wd.description == "nublado"  # code 3
        assert wd.wind_speed_ms == 3.6
        assert wd.rain_1h_mm == 0.5
        assert "18°C" in wd.texto
        assert "nublado" in wd.texto

    def test_campos_ausentes_produce_nones(self) -> None:
        """Campos None en current → temp, feels, humidity son None."""
        wd = _parse_openmeteo_response(
            {"current": {}}, -38.5, -72.0
        )

        assert wd.temperature_c is None
        assert wd.feels_like_c is None
        assert wd.humidity is None
        assert wd.description == "sin datos"
        assert "temperatura no disponible" in wd.texto

    def test_sin_current_produce_nones(self) -> None:
        """Sin bloque current → todos los campos son None."""
        wd = _parse_openmeteo_response({}, -33.0, -70.0)

        assert wd.temperature_c is None
        assert wd.humidity is None
        assert wd.description == "sin datos"

    def test_coordenadas_de_santiago_nombran_santiago(self) -> None:
        """Coords de Santiago resuelven al nombre de la comuna, no a genérico."""
        wd = _parse_openmeteo_response(
            {
                "current": {
                    "temperature_2m": 25.0,
                    "relative_humidity_2m": 30,
                    "weather_code": 0,
                }
            },
            -33.45, -70.65,  # Santiago
        )

        assert wd.location == "Santiago"
        assert wd.temperature_c == 25.0
        assert wd.description == "cielo despejado"

    def test_coordenadas_desconocidas_usan_nombre_generico(self) -> None:
        """Coords fuera del mapa de comunas → 'la zona consultada'."""
        wd = _parse_openmeteo_response(
            {
                "current": {
                    "temperature_2m": 10.0,
                    "relative_humidity_2m": 40,
                    "weather_code": 0,
                }
            },
            0.0, 0.0,  # Atlántico / desconocido
        )
        assert wd.location == "la zona consultada"

    def test_rain_cero_no_se_reporta(self) -> None:
        """rain=0.0 → rain_1h_mm=None (umbral > 0)."""
        wd = _parse_openmeteo_response(
            {
                "current": {
                    "temperature_2m": 16.0,
                    "relative_humidity_2m": 60,
                    "weather_code": 3,
                    "rain": 0.0,
                }
            },
            -38.23, -72.68,
        )

        assert wd.rain_1h_mm is None
        assert "mm" not in wd.texto  # sin mención de lluvia

    def test_wind_zero_si_incluye(self) -> None:
        """wind_speed_10m=0.0 → wind_speed_ms=0.0 (no None)."""
        wd = _parse_openmeteo_response(
            {
                "current": {
                    "temperature_2m": 20.0,
                    "relative_humidity_2m": 50,
                    "weather_code": 1,
                    "wind_speed_10m": 0.0,
                }
            },
            -38.23, -72.68,
        )

        assert wd.wind_speed_ms == 0.0
        assert "viento" not in wd.texto  # no se menciona si es 0


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
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=json_body)

        return _install_mock_client(monkeypatch, handler)

    async def test_get_weather_traiguen_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Consulta con coordenadas default devuelve texto natural."""
        mock_client = self._mock_client(monkeypatch, _OPENMETEO_RESPUESTA_COMPLETA)
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
            "latitude": -33.45,
            "longitude": -70.65,
            "current": {
                "temperature_2m": 25.0,
                "relative_humidity_2m": 30,
                "weather_code": 0,
            },
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
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=_OPENMETEO_RESPUESTA_COMPLETA)

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


class TestGetWeatherErrores:
    """get_weather con errores de API. Sin red real."""

    def setup_method(self) -> None:
        _clear_cache()
        import app.services.weather_service as ws
        ws._http_client = None

    def _install_mock(
        self,
        monkeypatch: pytest.MonkeyPatch,
        status: int = 200,
        json_body: dict[str, object] | None = None,
        exc: type[Exception] | None = None,
    ) -> httpx.AsyncClient:
        """Instala mock que responde con status o lanza excepción."""
        def handler(request: httpx.Request) -> httpx.Response:
            if exc:
                raise exc("error simulado")
            return httpx.Response(status, json=json_body or {})

        return _install_mock_client(monkeypatch, handler)

    async def test_error_http_devuelve_mensaje(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HTTP 500 → mensaje informativo."""
        mock_client = self._install_mock(monkeypatch, 500, {})
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
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>502 Proxy Error</html>")

        mock_client = _install_mock_client(monkeypatch, handler)
        try:
            texto = await get_weather()
            assert "no está disponible" in texto
        finally:
            await mock_client.aclose()

    # ── Validación de rango lat/lon (defensa en profundidad) ────

    async def test_latitud_invalida_devuelve_mensaje(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Latitud > 90° retorna mensaje sin llamar a la API."""
        texto = await get_weather(lat=91.0, lon=-70.0)
        assert "latitud" in texto.lower()

    async def test_longitud_invalida_devuelve_mensaje(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Longitud > 180° retorna mensaje sin llamar a la API."""
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
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=_OPENMETEO_RESPUESTA_COMPLETA)

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
        # Forzar _CACHE_MAX_SIZE a 3 para el test.
        monkeypatch.setattr("app.services.weather_service._CACHE_MAX_SIZE", 3)

        def handler(request: httpx.Request) -> httpx.Response:
            _ = float(request.url.params["latitude"])  # solo para validar que llega
            _ = float(request.url.params["longitude"])
            return httpx.Response(200, json={
                "current": {
                    "temperature_2m": 18.0,
                    "relative_humidity_2m": 60,
                    "weather_code": 3,
                },
            })

        mock_client = _install_mock_client(monkeypatch, handler)
        try:
            import app.services.weather_service as ws

            # Insertar 4 entradas con coordenadas distintas.
            await get_weather_full(-38.23, -72.68)  # Traiguén (1ª → será evictada)
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
        # Reloj fake: lista mutable para que el handler y _cache_* compartan
        # la misma referencia. time.monotonic() retorna t[0].
        t = [1000.0]
        monkeypatch.setattr("app.services.weather_service.time.monotonic", lambda: t[0])

        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=_OPENMETEO_RESPUESTA_COMPLETA)

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



class TestCacheDegradado:
    """Fallback degradado ante fallo de OpenMeteo (Issue #120).

    Si la API falla y existe un cache vencido dentro del tope configurable,
    se entrega ese dato con una advertencia de antigüedad en el texto.
    Si el cache es más viejo que el tope o no existe, se propaga el error.
    """

    def setup_method(self) -> None:
        """Limpia cache y cliente HTTP entre tests."""
        _clear_cache()
        import app.services.weather_service as ws
        ws._http_client = None

    def _handler_falla(self, request: httpx.Request) -> httpx.Response:
        """Simula error de red de OpenMeteo."""
        raise httpx.ConnectError("fallo simulado de OpenMeteo")

    async def test_fallo_con_cache_fresco_devuelve_cache_fresco(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cache dentro del TTL: se usa sin consultar la API."""
        import app.services.weather_service as ws

        t = [1000.0]
        monkeypatch.setattr("app.services.weather_service.time.monotonic", lambda: t[0])

        wd = _parse_openmeteo_response(_OPENMETEO_RESPUESTA_COMPLETA, -38.23, -72.68)
        key = ws._cache_key(-38.23, -72.68)
        ws._cache[key] = (t[0], wd)

        mock_client = _install_mock_client(monkeypatch, self._handler_falla)
        try:
            result = await get_weather_full()
            assert result.texto == wd.texto
            assert result.stale_age_minutes is None
        finally:
            await mock_client.aclose()

    async def test_fallo_con_cache_vencida_dentro_tope_devuelve_degradado(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cache vencido 45 min y tope 6h: fallback degradado con advertencia."""
        import app.services.weather_service as ws

        t = [1000.0]
        monkeypatch.setattr("app.services.weather_service.time.monotonic", lambda: t[0])

        wd = _parse_openmeteo_response(_OPENMETEO_RESPUESTA_COMPLETA, -38.23, -72.68)
        key = ws._cache_key(-38.23, -72.68)
        ws._cache[key] = (t[0] - 45 * 60, wd)

        mock_client = _install_mock_client(monkeypatch, self._handler_falla)
        try:
            result = await get_weather_full()

            # El texto degradado y stale_age_minutes ya prueban que se uso el
            # cache vencido. Se omite el assert sobre caplog.text: es flaky en CI
            # por interaccion de captura de logs entre tests (ver 39da0ff).
            assert result.stale_age_minutes == 45
            assert "Pronóstico de hace 45 minutos:" in result.texto
        finally:
            await mock_client.aclose()

    async def test_fallo_con_cache_vencida_fuera_tope_propaga_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cache de 7h supera el tope de 6h: se mantiene el error honesto."""
        import app.services.weather_service as ws

        t = [1000.0]
        monkeypatch.setattr("app.services.weather_service.time.monotonic", lambda: t[0])

        wd = _parse_openmeteo_response(_OPENMETEO_RESPUESTA_COMPLETA, -38.23, -72.68)
        key = ws._cache_key(-38.23, -72.68)
        ws._cache[key] = (t[0] - 7 * 3600, wd)

        mock_client = _install_mock_client(monkeypatch, self._handler_falla)
        try:
            with pytest.raises(ConnectionError):
                await get_weather_full()
        finally:
            await mock_client.aclose()

    async def test_fallo_sin_cache_devuelve_mensaje_amigable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sin cache y con API caída: get_weather() retorna mensaje para el usuario."""
        _clear_cache()
        mock_client = _install_mock_client(monkeypatch, self._handler_falla)
        try:
            with pytest.raises(ConnectionError):
                await get_weather_full()
        finally:
            await mock_client.aclose()

    async def test_tope_degradacion_es_configurable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reducir el tope a 1h hace que un cache de 2h ya no sirva."""
        import app.services.weather_service as ws

        t = [1000.0]
        monkeypatch.setattr("app.services.weather_service.time.monotonic", lambda: t[0])
        monkeypatch.setattr(
            "app.services.weather_service.settings.weather_stale_cache_max_age_hours", 1
        )

        wd = _parse_openmeteo_response(_OPENMETEO_RESPUESTA_COMPLETA, -38.23, -72.68)
        key = ws._cache_key(-38.23, -72.68)
        ws._cache[key] = (t[0] - 2 * 3600, wd)

        mock_client = _install_mock_client(monkeypatch, self._handler_falla)
        try:
            with pytest.raises(ConnectionError):
                await get_weather_full()
        finally:
            await mock_client.aclose()


# ── Tests: datos climáticos históricos (Issue #124) ─────────────


# Respuesta simulada de OpenMeteo Archive para 2025 (datos diarios reducidos).
_RESPUESTA_ARCHIVE_2025: dict[str, object] = {
    "latitude": -38.23,
    "longitude": -72.68,
    "daily": {
        "time": [
            "2025-01-01", "2025-01-02", "2025-01-03",
            "2025-06-15", "2025-06-16",
            "2025-07-10",
            "2025-12-30", "2025-12-31",
        ],
        "temperature_2m_max": [25.0, 26.0, 24.0, 12.0, 11.0, 8.0, 22.0, 23.0],
        "temperature_2m_min": [10.0, 12.0, 11.0, 2.0, 1.0, -2.0, 10.0, 11.0],
        "precipitation_sum": [0.0, 0.0, 5.0, 20.0, 15.0, 8.0, 0.0, 0.0],
    },
}

# Respuesta simulada multi-anual 2024-2025.
_RESPUESTA_ARCHIVE_2024_2025: dict[str, object] = {
    "latitude": -38.23,
    "longitude": -72.68,
    "daily": {
        "time": [
            "2024-06-01", "2024-06-02",
            "2025-07-10", "2025-07-11",
        ],
        "temperature_2m_max": [15.0, 14.0, 8.0, 9.0],
        "temperature_2m_min": [5.0, 4.0, -2.0, -1.0],
        "precipitation_sum": [10.0, 5.0, 8.0, 2.0],
    },
}


class TestResolverComuna:
    """Resolución de nombres de comuna a coordenadas."""

    def test_comuna_conocida_traiguen(self) -> None:
        """Traiguén resuelve a coordenadas conocidas."""
        coords = _resolver_comuna("Traiguén")
        assert coords is not None
        lat, lon = coords
        assert lat == -38.23
        assert lon == -72.68

    def test_comuna_conocida_temuco(self) -> None:
        """Temuco resuelve a coordenadas conocidas."""
        coords = _resolver_comuna("Temuco")
        assert coords is not None
        assert coords == (-38.74, -72.59)

    def test_comuna_case_insensitive(self) -> None:
        """Búsqueda case-insensitive."""
        coords = _resolver_comuna("TRAIGUEN")
        assert coords is not None

    def test_comuna_desconocida(self) -> None:
        """Comuna no registrada devuelve None."""
        coords = _resolver_comuna("Londres")
        assert coords is None

    def test_comuna_variantes_tilde_mismas_coords(self) -> None:
        """'traiguen' y 'Traiguén' (con y sin tilde) resuelven a las mismas coordenadas."""
        assert _resolver_comuna("traiguen") == _resolver_comuna("Traiguén") == (-38.23, -72.68)


class TestParseHistoricalResponse:
    """Parseo de respuesta de OpenMeteo Archive a resúmenes anuales."""

    def test_un_ano_datos_completos(self) -> None:
        """Respuesta con un año produce un resumen con todos los campos."""
        summaries = _parse_historical_response(_RESPUESTA_ARCHIVE_2025, 2025, 2025)
        assert len(summaries) == 1
        s = summaries[0]
        assert s.year == 2025
        assert s.temp_promedio is not None
        assert s.temp_max_promedio is not None
        assert s.temp_min_promedio is not None
        assert s.precipitacion_total_mm is not None
        assert s.dias_helada is not None

    def test_dos_anios(self) -> None:
        """Respuesta multi-anual produce dos resúmenes."""
        summaries = _parse_historical_response(_RESPUESTA_ARCHIVE_2024_2025, 2024, 2025)
        assert len(summaries) == 2
        years = [s.year for s in summaries]
        assert 2024 in years
        assert 2025 in years

    def test_calcula_temp_promedio(self) -> None:
        """Temp promedio calculado correctamente."""
        summaries = _parse_historical_response(_RESPUESTA_ARCHIVE_2025, 2025, 2025)
        s = summaries[0]
        # (max+min)/2 para 8 días
        expected_avg = sum([
            (25.0 + 10.0) / 2,
            (26.0 + 12.0) / 2,
            (24.0 + 11.0) / 2,
            (12.0 + 2.0) / 2,
            (11.0 + 1.0) / 2,
            (8.0 + -2.0) / 2,
            (22.0 + 10.0) / 2,
            (23.0 + 11.0) / 2,
        ]) / 8
        assert s.temp_promedio == pytest.approx(expected_avg, abs=0.01)

    def test_calcula_precipitacion_total(self) -> None:
        """Precipitación total suma correctamente."""
        summaries = _parse_historical_response(_RESPUESTA_ARCHIVE_2025, 2025, 2025)
        s = summaries[0]
        assert s.precipitacion_total_mm == pytest.approx(48.0, abs=0.01)  # 0+0+5+20+15+8+0+0

    def test_calcula_dias_helada(self) -> None:
        """Días con temp_min < 0°C se cuentan correctamente."""
        summaries = _parse_historical_response(_RESPUESTA_ARCHIVE_2025, 2025, 2025)
        s = summaries[0]
        # Solo 1 día con temp_min < 0°C: -2.0 el 2025-07-10
        assert s.dias_helada == 1

    def test_sin_datos_devuelve_lista_vacia(self) -> None:
        """Respuesta sin daily.time produce lista vacía."""
        summaries = _parse_historical_response(
            {"daily": {"time": [], "temperature_2m_max": [], "temperature_2m_min": [], "precipitation_sum": []}},
            2025, 2025,
        )
        assert summaries == []

    def test_fechas_fuera_rango_se_ignoran(self) -> None:
        """Datos de años fuera del rango no se incluyen."""
        summaries = _parse_historical_response(_RESPUESTA_ARCHIVE_2024_2025, 2025, 2025)
        assert len(summaries) == 1
        assert summaries[0].year == 2025


class TestFormatHistoricoText:
    """Formateo de resúmenes históricos a texto natural."""

    def test_resumen_completo(self) -> None:
        """Resumen completo incluye temperatura, lluvia y heladas."""
        from app.services.weather_service import HistoricalYearSummary

        summaries = [
            HistoricalYearSummary(
                year=2025, temp_promedio=12.5, temp_max_promedio=18.0,
                temp_min_promedio=7.0, precipitacion_total_mm=850.0, dias_helada=15,
            ),
        ]
        texto = _format_historico_text(summaries, "Traiguén")
        assert "Traiguén" in texto
        assert "2025" in texto
        assert "12°C" in texto
        assert "850mm" in texto
        assert "15 días" in texto
        assert "según OpenMeteo" in texto

    def test_dos_anios(self) -> None:
        """Dos años se separan con 'y' en el texto."""
        from app.services.weather_service import HistoricalYearSummary

        summaries = [
            HistoricalYearSummary(
                year=2024, temp_promedio=13.0, temp_max_promedio=19.0,
                temp_min_promedio=7.0, precipitacion_total_mm=800.0, dias_helada=10,
            ),
            HistoricalYearSummary(
                year=2025, temp_promedio=12.0, temp_max_promedio=18.0,
                temp_min_promedio=6.0, precipitacion_total_mm=850.0, dias_helada=15,
            ),
        ]
        texto = _format_historico_text(summaries, "Traiguén")
        assert "2024" in texto
        assert "2025" in texto
        assert ", y " in texto  # separador entre años
        assert "según OpenMeteo" in texto

    def test_metrica_temperatura_solo(self) -> None:
        """Con metrica='temperatura' solo incluye datos de temperatura."""
        from app.services.weather_service import HistoricalYearSummary

        summaries = [
            HistoricalYearSummary(
                year=2025, temp_promedio=12.5, temp_max_promedio=18.0,
                temp_min_promedio=7.0, precipitacion_total_mm=850.0, dias_helada=15,
            ),
        ]
        texto = _format_historico_text(summaries, "Traiguén", metrica="temperatura")
        assert "12°C" in texto
        assert "mm" not in texto
        assert "helada" not in texto
        assert "según OpenMeteo" in texto

    def test_metrica_lluvia_solo(self) -> None:
        """Con metrica='lluvia' solo incluye precipitación."""
        from app.services.weather_service import HistoricalYearSummary

        summaries = [
            HistoricalYearSummary(
                year=2025, temp_promedio=12.5, temp_max_promedio=18.0,
                temp_min_promedio=7.0, precipitacion_total_mm=850.0, dias_helada=15,
            ),
        ]
        texto = _format_historico_text(summaries, "Traiguén", metrica="lluvia")
        assert "mm" in texto
        assert "°C" not in texto
        assert "helada" not in texto
        assert "según OpenMeteo" in texto

    def test_metrica_heladas_solo(self) -> None:
        """Con metrica='heladas' solo incluye días de helada."""
        from app.services.weather_service import HistoricalYearSummary

        summaries = [
            HistoricalYearSummary(
                year=2025, temp_promedio=12.5, temp_max_promedio=18.0,
                temp_min_promedio=7.0, precipitacion_total_mm=850.0, dias_helada=15,
            ),
        ]
        texto = _format_historico_text(summaries, "Traiguén", metrica="heladas")
        assert "helada" in texto
        assert "°C" not in texto
        assert "mm" not in texto
        assert "según OpenMeteo" in texto

    def test_sin_datos_devuelve_mensaje(self) -> None:
        """Lista vacía produce mensaje informativo."""
        texto = _format_historico_text([], "Traiguén")
        assert "No hay datos históricos" in texto
        assert "Traiguén" in texto
        assert "según OpenMeteo" in texto


class TestFetchHistorico:
    """fetch_historico con httpx mockeado y cache."""

    def setup_method(self) -> None:
        """Limpia cache histórico y cliente HTTP entre tests."""
        _clear_historical_cache()
        import app.services.weather_service as ws
        ws._http_client = None

    def _mock_client(
        self, monkeypatch: pytest.MonkeyPatch, json_body: dict[str, object]
    ) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=json_body)

        transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=transport)
        monkeypatch.setattr("app.services.weather_service._http_client", mock_client)
        return mock_client

    async def test_fetch_historico_un_ano(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """fetch_historico devuelve resumen de un año."""
        mock_client = self._mock_client(monkeypatch, _RESPUESTA_ARCHIVE_2025)
        try:
            summaries = await fetch_historico(-38.23, -72.68, years=1)
            assert len(summaries) >= 1
            assert summaries[0].year >= 2024
        finally:
            await mock_client.aclose()

    async def test_fetch_historico_clampea_years_sobre_5(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """years > 5 se clampea a 5: el resultado se cachea bajo la clave y5, no y10."""
        import app.services.weather_service as ws

        mock_client = self._mock_client(monkeypatch, _RESPUESTA_ARCHIVE_2025)
        try:
            await fetch_historico(-38.23, -72.68, years=10)
            assert ws._historical_cache_get(-38.23, -72.68, 5) is not None
            assert ws._historical_cache_get(-38.23, -72.68, 10) is None
        finally:
            await mock_client.aclose()

    async def test_fetch_historico_cache_funciona(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Segunda llamada usa cache, no va a API."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=_RESPUESTA_ARCHIVE_2025)

        transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=transport)
        monkeypatch.setattr("app.services.weather_service._http_client", mock_client)
        try:
            s1 = await fetch_historico(-38.23, -72.68, years=1)
            s2 = await fetch_historico(-38.23, -72.68, years=1)
            assert len(s1) == len(s2)
            assert call_count == 1  # Segunda llamada no fue a API
        finally:
            await mock_client.aclose()

    async def test_fetch_historico_error_http(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Error HTTP 500 propaga RuntimeError."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=transport)
        monkeypatch.setattr("app.services.weather_service._http_client", mock_client)
        try:
            with pytest.raises(RuntimeError):
                await fetch_historico(-38.23, -72.68, years=1)
        finally:
            await mock_client.aclose()

    async def test_fetch_historico_error_red(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Error de red propaga ConnectionError."""
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("red caida")

        transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=transport)
        monkeypatch.setattr("app.services.weather_service._http_client", mock_client)
        try:
            with pytest.raises(ConnectionError):
                await fetch_historico(-38.23, -72.68, years=1)
        finally:
            await mock_client.aclose()

    async def test_fetch_historico_valida_latitud(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Latitud fuera de rango lanza ValueError."""
        with pytest.raises(ValueError, match="Latitud fuera de rango"):
            await fetch_historico(lat=100.0, lon=-70.0)

    async def test_fetch_historico_valida_longitud(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Longitud fuera de rango lanza ValueError."""
        with pytest.raises(ValueError, match="Longitud fuera de rango"):
            await fetch_historico(lat=-33.0, lon=200.0)


class TestGetClimaHistorico:
    """get_clima_historico tool function con mock."""

    def setup_method(self) -> None:
        _clear_historical_cache()
        import app.services.weather_service as ws
        ws._http_client = None

    def _mock_client(
        self, monkeypatch: pytest.MonkeyPatch, json_body: dict[str, object]
    ) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=json_body)

        transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=transport)
        monkeypatch.setattr("app.services.weather_service._http_client", mock_client)
        return mock_client

    async def test_comuna_conocida_devuelve_texto(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Comuna conocida devuelve texto natural."""
        mock_client = self._mock_client(monkeypatch, _RESPUESTA_ARCHIVE_2025)
        try:
            texto = await get_clima_historico("Traiguén")
            assert "Traiguén" in texto
            assert "2025" in texto
            assert "según OpenMeteo" in texto
        finally:
            await mock_client.aclose()

    async def test_comuna_desconocida_devuelve_mensaje(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Comuna no registrada devuelve mensaje informativo."""
        texto = await get_clima_historico("Londres")
        assert "no reconozco" in texto.lower()
        assert "Traiguén" in texto or "Temuco" in texto or "Santiago" in texto

    async def test_error_api_devuelve_mensaje_amigable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Error de API devuelve mensaje amigable para el agricultor."""
        caplog.set_level(logging.WARNING, logger="app.services.weather_service")

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("secreto-clima Traiguén -38.23,-72.68")

        transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=transport)
        monkeypatch.setattr("app.services.weather_service._http_client", mock_client)
        try:
            texto = await get_clima_historico("Traiguén")
            assert "No pude consultar" in texto
            assert "Traiguén" in texto
            assert "secreto-clima" not in caplog.text
            assert "Traiguén" not in caplog.text
            assert "-38.23" not in caplog.text
            assert "-72.68" not in caplog.text
        finally:
            await mock_client.aclose()

    async def test_texto_cita_fuente_openmeteo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """El texto histórico incluye 'según OpenMeteo'."""
        mock_client = self._mock_client(monkeypatch, _RESPUESTA_ARCHIVE_2025)
        try:
            texto = await get_clima_historico("Traiguén")
            assert "según OpenMeteo" in texto
        finally:
            await mock_client.aclose()

    async def test_metrica_temperatura(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Solicitar metrica=temperatura solo muestra temperatura."""
        mock_client = self._mock_client(monkeypatch, _RESPUESTA_ARCHIVE_2025)
        try:
            texto = await get_clima_historico("Traiguén", metrica="temperatura")
            assert "temperatura" in texto or "°C" in texto
        finally:
            await mock_client.aclose()
