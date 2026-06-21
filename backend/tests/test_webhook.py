"""Tests del endpoint webhook de WhatsApp (Open-WA).

Cubre: validación HMAC, detección de tipo de mensaje,
procesamiento de audio en background, y manejo de errores.
"""

import hashlib
import hmac as hmac_mod
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

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

    payload = _load_fixture("audio_message")
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
    """Webhook con firma HMAC en mayúsculas debe ser aceptado (case-insensitive)."""
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
async def test_openwa_download_media_exitoso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """download_media debe retornar los bytes del audio cuando Open-WA responde 200.

    Mockea httpx.AsyncClient con __aenter__/__aexit__ para el patrón
    async with httpx.AsyncClient(...) as client: que usa el servicio."""
    from app.services.openwa_service import OpenWAService
    import app.services.openwa_service as svc

    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:8000")
    monkeypatch.setattr(settings, "openwa_api_key", "test-api-key")

    fake_audio = b"FAKE_OGG_AUDIO_DATA"

    mock_client = AsyncMock()
    mock_response = AsyncMock()
    mock_response.raise_for_status = Mock()
    mock_response.content = fake_audio
    mock_client.get.return_value = mock_response

    # Mock httpx.AsyncClient: el servicio usa async with httpx.AsyncClient(...) as client:
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_client
    mock_ctx.__aexit__.return_value = None
    monkeypatch.setattr(svc.httpx, "AsyncClient", lambda *a, **kw: mock_ctx)

    service = OpenWAService()
    result = await service.download_media("msg_test_001")

    assert result == fake_audio
    mock_client.get.assert_called_once()


@pytest.mark.asyncio
async def test_openwa_download_media_error_httpx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """download_media debe propagar httpx.HTTPError cuando Open-WA falla."""
    from app.services.openwa_service import OpenWAService
    import app.services.openwa_service as svc

    monkeypatch.setattr(settings, "openwa_api_url", "http://openwa:8000")
    monkeypatch.setattr(settings, "openwa_api_key", "test-api-key")

    mock_client = AsyncMock()
    mock_response = AsyncMock()
    mock_response.raise_for_status = Mock(
        side_effect=httpx.HTTPStatusError(
            "Server error",
            request=httpx.Request("GET", "http://openwa:8000"),
            response=httpx.Response(500),
        )
    )
    mock_client.get.return_value = mock_response

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_client
    mock_ctx.__aexit__.return_value = None
    monkeypatch.setattr(svc.httpx, "AsyncClient", lambda *a, **kw: mock_ctx)

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


def test_get_audio_duration_ms_overflow_no_crash() -> None:
    """get_audio_duration_ms no debe explotar con OverflowError."""
    from pathlib import Path

    from app.services.audio_service import get_audio_duration_ms

    # Path inexistente → ffprobe falla → CalledProcessError → 0
    result = get_audio_duration_ms(Path("/tmp/no-existe-xyz.wav"))
    assert result == 0


def test_max_audio_size_constant() -> None:
    """_MAX_AUDIO_SIZE_BYTES debe ser > 0 y representar 25 MB."""
    from app.services.audio_service import _MAX_AUDIO_SIZE_BYTES

    assert _MAX_AUDIO_SIZE_BYTES == 25 * 1024 * 1024
    assert _MAX_AUDIO_SIZE_BYTES > 0


def test_sanitize_message_id_preserva_alfanumerico() -> None:
    """sanitize_message_id preserva caracteres seguros (alfanuméricos, guiones, underscores)."""
    from app.services.audio_service import sanitize_message_id

    assert sanitize_message_id("msg_abc-123_test") == "msg_abc-123_test"


def test_sanitize_message_id_reemplaza_arroba() -> None:
    """sanitize_message_id hashea el teléfono y preserva @c.us (dominio, no PII)."""
    from app.services.audio_service import sanitize_message_id

    result = sanitize_message_id("true_56912345678@c.us")
    # El teléfono 56912345678 debe estar hasheado (no aparece en claro)
    assert "56912345678" not in result
    # @c.us es el dominio de WhatsApp, no es PII — se preserva
    assert "@c.us" in result
    # Estructura: true_<hash_12_chars>@c.us
    assert result.startswith("true_")
    assert result.endswith("@c.us")


def test_sanitize_message_id_whatsapp_id_real() -> None:
    """sanitize_message_id hashea TODOS los teléfonos en IDs con múltiples ocurrencias."""
    from app.services.audio_service import sanitize_message_id

    result = sanitize_message_id("true_56912345678@c.us_3EB0A5F6C8D9_56912345678@c.us")
    # Ningún teléfono aparece en claro
    assert "56912345678" not in result
    # @c.us se preserva en ambas ocurrencias
    assert result.count("@c.us") == 2
    # Estructura general: true_<hash>@c.us_<random>_<hash>@c.us
    assert result.startswith("true_")
    assert result.endswith("@c.us")
    # Ambas ocurrencias del mismo teléfono producen el mismo hash
    import hashlib
    expected_hash = hashlib.sha256(b"56912345678").hexdigest()[:12]
    assert result == f"true_{expected_hash}@c.us_3EB0A5F6C8D9_{expected_hash}@c.us"


def test_sanitize_message_id_vacio_retorna_unknown() -> None:
    """sanitize_message_id retorna 'unknown' si el string está vacío."""
    from app.services.audio_service import sanitize_message_id

    assert sanitize_message_id("") == "unknown"


def test_sanitize_message_id_solo_especiales_retorna_underscores() -> None:
    """sanitize_message_id reemplaza caracteres especiales por '_' (no retorna 'unknown' si hay caracteres)."""
    from app.services.audio_service import sanitize_message_id

    result = sanitize_message_id("@@@!!!")
    # '@' → '_', '!' → '_', 6 caracteres especiales → 6 underscores
    assert result == "______"
    assert result != "unknown"  # No vacío — el regex reemplaza, no elimina


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
    """validate_path_in_audio_dir lanza ValueError si el path está fuera del directorio base."""
    import tempfile
    from pathlib import Path

    import pytest

    from app.services.audio_service import validate_path_in_audio_dir

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_dir = Path(tmpdir).resolve()
        # Construir path que después de resolve() apunte fuera
        traversal = audio_dir / ".." / "etc" / "passwd"

        with pytest.raises(ValueError, match="Path fuera del directorio de audio"):
            validate_path_in_audio_dir(traversal, audio_dir)


# ── Tests de AudioService.process_audio ────────────────────


@pytest.mark.asyncio
async def test_audio_service_process_audio_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AudioService.process_audio descarga, convierte y limpia sin errores.

    Usa inyección de audio_temp_dir en vez de monkeypatch sobre
    _get_audio_temp_dir (P1-5 DI)."""
    from app.schemas.webhook import WebhookPayload
    from app.services.audio_service import AudioService

    fake_ogg = b"FAKE_OGG_DATA"

    # Mock download_media para no llamar a Open-WA
    async def fake_download(_self: object, _msg_id: str) -> bytes:
        return fake_ogg

    monkeypatch.setattr(
        "app.services.openwa_service.OpenWAService.download_media",
        fake_download,
    )

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

    raw = _load_fixture("audio_message")
    payload = WebhookPayload.model_validate(raw)

    # P1-5: Inyectar audio_temp_dir en vez de monkeypatch sobre _get_audio_temp_dir
    service = AudioService(audio_temp_dir=tmp_path)
    await service.process_audio(payload, "+56912345678", "test-request-id")

    # Verificar que se creó .wav y NO quedó .ogg (se limpia después de convertir)
    wav_files = list(tmp_path.glob("*.wav"))
    ogg_files = list(tmp_path.glob("*.ogg"))
    assert len(wav_files) == 1
    assert len(ogg_files) == 0
    assert wav_files[0].read_bytes() == b"FAKE_WAV_DATA"


@pytest.mark.asyncio
async def test_audio_service_process_audio_error_descarga_limpia_archivos(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Si download_media falla, el finally limpia archivos temporales (P2-5)."""
    from app.schemas.webhook import WebhookPayload
    from app.services.audio_service import AudioService

    # Mock download_media que falla
    async def fake_download_error(_self: object, _msg_id: str) -> bytes:
        raise httpx.ConnectError("No se pudo conectar a Open-WA")

    monkeypatch.setattr(
        "app.services.openwa_service.OpenWAService.download_media",
        fake_download_error,
    )

    raw = _load_fixture("audio_message")
    payload = WebhookPayload.model_validate(raw)

    service = AudioService(audio_temp_dir=tmp_path)
    # No debe lanzar excepción — el except captura y loguea
    await service.process_audio(payload, "+56912345678", "test-request-id")

    # No deben quedar archivos huérfanos después del error
    ogg_files = list(tmp_path.glob("*.ogg"))
    wav_files = list(tmp_path.glob("*.wav"))
    assert len(ogg_files) == 0
    assert len(wav_files) == 0


@pytest.mark.asyncio
async def test_audio_service_process_audio_audio_excede_tamano(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Audio que excede _MAX_AUDIO_SIZE_BYTES es rechazado sin crear archivos (P2-5)."""
    from app.schemas.webhook import WebhookPayload
    from app.services.audio_service import AudioService, _MAX_AUDIO_SIZE_BYTES

    # Audio que excede el límite
    fake_ogg_large = b"X" * (_MAX_AUDIO_SIZE_BYTES + 1)

    async def fake_download_large(_self: object, _msg_id: str) -> bytes:
        return fake_ogg_large

    monkeypatch.setattr(
        "app.services.openwa_service.OpenWAService.download_media",
        fake_download_large,
    )

    raw = _load_fixture("audio_message")
    payload = WebhookPayload.model_validate(raw)

    service = AudioService(audio_temp_dir=tmp_path)
    await service.process_audio(payload, "+56912345678", "test-request-id")

    # No deben crearse archivos porque el audio fue rechazado por tamaño
    ogg_files = list(tmp_path.glob("*.ogg"))
    wav_files = list(tmp_path.glob("*.wav"))
    assert len(ogg_files) == 0
    assert len(wav_files) == 0


def test_hash_phone_for_log_consistencia() -> None:
    """_hash_phone_for_log produce el mismo prefijo que hash_phone completo."""
    from app.core.config import settings
    from app.core.phone_hash import hash_phone
    from app.services.openwa_service import _hash_phone_for_log

    phone = "+56912345678"
    full = hash_phone(phone, settings.phone_hash_pepper)
    truncated = _hash_phone_for_log(phone)

    assert len(truncated) == 8
    assert full.startswith(truncated)


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
