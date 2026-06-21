"""Router para webhooks de WhatsApp vía Open-WA.

Endpoint POST /api/v1/webhook/whatsapp que Open-WA llama cuando
llega un mensaje. Valida firma HMAC, detecta tipo de mensaje,
y delega el procesamiento de audio al AudioService.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.config import settings
from app.core.phone_hash import hash_phone
from app.core.security import verify_openwa_webhook
from app.schemas.webhook import WebhookPayload
from app.services.audio_service import AudioService

router = APIRouter(tags=["webhooks"])
logger = logging.getLogger(__name__)


def _is_audio_message(payload: WebhookPayload) -> bool:
    """Determina si el mensaje contiene audio.

    Un mensaje se considera de audio si has_media es True y al menos
    un elemento de media tiene mimetype que empieza con 'audio/'.
    """
    if not payload.message.has_media:
        return False
    return any(media.mimetype.startswith("audio/") for media in payload.message.media)


@router.post("/webhook/whatsapp")
async def webhook_whatsapp(
    request: Request,
    background_tasks: BackgroundTasks,
    raw_payload: dict[str, object] = Depends(verify_openwa_webhook),  # noqa: B008
) -> JSONResponse:
    """Endpoint que recibe mensajes de WhatsApp vía webhook de Open-WA.

    Flujo:
    1. Validar firma HMAC (hecho por la dependencia verify_openwa_webhook).
    2. Parsear payload → WebhookPayload.
    3. Detectar tipo de mensaje (audio vs texto vs otro).
    4. Si es audio: delegar procesamiento al AudioService en background.
    5. Si es texto: log + ignorar por ahora.
    6. Retornar 200 rápido (ack a Open-WA).

    Open-WA espera una respuesta rápida (<5s). El procesamiento pesado
    (descarga, ffmpeg) se hace en background tasks para no bloquear.
    """
    request_id: str = getattr(request.state, "request_id", "-")

    try:
        payload = WebhookPayload.model_validate(raw_payload)
    except ValidationError:
        logger.warning("Webhook con payload inválido — request_id=%s", request_id)
        return JSONResponse(
            status_code=200,
            content={"status": "ignored", "reason": "payload_invalido"},
        )

    phone = payload.message.from_
    message_id = payload.message.id

    logger.info(
        "Webhook recibido — message_id=%s phone_hash=%s has_media=%s media_count=%d request_id=%s",
        message_id,
        hash_phone(phone, settings.phone_hash_pepper),
        payload.message.has_media,
        len(payload.message.media),
        request_id,
    )

    # Mensaje de texto: log + ignorar (el pipeline de voz solo procesa audio por ahora)
    if not payload.message.has_media or not _is_audio_message(payload):
        logger.info(
            "Mensaje no-audio ignorado — message_id=%s type=text body_preview=%s request_id=%s",
            message_id,
            payload.message.body[:100] if payload.message.body else "(vacío)",
            request_id,
        )
        return JSONResponse(
            status_code=200,
            content={"status": "ignored", "reason": "mensaje_no_audio"},
        )

    # Mensaje de audio: delegar a AudioService en background
    audio_service = AudioService()
    background_tasks.add_task(audio_service.process_audio, payload, phone, request_id)

    return JSONResponse(
        status_code=200,
        content={"status": "received", "message_id": message_id},
    )
