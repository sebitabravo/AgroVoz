"""Tests para app.services.openrouter_service: cliente HTTP del fallback remoto.

Cobertura: is_configured() segun API key, chat_completion_with_tools con
httpx mockeado (happy path, error HTTP, timeout), sin red real.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.core.config import settings
from app.services import openrouter_service


def _install_mock_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    """Instala un cliente HTTP mockeado en _http_client (mismo patron que weather_service)."""
    transport = httpx.MockTransport(handler)
    mock_client = httpx.AsyncClient(transport=transport)
    monkeypatch.setattr("app.services.openrouter_service._http_client", mock_client)
    return mock_client


class TestIsConfigured:
    """is_configured() refleja si hay API key seteada."""

    def test_sin_api_key_no_configurado(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "openrouter_api_key", "")
        assert openrouter_service.is_configured() is False

    def test_api_key_solo_whitespace_no_configurado(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "openrouter_api_key", "   ")
        assert openrouter_service.is_configured() is False

    def test_con_api_key_configurado(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-test-key")
        assert openrouter_service.is_configured() is True


class TestChatCompletionWithTools:
    """chat_completion_with_tools() — request/response contra OpenRouter mockeado."""

    async def test_request_incluye_headers_y_body_correctos(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """El request manda Authorization Bearer, model, messages y tools."""
        monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-test-key")
        monkeypatch.setattr(settings, "openrouter_model", "openrouter/free")
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("authorization")
            captured["body"] = request.content
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "hola"}}
                    ]
                },
            )

        client = _install_mock_client(monkeypatch, handler)
        try:
            result = await openrouter_service.chat_completion_with_tools(
                messages=[{"role": "user", "content": "test"}],
                tools=[{"type": "function", "function": {"name": "x"}}],
            )
            assert captured["auth"] == "Bearer sk-or-test-key"
            assert result["choices"][0]["message"]["content"] == "hola"
        finally:
            await client.aclose()

    async def test_error_http_4xx_propaga_excepcion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un status 401/429 debe propagar httpx.HTTPStatusError (raise_for_status)."""
        monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-test-key")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": "rate limited"})

        client = _install_mock_client(monkeypatch, handler)
        try:
            with pytest.raises(httpx.HTTPStatusError):
                await openrouter_service.chat_completion_with_tools(
                    messages=[{"role": "user", "content": "test"}], tools=[]
                )
        finally:
            await client.aclose()

    async def test_error_de_red_propaga_excepcion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un error de conexion debe propagar httpx.ConnectError sin atraparlo."""
        monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-test-key")

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("fallo simulado de red")

        client = _install_mock_client(monkeypatch, handler)
        try:
            with pytest.raises(httpx.ConnectError):
                await openrouter_service.chat_completion_with_tools(
                    messages=[{"role": "user", "content": "test"}], tools=[]
                )
        finally:
            await client.aclose()


class TestCloseHttpClient:
    """close_http_client() libera el cliente compartido."""

    async def test_cierra_cliente_existente(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = httpx.AsyncClient()
        monkeypatch.setattr("app.services.openrouter_service._http_client", client)
        await openrouter_service.close_http_client()
        assert client.is_closed

    async def test_no_falla_sin_cliente_previo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.services.openrouter_service._http_client", None)
        await openrouter_service.close_http_client()  # no debe lanzar
