"""Router para webhooks de WhatsApp via Open-WA.

Endpoint POST /api/v1/webhook/whatsapp que Open-WA llama cuando
llega un mensaje. Valida firma HMAC, detecta tipo de mensaje,
y delega el procesamiento de audio o imagen al servicio correspondiente.
"""

import base64
import logging
import math

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.config import settings
from app.core.security import verify_openwa_webhook
from app.schemas.webhook import WebhookPayload
from app.services.audio_service import AudioService, sanitize_message_id
from app.services.vision_service import VisionService

router = APIRouter(tags=["webhooks"])
logger = logging.getLogger(__name__)


def get_audio_service() -> AudioService:
    """Factory para inyeccion de dependencias de AudioService.

    Permite que FastAPI inyecte AudioService via Depends,
    facilitando el mocking en tests (P2-2).
    """
    return AudioService()


def get_vision_service() -> VisionService:
    """Factory del servicio de visión para inyección y tests del webhook."""
    return VisionService()


# Dependencias a nivel de modulo para cumplir con B008 (no calls en argument defaults).
_audio_service_dep = Depends(get_audio_service)
_vision_service_dep = Depends(get_vision_service)
_verify_openwa_dep = Depends(verify_openwa_webhook)


def _is_voice_message(payload: WebhookPayload) -> bool:
    """Determina si el mensaje es un audio de voz.

    Open-WA real no usa hasMedia/media[].mimetype — el campo
    `data.type` es "voice" para notas de voz, y el audio viene
    inline como base64 en `data.media.data`.

    Solo verifica el tipo. La extraccion del media (base64) se
    valida despues por separado para dar un log/reason especifico
    cuando el evento es voice pero no trae datos de audio.
    """
    return payload.data.type == "voice"


# Largo maximo de un mensaje escrito. Mismo criterio que el demo web: por
# encima de esto no es una consulta, es un pegado accidental, y el prompt del
# LLM se dispara.
_MAX_TEXTO_CHARS = 500


def _is_text_message(payload: WebhookPayload) -> bool:
    """Determina si el mensaje es texto escrito con contenido util.

    Open-WA marca los mensajes escritos como ``type="chat"``; se aceptan
    tambien "text" y el caso sin tipo pero con body, por tolerancia a cambios
    del gateway. Se descartan los vacios y los desmedidamente largos.
    """
    if payload.data.type not in ("chat", "text", ""):
        return False
    cuerpo = payload.data.body.strip()
    return bool(cuerpo) and len(cuerpo) <= _MAX_TEXTO_CHARS


def _is_location_message(payload: WebhookPayload) -> bool:
    """Determina si Open-WA entregó un pin de ubicación."""
    return payload.data.type == "location"


def _extract_location(payload: WebhookPayload) -> tuple[float, float] | None:
    """Extrae y valida latitud/longitud de las variantes del payload."""
    data = payload.data
    nested = data.location
    lat = data.lat if data.lat is not None else data.latitude
    lng = data.lng if data.lng is not None else data.longitude
    if nested is not None:
        if lat is None:
            lat = nested.lat if nested.lat is not None else nested.latitude
        if lng is None:
            lng = nested.lng if nested.lng is not None else nested.longitude

    if lat is None or lng is None or not (math.isfinite(lat) and math.isfinite(lng)):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None
    return lat, lng


def _is_image_message(payload: WebhookPayload) -> bool:
    """Determina si Open-WA reportó una imagen recibida por WhatsApp."""
    return payload.data.type.casefold() == "image"


def _extract_audio_bytes(payload: WebhookPayload) -> bytes | None:
    """Extrae el audio base64 inline del payload del webhook.

    Open-WA incluye el audio como base64 en data.media.data
    para mensajes type=voice.

    Returns:
        Bytes del audio decodificado, o None si no hay media.
    """
    if payload.data.media is None or not payload.data.media.data:
        return None
    try:
        return base64.b64decode(payload.data.media.data)
    except ValueError:
        logger.warning("Audio base64 invalido en webhook")
        return None


@router.post("/webhook/whatsapp")
async def webhook_whatsapp(
    request: Request,
    background_tasks: BackgroundTasks,
    raw_payload: dict[str, object] = _verify_openwa_dep,
    audio_service: AudioService = _audio_service_dep,
    vision_service: VisionService = _vision_service_dep,
) -> JSONResponse:
    """Endpoint que recibe mensajes de WhatsApp via webhook de Open-WA.

    Flujo:
    1. Validar firma HMAC (hecho por la dependencia verify_openwa_webhook).
    2. Parsear payload -> WebhookPayload.
    3. Detectar tipo de mensaje (voice, text, image u otro).
    4. Si es voice o image: delegar al servicio correspondiente en background.
    5. Si es otro: log + ignorar por ahora.
    6. Retornar 200 rapido (ack a Open-WA).

    Open-WA espera una respuesta rapida (<5s). El procesamiento pesado
    (decodificar base64, ffmpeg) se hace en background tasks para no bloquear.
    """
    request_id: str = getattr(request.state, "request_id", "-")

    try:
        payload = WebhookPayload.model_validate(raw_payload)
    except ValidationError as exc:
        logger.warning(
            "Webhook con payload invalido — request_id=%s error_count=%d",
            request_id,
            len(exc.errors()),
        )
        return JSONResponse(
            status_code=200,
            content={"status": "ignored", "reason": "payload_invalido"},
        )

    chat_id = payload.data.chat_id or payload.data.from_
    raw_message_id = payload.data.id
    message_type = payload.data.type or "unknown"
    message_id_safe = sanitize_message_id(raw_message_id)

    logger.info(
        "Webhook recibido — message_id=%s type=%s request_id=%s",
        message_id_safe,
        message_type,
        request_id,
    )

    # Mensajes escritos: se procesan igual que los de voz, pero sin Whisper ni
    # TTS. El productor no siempre puede mandar audio (lugar ruidoso, reunion,
    # mala senal), asi que el texto es una via de entrada valida.
    if _is_text_message(payload):
        texto = payload.data.body.strip()
        logger.info(
            "Mensaje de texto recibido — message_id=%s chars=%d request_id=%s",
            message_id_safe,
            len(texto),
            request_id,
        )
        background_tasks.add_task(
            audio_service.process_text,
            texto=texto,
            chat_id=chat_id,
            request_id=request_id,
        )
        return JSONResponse(
            status_code=200,
            content={"status": "received", "message_id": message_id_safe},
        )

    if _is_location_message(payload):
        location = _extract_location(payload)
        if location is None:
            logger.info(
                "Ubicación sin coordenadas válidas ignorada — message_id=%s request_id=%s",
                message_id_safe,
                request_id,
            )
            return JSONResponse(
                status_code=200,
                content={"status": "ignored", "reason": "ubicacion_invalida"},
            )

        lat, lng = location
        logger.info(
            "Ubicación recibida — message_id=%s request_id=%s",
            message_id_safe,
            request_id,
        )
        background_tasks.add_task(
            audio_service.process_location,
            lat=lat,
            lng=lng,
            chat_id=chat_id,
            request_id=request_id,
        )
        return JSONResponse(
            status_code=200,
            content={"status": "received", "message_id": message_id_safe},
        )

    if _is_image_message(payload):
        if not settings.vision_enabled:
            logger.info(
                "Visión deshabilitada — message_id=%s request_id=%s",
                message_id_safe,
                request_id,
            )
            return JSONResponse(
                status_code=200,
                content={"status": "ignored", "reason": "vision_deshabilitada"},
            )
        if not raw_message_id:
            logger.warning("Imagen sin message_id — request_id=%s", request_id)
            return JSONResponse(
                status_code=200,
                content={"status": "ignored", "reason": "imagen_sin_id"},
            )

        background_tasks.add_task(
            vision_service.process_whatsapp_image,
            message_id=raw_message_id,
            chat_id=chat_id,
            request_id=request_id,
        )
        return JSONResponse(
            status_code=200,
            content={"status": "received", "message_id": message_id_safe},
        )

    # Ni voz, texto, ubicación ni imagen (sticker, ...): fuera de alcance.
    if not _is_voice_message(payload):
        logger.info(
            "Mensaje no-audio ignorado — message_id=%s type=%s has_body=%s request_id=%s",
            message_id_safe,
            message_type,
            "si" if payload.data.body else "no",
            request_id,
        )
        return JSONResponse(
            status_code=200,
            content={"status": "ignored", "reason": "mensaje_no_audio"},
        )

    # Extraer audio base64 inline
    audio_bytes = _extract_audio_bytes(payload)
    if audio_bytes is None:
        logger.warning(
            "Mensaje voice sin media data — message_id=%s request_id=%s",
            message_id_safe,
            request_id,
        )
        return JSONResponse(
            status_code=200,
            content={"status": "ignored", "reason": "voice_sin_media"},
        )

    logger.info(
        "Audio de voz recibido — message_id=%s size_bytes=%d request_id=%s",
        message_id_safe,
        len(audio_bytes),
        request_id,
    )

    # Delegar procesamiento a AudioService en background.
    # AudioService recibe los bytes ya decodificados y el chatId para responder.
    background_tasks.add_task(
        audio_service.process_audio,
        audio_bytes=audio_bytes,
        chat_id=chat_id,
        request_id=request_id,
    )

    return JSONResponse(
        status_code=200,
        content={"status": "received", "message_id": message_id_safe},
    )
