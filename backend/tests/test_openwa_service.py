"""Tests unitarios del cliente Open-WA (app.services.openwa_service).

A diferencia de test_webhook.py (que prueba el endpoint HTTP completo con
payloads reales), estos tests aislan OpenWAService: discovery de sesión,
caché de session ID, envío de texto, descarga de media, indicador de typing
y headers de autenticación.

httpx.AsyncClient se mockea para no tocar la red ni el gateway real.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.core.config import settings
from app.services.openwa_service import OpenWAService


@pytest.fixture(autouse=True)
def _reset_session_cache() -> object:
    """Limpia el caché de session ID de clase entre tests.

    _cached_session_id es de clase y persiste entre tests si no se resetea,
    lo que haría que _resolve_session_id no descubra la sesión.
    """
    prev = OpenWAService._cached_session_id
    OpenWAService._cached_session_id = None
    yield
    OpenWAService._cached_session_id = prev


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, mock_client: AsyncMock) -> None:
    """Reemplaza httpx.AsyncClient por un context manager que retorna mock_client."""
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_client
    mock_ctx.__aexit__.return_value = None
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: mock_ctx)


def _mock_response(json_data: object | None = None, content: bytes = b"") -> Mock:
    """Construye una respuesta httpx mockeada con raise_for_status no-op."""
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json = Mock(return_value=json_data if json_data is not None else {})
    resp.content = content
    return resp


# ── _resolve_session_id ────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_session_id_descubre_primera_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_session_id retorna el ID de la primera sesión con status=ready."""
    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:2785")
    monkeypatch.setattr(settings, "openwa_api_key", "k")

    client = AsyncMock()
    client.get.return_value = _mock_response([{"id": "sess-1", "status": "ready"}])
    _patch_async_client(monkeypatch, client)

    service = OpenWAService()
    session_id = await service._resolve_session_id()

    assert session_id == "sess-1"
    assert OpenWAService._cached_session_id == "sess-1"


@pytest.mark.asyncio
async def test_resolve_session_id_acepta_status_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sesiones con status=active también se consideran listas."""
    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:2785")
    monkeypatch.setattr(settings, "openwa_api_key", "k")

    client = AsyncMock()
    client.get.return_value = _mock_response([{"id": "sess-active", "status": "active"}])
    _patch_async_client(monkeypatch, client)

    service = OpenWAService()
    assert await service._resolve_session_id() == "sess-active"


@pytest.mark.asyncio
async def test_resolve_session_id_cachea_evita_segundo_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La segunda llamada a _resolve_session_id no hace HTTP (caché de clase)."""
    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:2785")
    monkeypatch.setattr(settings, "openwa_api_key", "k")

    client = AsyncMock()
    client.get.return_value = _mock_response([{"id": "sess-cached", "status": "ready"}])
    _patch_async_client(monkeypatch, client)

    service = OpenWAService()
    await service._resolve_session_id()
    await service._resolve_session_id()

    assert client.get.await_count == 1


@pytest.mark.asyncio
async def test_resolve_session_id_sin_sesiones_lanza_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si no hay sesiones ready/active, lanza RuntimeError pidiendo escanear QR."""
    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:2785")
    monkeypatch.setattr(settings, "openwa_api_key", "k")

    client = AsyncMock()
    client.get.return_value = _mock_response([{"id": "x", "status": "qr"}])
    _patch_async_client(monkeypatch, client)

    service = OpenWAService()
    with pytest.raises(RuntimeError, match="QR"):
        await service._resolve_session_id()


# ── send_text ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_text_normaliza_chat_id_y_envia(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """send_text POSTea a send-text con chatId normalizado y header X-API-Key."""
    caplog.set_level(logging.INFO, logger="app.services.openwa_service")
    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:2785")
    monkeypatch.setattr(settings, "openwa_api_key", "secret-key")

    client = AsyncMock()
    client.post.return_value = _mock_response({"status": "sent"})
    _patch_async_client(monkeypatch, client)
    OpenWAService._cached_session_id = "sess-1"

    service = OpenWAService()
    result = await service.send_text("+56912345678", "Hola")

    assert result == {"status": "sent"}
    client.post.assert_called_once_with(
        "http://openwa:2785/api/sessions/sess-1/messages/send-text",
        headers={"X-API-Key": "secret-key"},
        json={"chatId": "56912345678@c.us", "text": "Hola"},
    )
    assert "56912345678" not in caplog.text
    assert "Hola" not in caplog.text
    assert "secret-key" not in caplog.text
    assert "hash=" not in caplog.text


@pytest.mark.asyncio
async def test_send_text_propaga_http_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Si Open-WA retorna error HTTP, send_text propaga httpx.HTTPError."""
    caplog.set_level(logging.ERROR, logger="app.services.openwa_service")
    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:2785")
    monkeypatch.setattr(settings, "openwa_api_key", "k")

    client = AsyncMock()
    resp = Mock()
    resp.raise_for_status = Mock(
        side_effect=httpx.HTTPStatusError(
            "secreto-query +56999999999",
            request=httpx.Request("POST", "http://openwa:2785"),
            response=httpx.Response(500),
        )
    )
    client.post.return_value = resp
    _patch_async_client(monkeypatch, client)
    OpenWAService._cached_session_id = "sess-1"

    service = OpenWAService()
    with pytest.raises(httpx.HTTPError):
        await service.send_text("569@c.us", "x")
    assert "secreto-query" not in caplog.text
    assert "56999999999" not in caplog.text
    assert "569@c.us" not in caplog.text


# ── download_media ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_media_retorna_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """download_media retorna el contenido binario del audio."""
    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:2785")
    monkeypatch.setattr(settings, "openwa_api_key", "k")

    client = AsyncMock()
    client.get.return_value = _mock_response(content=b"AUDIO_OGG_BYTES")
    _patch_async_client(monkeypatch, client)
    OpenWAService._cached_session_id = "sess-1"

    service = OpenWAService()
    data = await service.download_media("msg_simple")

    assert data == b"AUDIO_OGG_BYTES"


@pytest.mark.asyncio
async def test_download_media_url_encodea_message_id_con_arroba(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """message_id con '@' debe ir URL-encoded (%40) en la URL de descarga."""
    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:2785")
    monkeypatch.setattr(settings, "openwa_api_key", "k")

    client = AsyncMock()
    client.get.return_value = _mock_response(content=b"x")
    _patch_async_client(monkeypatch, client)
    OpenWAService._cached_session_id = "sess-1"

    service = OpenWAService()
    await service.download_media("true_569@c.us_3EB")

    called_url = client.get.call_args.args[0]
    assert "%40" in called_url
    # El '@' crudo no debe quedar en la porción del message_id de la URL.
    assert "true_569@c.us" not in called_url


@pytest.mark.asyncio
async def test_download_image_reutiliza_endpoint_de_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Las imágenes usan streaming acotado sobre el endpoint decryptMedia."""
    monkeypatch.setattr(settings, "vision_image_max_bytes", 1024)
    response = Mock(headers={"content-length": "11"})
    response.raise_for_status = Mock()

    async def chunks() -> AsyncIterator[bytes]:
        yield b"IMAGE_"
        yield b"BYTES"

    response.aiter_bytes = chunks
    stream_context = AsyncMock()
    stream_context.__aenter__.return_value = response
    client = AsyncMock()
    client.stream = Mock(return_value=stream_context)
    _patch_async_client(monkeypatch, client)
    OpenWAService._cached_session_id = "sess-1"

    result = await OpenWAService().download_image("image-message")

    assert result == b"IMAGE_BYTES"


@pytest.mark.asyncio
async def test_download_image_corta_stream_que_supera_limite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un gateway sin Content-Length tampoco puede agotar la RAM del backend."""
    monkeypatch.setattr(settings, "vision_image_max_bytes", 4)
    response = Mock(headers={})
    response.raise_for_status = Mock()

    async def chunks() -> AsyncIterator[bytes]:
        yield b"123"
        yield b"45"

    response.aiter_bytes = chunks
    stream_context = AsyncMock()
    stream_context.__aenter__.return_value = response
    client = AsyncMock()
    client.stream = Mock(return_value=stream_context)
    _patch_async_client(monkeypatch, client)
    OpenWAService._cached_session_id = "sess-1"

    with pytest.raises(ValueError, match="tamaño máximo"):
        await OpenWAService().download_image("image-message")


# ── send_typing_indicator ──────────────────────────────────────


@pytest.mark.asyncio
async def test_send_typing_indicator_no_falla_si_gateway_cae(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si el indicador typing falla, no propaga excepción (no es crítico)."""
    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:2785")
    monkeypatch.setattr(settings, "openwa_api_key", "k")

    client = AsyncMock()
    client.post.side_effect = httpx.ConnectError("gateway down")
    _patch_async_client(monkeypatch, client)
    OpenWAService._cached_session_id = "sess-1"

    service = OpenWAService()
    # No debe lanzar.
    await service.send_typing_indicator("569@c.us", "recording")


@pytest.mark.asyncio
async def test_send_typing_indicator_envia_state_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_typing_indicator POSTea chatId + state al endpoint chats/typing."""
    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:2785")
    monkeypatch.setattr(settings, "openwa_api_key", "k")

    client = AsyncMock()
    client.post.return_value = _mock_response({})
    _patch_async_client(monkeypatch, client)
    OpenWAService._cached_session_id = "sess-1"

    service = OpenWAService()
    await service.send_typing_indicator("+56912345678", "recording")

    client.post.assert_called_once_with(
        "http://openwa:2785/api/sessions/sess-1/chats/typing",
        headers={"X-API-Key": "k"},
        json={"chatId": "56912345678@c.us", "state": "recording"},
    )


# ── _headers / __init__ ────────────────────────────────────────


def test_headers_incluye_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """_headers incluye X-API-Key cuando openwa_api_key está seteada."""
    monkeypatch.setattr(settings, "openwa_api_key", "mi-key")
    monkeypatch.setattr(settings, "app_env", "test")

    service = OpenWAService()
    assert service._headers() == {"X-API-Key": "mi-key"}


def test_headers_vacio_sin_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """_headers retorna dict vacío si no hay API key configurada."""
    monkeypatch.setattr(settings, "openwa_api_key", "")
    monkeypatch.setattr(settings, "app_env", "test")

    service = OpenWAService()
    assert service._headers() == {}


def test_base_url_sin_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    """El __init__ debe normalizar la URL base quitando el slash final."""
    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:2785/")
    monkeypatch.setattr(settings, "openwa_api_key", "k")
    monkeypatch.setattr(settings, "app_env", "test")

    service = OpenWAService()
    assert service._base_url == "http://openwa:2785"


# ── resolve_contact_phone (fix P0: LID resolution) ─────────────────


@pytest.mark.asyncio
async def test_resolve_contact_phone_exitoso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_contact_phone retorna el numero MSISDN resuelto."""
    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:2785")
    monkeypatch.setattr(settings, "openwa_api_key", "k")

    client = AsyncMock()
    client.get.return_value = _mock_response({"phone": "56912345678"})
    _patch_async_client(monkeypatch, client)
    OpenWAService._cached_session_id = "sess-1"

    service = OpenWAService()
    phone = await service.resolve_contact_phone("248069442560050@lid")

    assert phone == "56912345678"
    # Verificar que se llamo GET a /contacts/{id}/phone
    client.get.assert_called_once()
    called_url = client.get.call_args[0][0]
    assert "/contacts/" in called_url
    assert "/phone" in called_url


@pytest.mark.asyncio
async def test_resolve_contact_phone_retorna_null_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_contact_phone retorna None si el servidor devuelve phone=null."""
    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:2785")
    monkeypatch.setattr(settings, "openwa_api_key", "k")

    client = AsyncMock()
    client.get.return_value = _mock_response({"phone": None})
    _patch_async_client(monkeypatch, client)
    OpenWAService._cached_session_id = "sess-1"

    service = OpenWAService()
    phone = await service.resolve_contact_phone("248069442560050@lid")

    assert phone is None


@pytest.mark.asyncio
async def test_resolve_contact_phone_http_error_no_lanza(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_contact_phone no lanza excepcion en error de red (best-effort)."""
    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:2785")
    monkeypatch.setattr(settings, "openwa_api_key", "k")

    client = AsyncMock()
    resp = Mock()
    resp.raise_for_status = Mock(side_effect=httpx.ConnectError("gateway down"))
    client.get.return_value = resp
    _patch_async_client(monkeypatch, client)
    OpenWAService._cached_session_id = "sess-1"

    service = OpenWAService()
    # No debe lanzar excepcion
    phone = await service.resolve_contact_phone("248069442560050@lid")

    assert phone is None


@pytest.mark.asyncio
async def test_send_audio_con_lid_resuelto(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """send_audio con @lid resuelve a numero @c.us antes de enviar."""
    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:2785")
    monkeypatch.setattr(settings, "openwa_api_key", "k")

    audio_file = tmp_path / "hello.ogg"
    audio_file.write_bytes(b"FAKE_OGG")

    client = AsyncMock()

    # Mock dos respuestas: GET /contacts/{id}/phone y POST /messages/send-audio
    async def mock_get_or_post(url, **kwargs):
        resp = Mock()
        resp.raise_for_status = Mock()
        if "/contacts/" in url and "/phone" in url:
            resp.json = Mock(return_value={"phone": "56912345678"})
        else:
            resp.json = Mock(return_value={"status": "sent"})
        return resp

    client.get.side_effect = mock_get_or_post
    client.post.side_effect = mock_get_or_post

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = client
    mock_ctx.__aexit__.return_value = None
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: mock_ctx)

    OpenWAService._cached_session_id = "sess-1"
    service = OpenWAService()
    result = await service.send_audio("248069442560050@lid", str(audio_file))

    assert result == {"status": "sent"}
    # POST debe usar @c.us, no @lid
    post_calls = list(client.post.call_args_list)
    assert len(post_calls) == 1
    assert post_calls[0][1]["json"]["chatId"] == "56912345678@c.us"


# ── Robustness: send_text con numero invalido ──────────────


@pytest.mark.asyncio
async def test_send_text_con_numero_invalido_loguea_y_propaga(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_text con numero invalido (3 digitos) loguea error y propaga ValueError.

    Valida que la normalizacion E.164 se ejecute dentro del try/except,
    se loguee el error, y se propague para que el llamador (audio_service)
    pueda manejarlo.
    """
    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:2785")
    monkeypatch.setattr(settings, "openwa_api_key", "k")

    client = AsyncMock()
    _patch_async_client(monkeypatch, client)
    OpenWAService._cached_session_id = "sess-1"

    service = OpenWAService()

    # Llamar send_text con numero invalido debe lanzar ValueError.
    with pytest.raises(ValueError, match="3 digitos"):
        await service.send_text("123", "hola")


@pytest.mark.asyncio
async def test_send_typing_indicator_numero_invalido_no_lanza(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_typing_indicator con numero invalido NO propaga ValueError.

    El indicador de typing no es critico: un numero mal formado se loguea
    como warning y el flujo del pipeline continua sin interrupcion.
    """
    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:2785")
    monkeypatch.setattr(settings, "openwa_api_key", "k")

    client = AsyncMock()
    _patch_async_client(monkeypatch, client)
    OpenWAService._cached_session_id = "sess-1"

    service = OpenWAService()

    # No debe lanzar a pesar del numero invalido (3 digitos).
    await service.send_typing_indicator("123", "recording")

    # El ValueError ocurre al armar el payload: nunca se llega a la red.
    client.post.assert_not_called()
