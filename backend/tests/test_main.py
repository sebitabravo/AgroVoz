"""Tests de main.py — exception handler, lifespan y RequestIDMiddleware."""

import json
from collections.abc import Iterator
from importlib import reload
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient

import app.main as app_main
from app.core import config

# Settings que los tests de lifespan modifican y deben restaurar.
_LIFESPAN_SETTINGS = (
    "app_env",
    "openwa_api_key",
    "openweathermap_api_key",
    "openwa_webhook_secret",
)


@pytest.fixture
def prod_lifespan(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Configura app_env=production y restaura todas las settings al final.

    Elimina ~9 líneas de boilerplate save/restore por cada test de lifespan.
    """
    originals = {attr: getattr(config.settings, attr) for attr in _LIFESPAN_SETTINGS}
    monkeypatch.setattr(config.settings, "app_env", "production")
    reload(app_main)
    yield
    for attr, value in originals.items():
        monkeypatch.setattr(config.settings, attr, value)
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


async def test_lifespan_falla_sin_openweathermap_api_key_en_production(
    monkeypatch: pytest.MonkeyPatch,
    prod_lifespan: None,
) -> None:
    """Lifespan debe raise ValueError si app_env=production y no hay OpenWeatherMap key."""
    monkeypatch.setattr(config.settings, "openweathermap_api_key", "")
    monkeypatch.setattr(config.settings, "openwa_api_key", "set-not-empty")
    monkeypatch.setattr(config.settings, "openwa_webhook_secret", "set-not-empty")
    reload(app_main)

    with pytest.raises(ValueError, match="OPENWEATHERMAP_API_KEY"):
        async with app_main.lifespan(app_main.app):
            pass


async def test_lifespan_falla_sin_openwa_api_key_en_production(
    monkeypatch: pytest.MonkeyPatch,
    prod_lifespan: None,
) -> None:
    """Lifespan debe raise ValueError si app_env=production y no hay OPENWA_API_KEY."""
    monkeypatch.setattr(config.settings, "openwa_api_key", "")
    monkeypatch.setattr(config.settings, "openweathermap_api_key", "set-not-empty")
    monkeypatch.setattr(config.settings, "openwa_webhook_secret", "set-not-empty")
    reload(app_main)

    with pytest.raises(ValueError, match="OPENWA_API_KEY"):
        async with app_main.lifespan(app_main.app):
            pass


async def test_lifespan_falla_sin_ambas_api_keys_en_production(
    monkeypatch: pytest.MonkeyPatch,
    prod_lifespan: None,
) -> None:
    """Lifespan debe raise ValueError con ambas keys faltantes en producción."""
    monkeypatch.setattr(config.settings, "openwa_api_key", "")
    monkeypatch.setattr(config.settings, "openweathermap_api_key", "")
    monkeypatch.setattr(config.settings, "openwa_webhook_secret", "set-not-empty")
    reload(app_main)

    with pytest.raises(ValueError, match=r"(?=.*OPENWEATHERMAP_API_KEY)(?=.*OPENWA_API_KEY)"):
        async with app_main.lifespan(app_main.app):
            pass


async def test_lifespan_rechaza_key_con_solo_whitespace(
    monkeypatch: pytest.MonkeyPatch,
    prod_lifespan: None,
) -> None:
    """Lifespan debe rechazar keys que son solo whitespace (no solo vacías)."""
    monkeypatch.setattr(config.settings, "openwa_api_key", "   ")
    monkeypatch.setattr(config.settings, "openweathermap_api_key", "real-key")
    monkeypatch.setattr(config.settings, "openwa_webhook_secret", "real-secret")
    reload(app_main)

    with pytest.raises(ValueError, match="OPENWA_API_KEY"):
        async with app_main.lifespan(app_main.app):
            pass


async def test_lifespan_falla_sin_webhook_secret_en_production(
    monkeypatch: pytest.MonkeyPatch,
    prod_lifespan: None,
) -> None:
    """Lifespan debe raise ValueError si no hay OPENWA_WEBHOOK_SECRET en producción."""
    monkeypatch.setattr(config.settings, "openwa_webhook_secret", "")
    monkeypatch.setattr(config.settings, "openwa_api_key", "set-not-empty")
    monkeypatch.setattr(config.settings, "openweathermap_api_key", "set-not-empty")
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
