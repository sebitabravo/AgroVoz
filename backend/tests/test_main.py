"""Tests de main.py — exception handler y lifespan."""

import json
from importlib import reload
from unittest.mock import MagicMock

import pytest

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
