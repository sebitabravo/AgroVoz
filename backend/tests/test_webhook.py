"""Tests del endpoint webhook de WhatsApp (Open-WA).

Cubre: validación HMAC, detección de tipo de mensaje,
procesamiento de audio en background, y manejo de errores.
"""

import hashlib
import hmac as hmac_mod
import json
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings


def _load_fixture(name: str) -> dict[str, object]:
    """Carga un payload de prueba desde fixtures/openwa_webhook_payload.json."""
    fixture_path = Path(__file__).parent / "fixtures" / "openwa_webhook_payload.json"
    with open(fixture_path) as f:
        data: dict[str, object] = json.load(f)
    return dict(data[name])  # type: ignore[arg-type]


def _compute_hmac(body: bytes, secret: str) -> str:
    """Calcula HMAC-SHA256 igual que Open-WA."""
    return hmac_mod.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


# ── Tests de validación HMAC ──────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_sin_firma_retorna_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhook sin header X-OpenWA-Signature debe ser rechazado con 401."""
    from app.main import app

    monkeypatch.setattr(settings, "openwa_webhook_secret", "test-secret")

    payload = _load_fixture("audio_message")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
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

    payload = _load_fixture("audio_message")
    body = json.dumps(payload).encode("utf-8")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
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
async def test_webhook_firma_valida_retorna_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhook con firma HMAC correcta debe ser aceptado.

    No mockea el procesamiento de audio porque BackgroundTasks
    no se ejecuta sincrónicamente en tests con ASGITransport.
    """
    from app.main import app

    monkeypatch.setattr(settings, "openwa_webhook_secret", "test-secret")
    monkeypatch.setattr(settings, "openwa_api_key", "test-api-key")

    payload = _load_fixture("audio_message")
    body = json.dumps(payload).encode("utf-8")
    signature = _compute_hmac(body, "test-secret")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
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
    assert data["message_id"] == "msg_test_audio_001"


# ── Tests de tipo de mensaje ──────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_mensaje_texto_retorna_200_ignorado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mensaje de texto sin audio debe ser ignorado (200 pero no procesa)."""
    from app.main import app

    monkeypatch.setattr(settings, "openwa_webhook_secret", "test-secret")

    payload = _load_fixture("text_message")
    body = json.dumps(payload).encode("utf-8")
    signature = _compute_hmac(body, "test-secret")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
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
    assert data["reason"] == "mensaje_no_audio"


@pytest.mark.asyncio
async def test_webhook_mensaje_con_media_no_audio_ignorado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mensaje con media que no es audio (ej: imagen) debe ser ignorado."""
    from app.main import app

    monkeypatch.setattr(settings, "openwa_webhook_secret", "test-secret")

    payload = _load_fixture("message_media_no_audio")
    body = json.dumps(payload).encode("utf-8")
    signature = _compute_hmac(body, "test-secret")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
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
async def test_webhook_mensaje_sin_media_ignorado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mensaje con hasMedia=true pero media vacío debe ser ignorado."""
    from app.main import app

    monkeypatch.setattr(settings, "openwa_webhook_secret", "test-secret")

    payload = _load_fixture("message_sin_media")
    body = json.dumps(payload).encode("utf-8")
    signature = _compute_hmac(body, "test-secret")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
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


# ── Tests de payload inválido ─────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_payload_invalido_retorna_200_ignorado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Payload que no cumple el esquema debe retornar 200 con status=ignored.

    El endpoint nunca retorna 4xx por payload mal formado porque
    Open-WA no reenvía webhooks. Preferimos loggear y seguir.
    """
    from app.main import app

    monkeypatch.setattr(settings, "openwa_webhook_secret", "test-secret")

    payload = {"sessionId": "default", "message": "esto_no_es_un_objeto"}  # message mal tipado → ValidationError
    body = json.dumps(payload).encode("utf-8")
    signature = _compute_hmac(body, "test-secret")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
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
        transport=ASGITransport(app=app), base_url="http://test"
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
    assert "JSON válido" in response.json()["detail"]


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
        transport=ASGITransport(app=app), base_url="http://test"
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
async def test_openwa_download_media_exitoso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """download_media debe retornar los bytes del audio cuando Open-WA responde 200."""
    from app.services.openwa_service import OpenWAService

    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:8000")
    monkeypatch.setattr(settings, "openwa_api_key", "test-api-key")

    fake_audio = b"FAKE_OGG_AUDIO_DATA"

    # Mock httpx.AsyncClient.get para que retorne 200 con fake_audio

    class FakeResponse:
        content = fake_audio
        status_code = 200

        def raise_for_status(self) -> None:
            pass

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def get(self, url: str, headers: dict[str, str]) -> "FakeResponse":
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    service = OpenWAService()
    result = await service.download_media("msg_test_001")

    assert result == fake_audio


@pytest.mark.asyncio
async def test_openwa_download_media_error_httpx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """download_media debe propagar httpx.HTTPError cuando Open-WA falla."""
    from app.services.openwa_service import OpenWAService

    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:8000")
    monkeypatch.setattr(settings, "openwa_api_key", "test-api-key")

    class FakeResponse:
        status_code = 500

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError(
                "Server error",
                request=httpx.Request("GET", "http://openwa:8000"),
                response=httpx.Response(500),
            )

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def get(self, url: str, headers: dict[str, str]) -> "FakeResponse":
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    service = OpenWAService()
    with pytest.raises(httpx.HTTPStatusError):
        await service.download_media("msg_test_err")


# ── Tests de funciones auxiliares ─────────────────────────────


def test_is_audio_message_detecta_audio() -> None:
    """_is_audio_message retorna True para mensajes con media audio/ogg."""
    from app.api.webhooks import _is_audio_message
    from app.schemas.webhook import WebhookPayload

    raw = _load_fixture("audio_message")
    payload = WebhookPayload.model_validate(raw)
    assert _is_audio_message(payload) is True


def test_is_audio_message_rechaza_imagen() -> None:
    """_is_audio_message retorna False para mensajes con media image/jpeg."""
    from app.api.webhooks import _is_audio_message
    from app.schemas.webhook import WebhookPayload

    raw = _load_fixture("message_media_no_audio")
    payload = WebhookPayload.model_validate(raw)
    assert _is_audio_message(payload) is False


def test_is_audio_message_rechaza_sin_media() -> None:
    """_is_audio_message retorna False para mensajes sin media."""
    from app.api.webhooks import _is_audio_message
    from app.schemas.webhook import WebhookPayload

    raw = _load_fixture("text_message")
    payload = WebhookPayload.model_validate(raw)
    assert _is_audio_message(payload) is False


def test_compute_hmac_consistente() -> None:
    """El HMAC debe ser determinista para el mismo body y secret."""
    body = b'{"test": true}'
    sig1 = _compute_hmac(body, "secret")
    sig2 = _compute_hmac(body, "secret")
    assert sig1 == sig2
    assert len(sig1) == 64  # SHA-256 hex digest


def test_compute_hmac_diferente_secret_diferente_firma() -> None:
    """Distinto secret produce distinta firma para el mismo body."""
    body = b'{"test": true}'
    sig1 = _compute_hmac(body, "secret-a")
    sig2 = _compute_hmac(body, "secret-b")
    assert sig1 != sig2
