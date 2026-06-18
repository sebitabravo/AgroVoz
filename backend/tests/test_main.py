"""Tests de main.py — exception handler, lifespan y RequestIDMiddleware."""

import json
from importlib import reload
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient

import app.main as app_main
from app.core import config


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


async def test_lifespan_falla_sin_api_key_en_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lifespan debe raise ValueError si app_env=production y no hay API key."""
    original_env = config.settings.app_env
    original_key = config.settings.openweathermap_api_key

    try:
        monkeypatch.setattr(config.settings, "app_env", "production")
        monkeypatch.setattr(config.settings, "openweathermap_api_key", "")
        reload(app_main)

        with pytest.raises(ValueError, match="OPENWEATHERMAP_API_KEY"):
            async with app_main.lifespan(app_main.app):
                pass  # No debería llegar acá — lifespan raisea antes del yield
    finally:
        monkeypatch.setattr(config.settings, "app_env", original_env)
        monkeypatch.setattr(config.settings, "openweathermap_api_key", original_key)
        reload(app_main)


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
