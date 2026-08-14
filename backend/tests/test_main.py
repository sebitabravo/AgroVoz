"""Tests de main.py — exception handler, lifespan, RequestIDMiddleware, GZipMiddleware."""

import asyncio
import datetime
import json
from collections.abc import Generator, Iterator
from importlib import reload
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import Session

import app.main as app_main
from app.core import config


@pytest.fixture
def prod_lifespan(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Configura app_env=production base. monkeypatch auto-undo revierte al final.

    Solo setea el app_env — cada test hace su propio reload(app_main) después
    de configurar los atributos específicos que necesita. El reload de teardown
    asegura que app_main arranque limpio para el siguiente test.
    """
    monkeypatch.setattr(config.settings, "app_env", "production")
    yield
    reload(app_main)


async def test_exception_handler_no_leakea_info_en_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En producción, el handler global no debe exponer str(exc) al cliente."""
    original_env = config.settings.app_env

    try:
        monkeypatch.setattr(config.settings, "app_env", "production")
        reload(app_main)

        mock_request = MagicMock()
        mock_request.method = "GET"
        mock_request.url.path = "/test"

        response = await app_main.global_exception_handler(
            mock_request,
            ValueError("DATABASE_URL=sqlite:///secret.db"),
        )
        assert response.status_code == 500
        data = json.loads(bytes(response.body))
        # En producción: mensaje genérico, sin leakear el str(exc) real
        assert data["detail"] == "Error interno del servidor."
        assert "DATABASE_URL" not in data["detail"]
        assert "secret" not in data["detail"]
    finally:
        monkeypatch.setattr(config.settings, "app_env", original_env)
        reload(app_main)


async def test_exception_handler_expone_detalle_en_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En desarrollo, el handler global debe incluir el str(exc) para debug."""
    original_env = config.settings.app_env

    try:
        monkeypatch.setattr(config.settings, "app_env", "development")
        reload(app_main)

        mock_request = MagicMock()
        mock_request.method = "POST"
        mock_request.url.path = "/api/v1/test"

        error_msg = "Falta configurar OPENWA_API_KEY"
        response = await app_main.global_exception_handler(
            mock_request,
            ValueError(error_msg),
        )
        assert response.status_code == 500
        data = json.loads(bytes(response.body))
        # En desarrollo: debe incluir el mensaje real del error
        assert error_msg in data["detail"]
    finally:
        monkeypatch.setattr(config.settings, "app_env", original_env)
        reload(app_main)


async def test_lifespan_falla_sin_openwa_api_key_en_production(
    monkeypatch: pytest.MonkeyPatch,
    prod_lifespan: None,
) -> None:
    """Lifespan debe raise ValueError si app_env=production y no hay OPENWA_API_KEY."""
    monkeypatch.setattr(config.settings, "openwa_api_key", "")
    monkeypatch.setattr(config.settings, "openwa_webhook_secret", "set-not-empty")
    reload(app_main)

    with pytest.raises(ValueError, match="OPENWA_API_KEY"):
        async with app_main.lifespan(app_main.app):
            pass


async def test_lifespan_falla_sin_ningun_secret_en_production(
    monkeypatch: pytest.MonkeyPatch,
    prod_lifespan: None,
) -> None:
    """Lifespan debe raise ValueError con los 3 secrets vacíos en producción."""
    monkeypatch.setattr(config.settings, "openwa_api_key", "")
    monkeypatch.setattr(config.settings, "openwa_webhook_secret", "")
    reload(app_main)

    with pytest.raises(
        ValueError,
        match=r"(?=.*OPENWA_API_KEY)(?=.*OPENWA_WEBHOOK_SECRET)",
    ):
        async with app_main.lifespan(app_main.app):
            pass


async def test_lifespan_rechaza_key_con_solo_whitespace(
    monkeypatch: pytest.MonkeyPatch,
    prod_lifespan: None,
) -> None:
    """Lifespan debe rechazar keys que son solo whitespace (no solo vacías)."""
    monkeypatch.setattr(config.settings, "openwa_api_key", "   ")
    monkeypatch.setattr(config.settings, "openwa_webhook_secret", "real-secret")
    reload(app_main)

    with pytest.raises(ValueError, match="OPENWA_API_KEY"):
        async with app_main.lifespan(app_main.app):
            pass


async def test_lifespan_rechaza_webhook_secret_con_solo_whitespace(
    monkeypatch: pytest.MonkeyPatch,
    prod_lifespan: None,
) -> None:
    """Lifespan debe rechazar OPENWA_WEBHOOK_SECRET que es solo whitespace."""
    monkeypatch.setattr(config.settings, "openwa_webhook_secret", "\n ")
    monkeypatch.setattr(config.settings, "openwa_api_key", "real-key")
    reload(app_main)

    with pytest.raises(ValueError, match="OPENWA_WEBHOOK_SECRET"):
        async with app_main.lifespan(app_main.app):
            pass


async def test_lifespan_falla_sin_webhook_secret_en_production(
    monkeypatch: pytest.MonkeyPatch,
    prod_lifespan: None,
) -> None:
    """Lifespan debe raise ValueError si no hay OPENWA_WEBHOOK_SECRET en producción."""
    monkeypatch.setattr(config.settings, "openwa_webhook_secret", "")
    monkeypatch.setattr(config.settings, "openwa_api_key", "set-not-empty")
    reload(app_main)

    with pytest.raises(ValueError, match="OPENWA_WEBHOOK_SECRET"):
        async with app_main.lifespan(app_main.app):
            pass


# ── RequestIDMiddleware ──


async def test_request_id_generado_cuando_cliente_no_envia_header(
    client: AsyncClient,
) -> None:
    """RequestIDMiddleware genera X-Request-ID cuando el cliente no envía header."""
    response = await client.get("/api/v1/health?probe=liveness")

    assert response.status_code == 200
    request_id = response.headers.get("X-Request-ID")
    assert request_id is not None
    # UUID7-like: timestamp_hex-UUID_hex (ej: "195b7e3a0a0-a1b2c3d4")
    # Al menos 16 caracteres con al menos un guion
    assert len(request_id) >= 16
    assert "-" in request_id


async def test_request_id_propaga_header_valido_del_cliente(
    client: AsyncClient,
) -> None:
    """RequestIDMiddleware reusa X-Request-ID válido enviado por el cliente."""
    custom_id = "test-request-id-001"
    response = await client.get(
        "/api/v1/health?probe=liveness",
        headers={"X-Request-ID": custom_id},
    )

    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == custom_id


async def test_request_id_sanitiza_input_malicioso(
    client: AsyncClient,
) -> None:
    """RequestIDMiddleware rechaza X-Request-ID con caracteres no alfanuméricos.

    Previene log injection vía headers maliciosos (CWE-117).
    """
    malicious_id = "bad\nid<script>\x00"
    response = await client.get(
        "/api/v1/health?probe=liveness",
        headers={"X-Request-ID": malicious_id},
    )

    assert response.status_code == 200
    response_id = response.headers.get("X-Request-ID")
    assert response_id is not None
    assert response_id != malicious_id
    # Debe generar uno nuevo con formato válido
    assert "-" in response_id
    assert len(response_id) >= 16


async def test_request_id_rechaza_header_demasiado_largo(
    client: AsyncClient,
) -> None:
    """RequestIDMiddleware rechaza X-Request-ID de más de 64 caracteres."""
    long_id = "a" * 65
    response = await client.get(
        "/api/v1/health?probe=liveness",
        headers={"X-Request-ID": long_id},
    )

    assert response.status_code == 200
    response_id = response.headers.get("X-Request-ID")
    assert response_id is not None
    assert response_id != long_id
    assert len(response_id) <= 64


# ── RequestIDFormatter ──


def test_request_id_formatter_inyecta_request_id_desde_contextvar() -> None:
    """RequestIDFormatter inyecta request_id en LogRecord desde el ContextVar."""
    import logging

    formatter = app_main.RequestIDFormatter("%(request_id)s — %(message)s")
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="test message", args=(), exc_info=None,
    )
    # Simular ContextVar con un request_id real
    token = app_main.request_id_ctx.set("abc123-test")
    try:
        result = formatter.format(record)
        assert result == "abc123-test — test message"
    finally:
        app_main.request_id_ctx.reset(token)


def test_request_id_formatter_no_sobreescribe_request_id_existente() -> None:
    """Si el LogRecord ya tiene request_id, el formatter no lo sobreescribe."""
    import logging

    formatter = app_main.RequestIDFormatter("%(request_id)s — %(message)s")
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="test message", args=(), exc_info=None,
    )
    record.request_id = "pre-existente"
    # ContextVar tiene otro valor — el formatter no debe sobreescribir
    token = app_main.request_id_ctx.set("contextvar-value")
    try:
        result = formatter.format(record)
        assert result == "pre-existente — test message"
    finally:
        app_main.request_id_ctx.reset(token)


def test_request_id_formatter_default_sin_contextvar() -> None:
    """Si ContextVar no está seteada, usa el default '-'."""
    import logging

    formatter = app_main.RequestIDFormatter("%(request_id)s — %(message)s")
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="test message", args=(), exc_info=None,
    )
    # No seteamos ContextVar — debe usar el default "-"
    result = formatter.format(record)
    assert result == "- — test message"


# ── Lifespan shutdown: _close_http_client ──


async def test_lifespan_shutdown_cierra_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El shutdown del lifespan debe llamar a _close_http_client.

    Verifica que el connection pool de httpx se libera correctamente
    al detener la aplicación, sin dejar conexiones abiertas.
    """
    import app.services.weather_service as ws

    # Crear un cliente HTTP para que _close_http_client tenga algo que cerrar.
    client = await ws._get_http_client()
    assert not client.is_closed

    # Ejecutar el lifespan completo (startup + shutdown).
    async with app_main.lifespan(app_main.app):
        pass

    # Después del shutdown, el cliente debe estar cerrado.
    assert ws._http_client is None or ws._http_client.is_closed


async def test_data_hub_remote_scheduler_continua_tras_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una caída de una ronda remota no mata el scheduler diario."""
    sync = Mock(side_effect=RuntimeError("proveedor caído"))
    sleep = AsyncMock(side_effect=[None, asyncio.CancelledError])
    monkeypatch.setattr(app_main, "_sync_data_hub_remote", sync)
    monkeypatch.setattr(app_main.asyncio, "sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        await app_main._data_hub_remote_scheduler()

    sync.assert_called_once_with()
    assert sleep.await_count == 2


async def test_report_temp_scheduler_purga_antes_de_dormir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La tarea ejecuta cleanup al iniciar y conserva cancelación cooperativa."""
    purge = Mock(return_value=2)
    sleep = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr("app.services.report_service.purge_stale_reports", purge)
    monkeypatch.setattr(app_main.asyncio, "sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        await app_main._report_temp_purge_scheduler()

    purge.assert_called_once_with()
    sleep.assert_awaited_once_with(app_main._REPORT_TEMP_PURGE_INTERVAL_SECONDS)


# ── GZipMiddleware ──────────────────────────────────────────────────

# Helper: sesion a la misma DB temporal que usa el client fixture.
def _session_test_db(tmp_path: Path) -> Generator[Session, None, None]:
    """Session apuntando a la misma DB temporal que usa el client fixture."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "test_agrovoz.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


async def test_gzip_comprime_respuesta_json_grande(
    client: AsyncClient,
    tmp_path: Path,
) -> None:
    """GZipMiddleware comprime responses JSON > 500 bytes.

    Seedea 3 precios ODEPA en la DB temporal. La respuesta sin
    ?mercado= genera PriceListResponse con 3 entradas cuyo JSON
    serializado pesa > 500 bytes. GZipMiddleware agrega
    Content-Encoding: gzip.
    """
    from decimal import Decimal

    from app.models.odepa_price import OdepaPrice

    with next(_session_test_db(tmp_path)) as db:
        for i in range(3):
            registro = OdepaPrice(
                producto="papa",
                mercado=f"Mercado Test {i}",
                precio_kg=Decimal("1200"),
                unidad="kg",
                fecha=datetime.date(2026, 6, 20),
                fuente="test",
            )
            db.add(registro)
        db.commit()

    response = await client.get("/api/v1/prices/papa")
    assert response.status_code == 200
    data = response.json()
    assert data["total_mercados"] == 3

    # Verificar que la respuesta fue comprimida.
    assert response.headers.get("Content-Encoding") == "gzip"


async def test_gzip_no_comprime_respuesta_pequena(
    client: AsyncClient,
) -> None:
    """GZipMiddleware NO comprime responses < 500 bytes.

    /api/v1/health?probe=liveness devuelve JSON de ~37 bytes,
    muy por debajo del minimum_size=500. No debe tener
    Content-Encoding: gzip.
    """
    response = await client.get("/api/v1/health?probe=liveness")
    assert response.status_code == 200
    # El body tiene ~37 bytes (< 500). GZipMiddleware no debe comprimir.
    assert response.headers.get("Content-Encoding") != "gzip"


async def test_gzip_comprime_audio_ogg_por_defecto(
    client: AsyncClient,
) -> None:
    """GZipMiddleware en Starlette 1.3.1 comprime audio/ogg por defecto.

    Starlette solo excluye text/event-stream de la compresion. Audio/ogg
    se comprime con gzip (content-encoding: gzip). Esto no rompe la
    respuesta: el content-type se preserva, los headers de seguridad
    se mantienen, y el body es correcto. En produccion, AgroVoz NO
    sirve audio/ogg como respuesta HTTP directa (el audio se envia
    via Open-WA API), por lo que este caso no ocurre en la practica.

    Este test verifica que la respuesta es funcional aunque GZip
    comprima el content-type audio/ogg.
    """
    from app.main import app

    _original_routes = list(app.router.routes)

    from starlette.responses import Response as StarletteResponse

    @app.get("/__test_gzip_audio")
    async def _test_audio_ogg() -> StarletteResponse:
        return StarletteResponse(
            content=bytes(1000),  # > minimum_size=500
            media_type="audio/ogg",
        )

    try:
        response = await client.get("/__test_gzip_audio")
        assert response.status_code == 200
        # Starlette 1.3.1 comprime audio/ogg (solo excluye text/event-stream).
        # Verificamos que la respuesta es funcional.
        assert response.headers.get("content-type") == "audio/ogg"
        assert response.headers.get("Content-Encoding") == "gzip"
        # Content-Length refleja el tamaño comprimido (~29 bytes), no 1000.
        assert int(response.headers.get("content-length", "0")) < 100
    finally:
        app.router.routes = _original_routes


# ── CORSMiddleware ──────────────────────────────────────────────────


async def test_cors_dev_expone_allow_origin_sin_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En dev (cors_origins_list=['*']) la API expone Access-Control-Allow-Origin.

    Verifica que la landing puede hacer fetch (issue #151) y, sobre todo, que
    NUNCA se emite Access-Control-Allow-Credentials: true. Combinar credentials
    con origen wildcard viola la RFC 6454 y expondria la cookie de sesion admin
    a cualquier origen.

    Reconstruye la app bajo app_env=development (autocontenido): otros tests del
    modulo recargan app.main bajo production y dejan el modulo en ese estado.
    """
    original_env = config.settings.app_env
    try:
        monkeypatch.setattr(config.settings, "app_env", "development")
        reload(app_main)

        transport = ASGITransport(app=app_main.app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as dev_client:
            response = await dev_client.get(
                "/api/v1/health?probe=liveness",
                headers={"Origin": "https://una-landing-cualquiera.cl"},
            )

        assert response.status_code == 200
        # En dev el middleware responde con ACAO (wildcard).
        assert response.headers.get("access-control-allow-origin") is not None
        # Seguridad: jamas credenciales cross-origin.
        assert response.headers.get("access-control-allow-credentials") != "true"
    finally:
        monkeypatch.setattr(config.settings, "app_env", original_env)
        reload(app_main)


async def test_cors_preflight_options_responde_allow_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El preflight OPTIONS es respondido por CORSMiddleware (el mas externo).

    CORS debe responder el preflight ANTES que TrustedHost lo rechace, para que
    el navegador autorice el POST/PUT/DELETE de la landing.
    """
    original_env = config.settings.app_env
    try:
        monkeypatch.setattr(config.settings, "app_env", "development")
        reload(app_main)

        transport = ASGITransport(app=app_main.app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as dev_client:
            response = await dev_client.options(
                "/api/v1/health",
                headers={
                    "Origin": "https://agrovoz.cl",
                    "Access-Control-Request-Method": "POST",
                },
            )

        assert response.status_code == 200
        allow_methods = response.headers.get("access-control-allow-methods", "")
        assert "POST" in allow_methods
        # El preflight tampoco debe habilitar credenciales.
        assert response.headers.get("access-control-allow-credentials") != "true"
    finally:
        monkeypatch.setattr(config.settings, "app_env", original_env)
        reload(app_main)


async def test_cors_produccion_refleja_solo_origenes_permitidos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En produccion solo se reflejan los origenes configurados; evil.com no.

    Reconstruye la app con app_env=production y cors_origins fijos, luego verifica
    que un origen permitido se refleja y uno no permitido queda sin ACAO. Confirma
    que el fix del issue #151 no deja un CORS abierto en prod.
    """
    original_env = config.settings.app_env

    try:
        monkeypatch.setattr(config.settings, "app_env", "production")
        monkeypatch.setattr(
            config.settings,
            "cors_origins",
            "https://agrovoz.cl,https://www.agrovoz.cl",
        )
        reload(app_main)

        transport = ASGITransport(app=app_main.app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as prod_client:
            permitido = await prod_client.get(
                "/api/v1/health?probe=liveness",
                headers={"Origin": "https://agrovoz.cl"},
            )
            prohibido = await prod_client.get(
                "/api/v1/health?probe=liveness",
                headers={"Origin": "https://evil.com"},
            )

        # Origen permitido: se refleja exactamente (no wildcard en prod).
        assert (
            permitido.headers.get("access-control-allow-origin")
            == "https://agrovoz.cl"
        )
        # Origen no permitido: no se refleja.
        assert (
            prohibido.headers.get("access-control-allow-origin")
            != "https://evil.com"
        )
        # Nunca credenciales, ni siquiera con origen permitido.
        assert (
            permitido.headers.get("access-control-allow-credentials") != "true"
        )
    finally:
        monkeypatch.setattr(config.settings, "app_env", original_env)
        reload(app_main)
