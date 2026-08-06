"""Tests del endpoint webhook de WhatsApp (Open-WA).

Cubre: validacion HMAC, deteccion de tipo de mensaje,
procesamiento de audio en background, y manejo de errores.

Estructura real del payload de Open-WA verificada con trafico en vivo (2026-06-21).
"""

import hashlib
import hmac as hmac_mod
import json
import logging
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.services.tts_service import PiperModelNotFoundError


@pytest.fixture(autouse=True)
def _no_correr_pipeline_en_tests_de_endpoint(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Impide que los tests del ENDPOINT disparen el pipeline real en background.

    Los ``test_webhook_*`` verifican HMAC y routing, no el procesamiento. Desde
    que el webhook acepta mensajes de texto, esos payloads dejaron de ignorarse
    y arrancaban Whisper y el LLM de verdad: el archivo paso de 0.3s a 113s.

    Los ``test_audio_service_*`` llaman a los metodos directamente y si quieren
    la implementacion real, asi que quedan fuera.
    """
    if not request.node.name.startswith("test_webhook_"):
        return

    async def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("app.services.audio_service.AudioService.process_text", _noop)
    monkeypatch.setattr("app.services.audio_service.AudioService.process_audio", _noop)


def _load_fixture(name: str) -> dict[str, object]:
    """Carga un payload de prueba desde fixtures/openwa_webhook_payload.json."""
    fixture_path = Path(__file__).parent / "fixtures" / "openwa_webhook_payload.json"
    with open(fixture_path) as f:
        data: dict[str, object] = json.load(f)
    payload = data[name]
    assert isinstance(payload, dict), f"Fixture '{name}' no es un dict"
    return dict(payload)


def _compute_hmac(body: bytes, secret: str) -> str:
    """Calcula HMAC-SHA256 como Open-WA: formato 'sha256=<hex>'."""
    hex_digest = hmac_mod.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return f"sha256={hex_digest}"


# ── Tests de validacion HMAC ──────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_sin_firma_retorna_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhook sin header X-OpenWA-Signature debe ser rechazado con 401."""
    from app.main import app

    monkeypatch.setattr(settings, "openwa_webhook_secret", "test-secret")

    payload = _load_fixture("voice_message")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/webhook/whatsapp",
            json=payload,
            # Sin header X-OpenWA-Signature
        )

    assert response.status_code == 401
    assert "Firma HMAC requerida" in response.json()["detail"]


@pytest.mark.asyncio
async def test_webhook_firma_invalida_retorna_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhook con firma HMAC incorrecta debe ser rechazado con 401."""
    from app.main import app

    monkeypatch.setattr(settings, "openwa_webhook_secret", "test-secret")

    payload = _load_fixture("voice_message")
    body = json.dumps(payload).encode("utf-8")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/webhook/whatsapp",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-OpenWA-Signature": "firma-inventada-12345",
            },
        )

    assert response.status_code == 401
    assert "Firma HMAC inválida" in response.json()["detail"]


@pytest.mark.asyncio
async def test_webhook_firma_mayuscula_es_valida(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhook con firma HMAC en mayusculas debe ser aceptado (case-insensitive)."""
    from app.main import app

    monkeypatch.setattr(settings, "openwa_webhook_secret", "test-secret")
    monkeypatch.setattr(settings, "openwa_api_key", "test-api-key")

    payload = _load_fixture("text_message")
    body = json.dumps(payload).encode("utf-8")
    signature = _compute_hmac(body, "test-secret").upper()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/webhook/whatsapp",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-OpenWA-Signature": signature,
            },
        )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_webhook_firma_valida_voice_retorna_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhook con firma HMAC correcta y mensaje voice debe ser aceptado.

    El message_id en la respuesta debe ser el sanitizado del fixture.
    BackgroundTasks no se ejecuta sincronicamente con ASGITransport.
    """
    from app.main import app

    monkeypatch.setattr(settings, "openwa_webhook_secret", "test-secret")
    monkeypatch.setattr(settings, "openwa_api_key", "test-api-key")

    payload = _load_fixture("voice_message")
    body = json.dumps(payload).encode("utf-8")
    signature = _compute_hmac(body, "test-secret")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/webhook/whatsapp",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-OpenWA-Signature": signature,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "received"
    # El message_id debe ser el ID sanitizado del fixture
    assert data["message_id"] == "false_test_lid_001"


# ── Tests de tipo de mensaje ──────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_mensaje_texto_retorna_200_ignorado(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mensaje de texto (type=text) se procesa como consulta, no se ignora.

    El productor no siempre puede mandar audio (lugar ruidoso, reunion, mala
    senal), asi que el texto es una via de entrada de primera clase. Antes el
    webhook lo descartaba con reason="mensaje_no_audio".
    """
    from app.main import app

    caplog.set_level(logging.INFO, logger="app.api.webhooks")
    monkeypatch.setattr(settings, "openwa_webhook_secret", "test-secret")

    procesados: list[str] = []

    async def fake_process_text(
        _self: object, texto: str, chat_id: str, request_id: str
    ) -> None:
        procesados.append(texto)

    monkeypatch.setattr(
        "app.services.audio_service.AudioService.process_text", fake_process_text
    )

    payload = _load_fixture("text_message")
    body = json.dumps(payload).encode("utf-8")
    signature = _compute_hmac(body, "test-secret")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/webhook/whatsapp",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-OpenWA-Signature": signature,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "received"
    assert procesados == ["¿A cuanto esta la papa?"]
    assert "¿A cuanto esta la papa?" not in caplog.text
    assert "248069442560050" not in caplog.text
    assert "chat_id_hash" not in caplog.text


@pytest.mark.asyncio
async def test_webhook_mensaje_ubicacion_extrae_coordenadas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un pin location se delega con lat/lng y no cae en el descarte de audio."""
    from app.main import app

    monkeypatch.setattr(settings, "openwa_webhook_secret", "test-secret")
    recibidos: list[tuple[float, float, str]] = []

    async def fake_process_location(
        _self: object,
        lat: float,
        lng: float,
        chat_id: str,
        request_id: str,
    ) -> None:
        recibidos.append((lat, lng, chat_id))

    monkeypatch.setattr(
        "app.services.audio_service.AudioService.process_location",
        fake_process_location,
    )

    payload = _load_fixture("location_message")
    body = json.dumps(payload).encode("utf-8")
    signature = _compute_hmac(body, "test-secret")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/webhook/whatsapp",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-OpenWA-Signature": signature,
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "received"
    assert recibidos == [(-38.2412, -72.6911, "248069442560050@lid")]


def test_extract_location_rechaza_coordenadas_fuera_de_rango() -> None:
    """El payload no puede usar un pin que OpenMeteo rechazaría."""
    from app.api.webhooks import _extract_location
    from app.schemas.webhook import WebhookPayload

    payload = WebhookPayload.model_validate(
        {"data": {"type": "location", "lat": 91, "lng": -72.0}}
    )

    assert _extract_location(payload) is None


@pytest.mark.asyncio
async def test_audio_service_location_informa_consentimiento_faltante(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WhatsApp recibe una explicación honesta cuando el gate está apagado."""
    from app.services.audio_service import AudioService
    from app.services.openwa_service import OpenWAService

    mensajes: list[str] = []

    async def fake_send_text(_self: OpenWAService, _target: str, message: str) -> dict[str, object]:
        mensajes.append(message)
        return {"status": "sent"}

    monkeypatch.setattr(settings, "location_sharing_enabled", False)
    monkeypatch.setattr(OpenWAService, "send_text", fake_send_text)
    monkeypatch.setattr(OpenWAService, "send_typing_indicator", AsyncMock())

    await AudioService().process_location(
        lat=-38.24,
        lng=-72.69,
        chat_id="56912345678@c.us",
        request_id="request-location-consent",
    )

    assert len(mensajes) == 1
    assert "consentimiento explícito" in mensajes[0]
    assert "no hay un opt-in por voz" in mensajes[0]
    assert "No guardé tu ubicación" in mensajes[0]


@pytest.mark.asyncio
async def test_webhook_mensaje_image_ignorado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mensaje con type=image debe ser ignorado."""
    from app.main import app

    monkeypatch.setattr(settings, "openwa_webhook_secret", "test-secret")

    payload = _load_fixture("image_message")
    body = json.dumps(payload).encode("utf-8")
    signature = _compute_hmac(body, "test-secret")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/webhook/whatsapp",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-OpenWA-Signature": signature,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ignored"


@pytest.mark.asyncio
async def test_webhook_mensaje_voice_sin_media_ignorado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mensaje voice sin media.data debe ser ignorado."""
    from app.main import app

    monkeypatch.setattr(settings, "openwa_webhook_secret", "test-secret")

    payload = _load_fixture("voice_message_sin_media")
    body = json.dumps(payload).encode("utf-8")
    signature = _compute_hmac(body, "test-secret")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/webhook/whatsapp",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-OpenWA-Signature": signature,
            },
        )

    assert response.status_code == 200
    data = response.json()
    # voice pero sin media data -> se ignora
    assert data["status"] == "ignored"
    assert data["reason"] == "voice_sin_media"


# ── Tests de payload invalido ─────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_payload_invalido_retorna_200_ignorado(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Payload que no cumple el esquema debe retornar 200 con status=ignored.

    El endpoint nunca retorna 4xx por payload mal formado porque
    Open-WA no reenvia webhooks. Preferimos loggear y seguir.
    """
    from app.main import app

    caplog.set_level(logging.WARNING, logger="app.api.webhooks")
    monkeypatch.setattr(settings, "openwa_webhook_secret", "test-secret")

    # data mal tipado -> ValidationError
    payload = {"event": "message.received", "data": "esto_no_es_un_objeto"}
    body = json.dumps(payload).encode("utf-8")
    signature = _compute_hmac(body, "test-secret")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/webhook/whatsapp",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-OpenWA-Signature": signature,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ignored"
    assert data["reason"] == "payload_invalido"
    assert "esto_no_es_un_objeto" not in caplog.text


@pytest.mark.asyncio
async def test_webhook_body_no_json_retorna_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Body que no es JSON debe ser rechazado con 400 por la dependencia HMAC."""
    from app.main import app

    monkeypatch.setattr(settings, "openwa_webhook_secret", "test-secret")

    body = b"esto no es json"
    signature = _compute_hmac(body, "test-secret")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/webhook/whatsapp",
            content=body,
            headers={
                "Content-Type": "text/plain",
                "X-OpenWA-Signature": signature,
            },
        )

    assert response.status_code == 400
    assert "Body debe ser JSON válido" in response.json()["detail"]


# ── Tests de seguridad ────────────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_secret_vacio_acepta_sin_validar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En dev sin webhook secret configurado, se aceptan requests sin firma."""
    from app.main import app

    monkeypatch.setattr(settings, "openwa_webhook_secret", "")

    payload = _load_fixture("text_message")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/webhook/whatsapp",
            json=payload,
            # Sin header de firma
        )

    # Sin secret configurado, el webhook se acepta (dev mode)
    assert response.status_code == 200


# ── Tests del servicio Open-WA ────────────────────────────────


@pytest.mark.asyncio
async def test_openwa_send_audio_resuelve_lid_a_telefono(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """send_audio resuelve @lid a telefono real antes de enviar (fix P0).

    Cuando llega un @lid (contacto nuevo), resolve_contact_phone() lo mapea
    a numero MSISDN real via OpenWA API. Si la resolucion es exitosa, se usa
    f"{telefono}@c.us" como target final.
    """
    from app.services.openwa_service import OpenWAService

    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:2785")
    monkeypatch.setattr(settings, "openwa_api_key", "test-api-key")

    mock_client = AsyncMock()

    # Mock TWO HTTP responses:
    # 1. GET /contacts/{id}/phone (resolucion de LID) -> retorna numero real
    # 2. POST /messages/send-audio (envio)
    resolve_response = AsyncMock()
    resolve_response.raise_for_status = Mock()
    resolve_response.json = Mock(return_value={"phone": "56912345678"})

    send_response = AsyncMock()
    send_response.raise_for_status = Mock()
    send_response.json = Mock(return_value={"status": "sent"})

    # Alterna entre dos responses segun la URL
    async def mock_post_or_get(url, **kwargs):
        if "/contacts/" in url and "/phone" in url:
            return resolve_response
        elif "/messages/send-audio" in url:
            return send_response
        return send_response

    mock_client.post.side_effect = mock_post_or_get
    mock_client.get.side_effect = mock_post_or_get

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_client
    mock_ctx.__aexit__.return_value = None
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: mock_ctx)

    audio_file = tmp_path / "hello.ogg"
    audio_file.write_bytes(b"FAKE_HELLO_OGG")

    # Setear cache de clase para evitar HTTP discovery en _resolve_session_id
    prev_cache = OpenWAService._cached_session_id
    OpenWAService._cached_session_id = "test-session-id"
    try:
        service = OpenWAService()
        result = await service.send_audio("248069442560050@lid", str(audio_file))
    finally:
        OpenWAService._cached_session_id = prev_cache

    assert result == {"status": "sent"}
    # Verificar que la llamada POST usa el numero resuelto (@c.us, no @lid)
    post_calls = list(mock_client.post.call_args_list)
    assert len(post_calls) == 1
    sent_payload = post_calls[0][1]["json"]
    assert sent_payload["chatId"] == "56912345678@c.us"
    assert sent_payload["base64"] == "RkFLRV9IRUxMT19PR0c="
    assert sent_payload["mimetype"] == "audio/ogg"


def test_openwa_phone_to_chat_id_normaliza() -> None:
    """phone_to_chat_id soporta E.164, @c.us y @lid.

    La resolucion de @lid a numero real se hace en send_audio/send_text
    via resolve_contact_phone, no en phone_to_chat_id (que es un formatador simple).
    """
    from app.services.openwa_service import phone_to_chat_id

    # E.164 -> @c.us
    assert phone_to_chat_id("+56912345678") == "56912345678@c.us"
    assert phone_to_chat_id("56912345678") == "56912345678@c.us"
    # @c.us -> directo
    assert phone_to_chat_id("56912345678@c.us") == "56912345678@c.us"
    # @lid -> directo (sin transformacion)
    assert phone_to_chat_id("248069442560050@lid") == "248069442560050@lid"


# ── Tests de funciones auxiliares ─────────────────────────────


def test_is_voice_message_detecta_voice() -> None:
    """_is_voice_message retorna True para mensajes type=voice con media."""
    from app.api.webhooks import _is_voice_message
    from app.schemas.webhook import WebhookPayload

    raw = _load_fixture("voice_message")
    payload = WebhookPayload.model_validate(raw)
    assert _is_voice_message(payload) is True


def test_is_voice_message_rechaza_texto() -> None:
    """_is_voice_message retorna False para mensajes type=text."""
    from app.api.webhooks import _is_voice_message
    from app.schemas.webhook import WebhookPayload

    raw = _load_fixture("text_message")
    payload = WebhookPayload.model_validate(raw)
    assert _is_voice_message(payload) is False


def test_is_voice_message_rechaza_image() -> None:
    """_is_voice_message retorna False para mensajes type=image."""
    from app.api.webhooks import _is_voice_message
    from app.schemas.webhook import WebhookPayload

    raw = _load_fixture("image_message")
    payload = WebhookPayload.model_validate(raw)
    assert _is_voice_message(payload) is False


def test_is_voice_message_acepta_voice_sin_media() -> None:
    """_is_voice_message retorna True aunque falte media (solo verifica type)."""
    from app.api.webhooks import _is_voice_message
    from app.schemas.webhook import WebhookPayload

    raw = _load_fixture("voice_message_sin_media")
    payload = WebhookPayload.model_validate(raw)
    assert _is_voice_message(payload) is True


def test_get_audio_duration_ms_overflow_no_crash(tmp_path: Path) -> None:
    """get_audio_duration_ms no debe explotar con OverflowError."""
    from app.services.audio_service import get_audio_duration_ms

    # Path inexistente -> ffprobe falla -> CalledProcessError -> 0
    result = get_audio_duration_ms(tmp_path / "no-existe.wav")
    assert result == 0


def test_max_audio_size_constant() -> None:
    """_MAX_AUDIO_SIZE_BYTES debe ser > 0 y representar 25 MB."""
    from app.services.audio_service import _MAX_AUDIO_SIZE_BYTES

    assert _MAX_AUDIO_SIZE_BYTES == 25 * 1024 * 1024
    assert _MAX_AUDIO_SIZE_BYTES > 0


def test_sanitize_message_id_preserva_alfanumerico() -> None:
    """sanitize_message_id preserva caracteres seguros (alfanumericos, guiones, underscores)."""
    from app.services.audio_service import sanitize_message_id

    assert sanitize_message_id("msg_abc-123_test") == "msg_abc-123_test"


def test_sanitize_message_id_reemplaza_arroba() -> None:
    """sanitize_message_id hashea el telefono y preserva @c.us (dominio, no PII)."""
    from app.services.audio_service import sanitize_message_id

    result = sanitize_message_id("true_56912345678@c.us")
    # El telefono 56912345678 debe estar hasheado (no aparece en claro)
    assert "56912345678" not in result
    # @c.us es el dominio de WhatsApp, no es PII — se preserva
    assert "@c.us" in result
    # Estructura: true_<hash_12_chars>@c.us
    assert result.startswith("true_")
    assert result.endswith("@c.us")


def test_sanitize_message_id_whatsapp_id_real() -> None:
    """sanitize_message_id hashea TODOS los telefonos en IDs con multiples ocurrencias."""
    from app.services.audio_service import sanitize_message_id

    result = sanitize_message_id("true_56912345678@c.us_3EB0A5F6C8D9_56912345678@c.us")
    # Ningun telefono aparece en claro
    assert "56912345678" not in result
    # @c.us se preserva en ambas ocurrencias
    assert result.count("@c.us") == 2
    assert result.startswith("true_")
    assert result.endswith("@c.us")
    import hashlib
    expected_hash = hashlib.sha256(b"56912345678").hexdigest()[:12]
    assert result == f"true_{expected_hash}@c.us_3EB0A5F6C8D9_{expected_hash}@c.us"


def test_sanitize_message_id_vacio_retorna_unknown() -> None:
    """sanitize_message_id retorna 'unknown' si el string esta vacio."""
    from app.services.audio_service import sanitize_message_id

    assert sanitize_message_id("") == "unknown"


def test_sanitize_message_id_solo_especiales_retorna_underscores() -> None:
    """sanitize_message_id reemplaza caracteres especiales por '_'."""
    from app.services.audio_service import sanitize_message_id

    result = sanitize_message_id("@@@!!!")
    assert result == "______"
    assert result != "unknown"


def test_validate_path_in_audio_dir_ruta_valida() -> None:
    """validate_path_in_audio_dir acepta un path dentro del directorio de audio."""
    import tempfile
    from pathlib import Path

    from app.services.audio_service import validate_path_in_audio_dir

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_dir = Path(tmpdir).resolve()
        file_path = audio_dir / "test_audio.wav"
        result = validate_path_in_audio_dir(file_path, audio_dir)
        assert result == file_path.resolve()


def test_validate_path_in_audio_dir_path_traversal_detectado() -> None:
    """validate_path_in_audio_dir lanza ValueError si el path esta fuera del directorio base."""
    import tempfile
    from pathlib import Path

    import pytest

    from app.services.audio_service import validate_path_in_audio_dir

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_dir = Path(tmpdir).resolve()
        traversal = audio_dir / ".." / "etc" / "passwd"

        with pytest.raises(ValueError, match="Path fuera del directorio de audio"):
            validate_path_in_audio_dir(traversal, audio_dir)


def test_extract_audio_bytes_returns_decoded() -> None:
    """_extract_audio_bytes decodifica base64 del payload."""
    import base64

    from app.api.webhooks import _extract_audio_bytes
    from app.schemas.webhook import WebhookPayload

    raw = _load_fixture("voice_message")
    payload = WebhookPayload.model_validate(raw)
    result = _extract_audio_bytes(payload)
    assert result is not None
    expected = base64.b64decode(raw["data"]["media"]["data"])  # type: ignore[index]
    assert result == expected


def test_extract_audio_bytes_voice_sin_media_retorna_none() -> None:
    """_extract_audio_bytes retorna None si el mensaje voice no tiene media."""
    from app.api.webhooks import _extract_audio_bytes
    from app.schemas.webhook import WebhookPayload

    raw = _load_fixture("voice_message_sin_media")
    payload = WebhookPayload.model_validate(raw)
    assert _extract_audio_bytes(payload) is None


# ── Tests de AudioService.process_audio ────────────────────


@pytest.mark.asyncio
async def test_audio_service_process_audio_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AudioService.process_audio guarda, convierte, envia respuesta y limpia sin errores.

    Usa inyeccion de audio_temp_dir en vez de monkeypatch sobre
    _get_audio_temp_dir (P1-5 DI).
    """
    from app.services.audio_service import AudioService

    caplog.set_level(logging.INFO, logger="app.services.audio_service")
    fake_ogg = b"FAKE_OGG_DATA"

    # Mock convert_ogg_to_wav para no ejecutar ffmpeg
    def fake_convert(input_path: Path, output_path: Path) -> None:
        output_path.write_bytes(b"FAKE_WAV_DATA")

    monkeypatch.setattr(
        "app.services.audio_service.convert_ogg_to_wav",
        fake_convert,
    )

    # Mock get_audio_duration_ms para no ejecutar ffprobe
    monkeypatch.setattr(
        "app.services.audio_service.get_audio_duration_ms",
        lambda wav_path: 5000,
    )

    # Crear hello.ogg falso y apuntar la constante al directorio tmp
    hello_ogg = tmp_path / "hello.ogg"
    hello_ogg.write_bytes(b"FAKE_HELLO_OGG")
    monkeypatch.setattr("app.services.audio_service._HELLO_OGG_PATH", hello_ogg)

    # Capturar llamada a send_audio sin llamar a Open-WA real
    send_audio_calls: list[tuple[str, str]] = []

    async def fake_send_audio(
        _self: object, target: str, audio_path: str, caption: str | None = None
    ) -> dict[str, object]:
        send_audio_calls.append((target, audio_path))
        return {"status": "sent"}

    monkeypatch.setattr(
        "app.services.openwa_service.OpenWAService.send_audio",
        fake_send_audio,
    )

    _mock_whisper_transcribe(monkeypatch)
    _mock_llm_answer(monkeypatch)
    _mock_tts_fallback(monkeypatch)

    # P1-5: Inyectar audio_temp_dir en vez de monkeypatch sobre _get_audio_temp_dir
    service = AudioService(audio_temp_dir=tmp_path)
    await service.process_audio(
        audio_bytes=fake_ogg,
        chat_id="248069442560050@lid",
        request_id="test-request-id",
    )

    # Verificar limpieza: el finally elimina ogg y wav temporales siempre
    wav_files = list(tmp_path.glob("*.wav"))
    ogg_files = [f for f in tmp_path.glob("*.ogg") if f.name != "hello.ogg"]
    assert len(wav_files) == 0
    assert len(ogg_files) == 0

    # Verificar que se envio hello.ogg al chatId correcto (issue #12)
    assert len(send_audio_calls) == 1
    assert send_audio_calls[0][0] == "248069442560050@lid"
    assert send_audio_calls[0][1] == str(hello_ogg)
    assert "248069442560050" not in caplog.text
    assert str(tmp_path) not in caplog.text
    assert "chat_id_hash" not in caplog.text


@pytest.mark.asyncio
async def test_audio_service_process_audio_tts_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AudioService.process_audio con TTS exitoso.

    Verifica que cuando TTSService.synthesize retorna un path valido:
    (a) send_audio recibe ese path exacto,
    (b) el archivo se elimina despues del envio (finally cleanup),
    (c) NO se usa hello.ogg.
    """
    from app.services.audio_service import AudioService

    fake_ogg = b"FAKE_OGG_DATA"

    def fake_convert(input_path: Path, output_path: Path) -> None:
        output_path.write_bytes(b"FAKE_WAV_DATA")

    monkeypatch.setattr("app.services.audio_service.convert_ogg_to_wav", fake_convert)
    monkeypatch.setattr("app.services.audio_service.get_audio_duration_ms", lambda wav_path: 5000)

    _mock_whisper_transcribe(monkeypatch)
    _mock_llm_answer(monkeypatch)

    # Mock TTS EXITOSO — retorna un OGG falso
    tts_ogg_path = _mock_tts_success(monkeypatch, tmp_path)

    # Capturar llamada a send_audio
    send_audio_calls: list[tuple[str, str]] = []

    async def fake_send_audio(
        _self: object, target: str, audio_path: str, caption: str | None = None
    ) -> dict[str, object]:
        send_audio_calls.append((target, audio_path))
        return {"status": "sent"}

    monkeypatch.setattr(
        "app.services.openwa_service.OpenWAService.send_audio",
        fake_send_audio,
    )

    service = AudioService(audio_temp_dir=tmp_path)
    await service.process_audio(
        audio_bytes=fake_ogg,
        chat_id="248069442560050@lid",
        request_id="test-tts-success",
    )

    # Con la DB aislada el productor es primer contacto, asi que el pipeline
    # envia la bienvenida ademas de la respuesta. Lo que este test verifica es
    # el envio de la RESPUESTA (el ultimo), y que ningun envio use hello.ogg.
    assert send_audio_calls, "no se envio ningun audio"
    target, audio_path = send_audio_calls[-1]
    assert target == "248069442560050@lid"
    assert audio_path == tts_ogg_path
    assert all("hello.ogg" not in path for _, path in send_audio_calls)

    # Verificar limpieza: no quedan WAVs ni OGGs temporales
    wav_files = list(tmp_path.glob("*.wav"))
    ogg_files = [f for f in tmp_path.glob("*.ogg") if f.name != "hello.ogg"]
    assert len(wav_files) == 0
    assert len(ogg_files) == 0


# monkeypatch reemplaza el metodo en la clase. Al llamar inst.method(arg),
# Python pasa self automaticamente (las funciones son descriptores).
# Por eso fake_transcribe recibe _self como primer parametro.
def _mock_tts_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mockea TTSService.synthesize para que falle con PiperModelNotFoundError.

    Esto fuerza el fallback a hello.ogg en tests que verifican el
    comportamiento degradado del pipeline cuando TTS no esta disponible
    (modelo no descargado, primer deploy, etc).
    """

    def fake_synthesize_fail(
        _self: object, text: str, output_dir: str | Path | None = None
    ) -> str:
        raise PiperModelNotFoundError(
            "Modelo Piper no encontrado (mock para test)"
        )

    monkeypatch.setattr(
        "app.services.pipeline_service.TTSService.synthesize",
        fake_synthesize_fail,
    )


def _mock_tts_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """Mockea TTSService.synthesize para retornar un OGG valido falso.

    Retorna la ruta al OGG que se "sintetizo" para que el test pueda
    verificar que send_audio recibe el path correcto.
    """
    fake_ogg_path = tmp_path / "tts_mock_output.ogg"
    fake_ogg_path.write_bytes(b"FAKE_TTS_OGG")

    def fake_synthesize_success(
        _self: object, text: str, output_dir: str | Path | None = None
    ) -> str:
        return str(fake_ogg_path)

    monkeypatch.setattr(
        "app.services.pipeline_service.TTSService.synthesize",
        fake_synthesize_success,
    )
    return str(fake_ogg_path)


def _mock_llm_answer(monkeypatch: pytest.MonkeyPatch, text: str = "Respuesta mock del LLM") -> None:
    """Mockea llm_service.answer para evitar cargar el modelo Qwen2.5-3B real.

    El modelo real pesa 2GB y su carga toma ~10-15s en CPU. Los tests
    de audio_service solo verifican que el pipeline orquesta las etapas
    sin importar el contenido de la respuesta del LLM.
    """

    async def fake_answer(*args: object, **kwargs: object) -> str:
        # Acepta cualquier firma de answer() (query_text, history, phone_hash,
        # cultivos, ...) para no romperse si el pipeline agrega parámetros.
        return text

    monkeypatch.setattr(
        "app.services.llm_service.answer",
        fake_answer,
    )


def _mock_whisper_transcribe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mockea WhisperService.transcribe para tests de audio_service.

    Agrega monkeypatch.setattr sobre el metodo transcribe en el modulo
    pipeline_service para evitar cargar el modelo real.
    """

    def fake_transcribe(_self: object, audio_path: str) -> dict[str, object]:
        return {
            "text": "Hola esta es una prueba",
            "language": "es",
            "segments": [],
            "duration_ms": 100,
        }

    monkeypatch.setattr(
        "app.services.pipeline_service.WhisperService.transcribe",
        fake_transcribe,
    )


@pytest.mark.asyncio
async def test_audio_service_hello_ogg_no_existe_no_crashea(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Si hello.ogg no existe, process_audio loguea warning pero no falla."""
    from app.services.audio_service import AudioService

    def fake_convert(input_path: Path, output_path: Path) -> None:
        output_path.write_bytes(b"FAKE_WAV_DATA")

    monkeypatch.setattr("app.services.audio_service.convert_ogg_to_wav", fake_convert)
    monkeypatch.setattr("app.services.audio_service.get_audio_duration_ms", lambda wav_path: 3000)
    _mock_whisper_transcribe(monkeypatch)
    _mock_llm_answer(monkeypatch)
    _mock_tts_fallback(monkeypatch)

    # Apuntar a un archivo que NO existe
    monkeypatch.setattr(
        "app.services.audio_service._HELLO_OGG_PATH",
        tmp_path / "no-existe.ogg",
    )

    send_audio_called = False

    async def fake_send_audio(
        _self: object, target: str, audio_path: str, caption: str | None = None
    ) -> dict[str, object]:
        nonlocal send_audio_called
        send_audio_called = True
        return {}

    monkeypatch.setattr("app.services.openwa_service.OpenWAService.send_audio", fake_send_audio)

    service = AudioService(audio_temp_dir=tmp_path)
    await service.process_audio(
        audio_bytes=b"FAKE_OGG_DATA",
        chat_id="248069442560050@lid",
        request_id="req-id",
    )

    # No debe llamar send_audio si el archivo no existe
    assert not send_audio_called
    # El finally limpia el .wav siempre, incluso cuando hello.ogg no existe
    assert len(list(tmp_path.glob("*.wav"))) == 0


@pytest.mark.asyncio
async def test_audio_service_send_audio_falla_logs_pero_no_crashea(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Si send_audio lanza HTTPError, process_audio loguea el error sin propagar."""
    from app.services.audio_service import AudioService

    caplog.set_level(logging.INFO, logger="app.services.audio_service")
    def fake_convert(input_path: Path, output_path: Path) -> None:
        output_path.write_bytes(b"FAKE_WAV_DATA")

    monkeypatch.setattr("app.services.audio_service.convert_ogg_to_wav", fake_convert)
    monkeypatch.setattr("app.services.audio_service.get_audio_duration_ms", lambda wav_path: 3000)
    _mock_whisper_transcribe(monkeypatch)
    _mock_llm_answer(monkeypatch)
    _mock_tts_fallback(monkeypatch)

    hello_ogg = tmp_path / "hello.ogg"
    hello_ogg.write_bytes(b"FAKE_HELLO_OGG")
    monkeypatch.setattr("app.services.audio_service._HELLO_OGG_PATH", hello_ogg)

    async def fake_send_audio_error(
        _self: object, target: str, audio_path: str, caption: str | None = None
    ) -> dict[str, object]:
        raise httpx.ConnectError(
            f"secreto-audio target={target} path={audio_path}"
        )

    monkeypatch.setattr(
        "app.services.openwa_service.OpenWAService.send_audio",
        fake_send_audio_error,
    )

    service = AudioService(audio_temp_dir=tmp_path)
    # No debe lanzar excepcion — el except captura el ConnectError
    await service.process_audio(
        audio_bytes=b"FAKE_OGG_DATA",
        chat_id="248069442560050@lid",
        request_id="req-id",
    )
    assert "secreto-audio" not in caplog.text
    assert "248069442560050" not in caplog.text
    assert str(tmp_path) not in caplog.text
    assert "chat_id_hash" not in caplog.text


@pytest.mark.asyncio
async def test_audio_service_process_audio_error_convert_limpia_archivos(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Si convert_ogg_to_wav falla, el finally limpia archivos temporales."""
    from app.services.audio_service import AudioService

    # Mock convert que falla
    def fake_convert_error(input_path: Path, output_path: Path) -> None:
        raise subprocess.CalledProcessError(1, ["ffmpeg"], stderr=b"error")

    monkeypatch.setattr(
        "app.services.audio_service.convert_ogg_to_wav",
        fake_convert_error,
    )

    service = AudioService(audio_temp_dir=tmp_path)
    await service.process_audio(
        audio_bytes=b"FAKE_OGG_DATA",
        chat_id="248069442560050@lid",
        request_id="test-request-id",
    )

    # No deben quedar archivos huerfanos despues del error
    ogg_files = list(tmp_path.glob("*.ogg"))
    wav_files = list(tmp_path.glob("*.wav"))
    assert len(ogg_files) == 0
    assert len(wav_files) == 0


@pytest.mark.asyncio
async def test_audio_service_process_audio_audio_excede_tamano(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Audio que excede _MAX_AUDIO_SIZE_BYTES es rechazado sin crear archivos."""
    from app.services.audio_service import _MAX_AUDIO_SIZE_BYTES, AudioService

    # Audio que excede el limite
    fake_ogg_large = b"X" * (_MAX_AUDIO_SIZE_BYTES + 1)

    service = AudioService(audio_temp_dir=tmp_path)
    await service.process_audio(
        audio_bytes=fake_ogg_large,
        chat_id="248069442560050@lid",
        request_id="test-request-id",
    )

    # No deben crearse archivos porque el audio fue rechazado por tamano
    ogg_files = list(tmp_path.glob("*.ogg"))
    wav_files = list(tmp_path.glob("*.wav"))
    assert len(ogg_files) == 0
    assert len(wav_files) == 0


@pytest.mark.asyncio
async def test_audio_service_process_audio_audio_largo_omite_whisper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Audio > _MAX_WHISPER_AUDIO_MS omite transcripcion Whisper y envia respuesta igual."""
    from app.services.audio_service import AudioService
    from app.services.pipeline_service import _MAX_WHISPER_AUDIO_MS

    def fake_convert(input_path: Path, output_path: Path) -> None:
        output_path.write_bytes(b"FAKE_WAV_DATA")

    monkeypatch.setattr("app.services.audio_service.convert_ogg_to_wav", fake_convert)
    # Duracion mayor al limite
    monkeypatch.setattr(
        "app.services.audio_service.get_audio_duration_ms",
        lambda wav_path: _MAX_WHISPER_AUDIO_MS + 1,
    )

    hello_ogg = tmp_path / "hello.ogg"
    hello_ogg.write_bytes(b"FAKE_HELLO_OGG")
    monkeypatch.setattr("app.services.audio_service._HELLO_OGG_PATH", hello_ogg)

    whisper_called = False

    def fake_transcribe(_self: object, audio_path: str) -> dict[str, object]:
        nonlocal whisper_called
        whisper_called = True
        return {"text": "nunca deberia llamarse", "language": "es", "segments": [], "duration_ms": 0}

    monkeypatch.setattr("app.services.pipeline_service.WhisperService.transcribe", fake_transcribe)

    send_audio_called = False

    async def fake_send_audio(
        _self: object, target: str, audio_path: str, caption: str | None = None
    ) -> dict[str, object]:
        nonlocal send_audio_called
        send_audio_called = True
        return {}

    monkeypatch.setattr("app.services.openwa_service.OpenWAService.send_audio", fake_send_audio)

    service = AudioService(audio_temp_dir=tmp_path)
    await service.process_audio(
        audio_bytes=b"FAKE_OGG_DATA",
        chat_id="248069442560050@lid",
        request_id="req-audio-largo",
    )

    # Whisper NO se llama para audios muy largos
    assert not whisper_called
    # Pipeline continua: send_audio se llama igual
    assert send_audio_called


@pytest.mark.asyncio
async def test_audio_service_process_audio_whisper_runtime_error_continua(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Si Whisper lanza RuntimeError, process_audio loguea pero continua enviando respuesta."""
    from app.services.audio_service import AudioService

    def fake_convert(input_path: Path, output_path: Path) -> None:
        output_path.write_bytes(b"FAKE_WAV_DATA")

    monkeypatch.setattr("app.services.audio_service.convert_ogg_to_wav", fake_convert)
    monkeypatch.setattr("app.services.audio_service.get_audio_duration_ms", lambda wav_path: 3000)

    hello_ogg = tmp_path / "hello.ogg"
    hello_ogg.write_bytes(b"FAKE_HELLO_OGG")
    monkeypatch.setattr("app.services.audio_service._HELLO_OGG_PATH", hello_ogg)

    def fake_transcribe_runtime_error(_self: object, audio_path: str) -> dict[str, object]:
        raise RuntimeError("Error de transcripcion Whisper: OOM")

    monkeypatch.setattr(
        "app.services.pipeline_service.WhisperService.transcribe",
        fake_transcribe_runtime_error,
    )

    send_audio_called = False

    async def fake_send_audio(
        _self: object, target: str, audio_path: str, caption: str | None = None
    ) -> dict[str, object]:
        nonlocal send_audio_called
        send_audio_called = True
        return {}

    monkeypatch.setattr("app.services.openwa_service.OpenWAService.send_audio", fake_send_audio)

    service = AudioService(audio_temp_dir=tmp_path)
    await service.process_audio(
        audio_bytes=b"FAKE_OGG_DATA",
        chat_id="248069442560050@lid",
        request_id="req-error-runtime",
    )

    # Pipeline continua: send_audio se llama aunque Whisper fallo
    assert send_audio_called


@pytest.mark.asyncio
async def test_audio_service_process_audio_whisper_timeout_continua(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Si Whisper excede el timeout, process_audio loguea pero continua enviando respuesta."""
    from app.services.audio_service import AudioService

    def fake_convert(input_path: Path, output_path: Path) -> None:
        output_path.write_bytes(b"FAKE_WAV_DATA")

    monkeypatch.setattr("app.services.audio_service.convert_ogg_to_wav", fake_convert)
    monkeypatch.setattr("app.services.audio_service.get_audio_duration_ms", lambda wav_path: 3000)

    hello_ogg = tmp_path / "hello.ogg"
    hello_ogg.write_bytes(b"FAKE_HELLO_OGG")
    monkeypatch.setattr("app.services.audio_service._HELLO_OGG_PATH", hello_ogg)

    def fake_transcribe_timeout(_self: object, audio_path: str) -> dict[str, object]:
        raise TimeoutError("transcripcion excedio timeout de 30s")

    monkeypatch.setattr(
        "app.services.pipeline_service.WhisperService.transcribe",
        fake_transcribe_timeout,
    )

    send_audio_called = False

    async def fake_send_audio(
        _self: object, target: str, audio_path: str, caption: str | None = None
    ) -> dict[str, object]:
        nonlocal send_audio_called
        send_audio_called = True
        return {}

    monkeypatch.setattr("app.services.openwa_service.OpenWAService.send_audio", fake_send_audio)

    service = AudioService(audio_temp_dir=tmp_path)
    await service.process_audio(
        audio_bytes=b"FAKE_OGG_DATA",
        chat_id="248069442560050@lid",
        request_id="req-error-timeout",
    )

    # Pipeline continua: send_audio se llama aunque Whisper timeout
    assert send_audio_called


def test_compute_hmac_consistente() -> None:
    """El HMAC debe ser determinista para el mismo body y secret."""
    body = b'{"test": true}'
    sig1 = _compute_hmac(body, "secret")
    sig2 = _compute_hmac(body, "secret")
    assert sig1 == sig2
    assert sig1.startswith("sha256=")
    hex_part = sig1[len("sha256="):]
    assert len(hex_part) == 64  # SHA-256 hex digest


def test_compute_hmac_diferente_secret_diferente_firma() -> None:
    """Distinto secret produce distinta firma para el mismo body."""
    body = b'{"test": true}'
    sig1 = _compute_hmac(body, "secret-a")
    sig2 = _compute_hmac(body, "secret-b")
    assert sig1 != sig2
