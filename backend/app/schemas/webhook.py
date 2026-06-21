"""Schemas Pydantic para el webhook de Open-WA.

Define los modelos de datos que Open-WA envia en el POST /api/v1/webhook/whatsapp.
Estructura real verificada con trafico en vivo (2026-06-21).
"""

from pydantic import BaseModel, ConfigDict, Field


class WebhookMedia(BaseModel):
    """Archivo multimedia adjunto al mensaje.

    Para mensajes de voz (type=voice), Open-WA incluye el audio como
    base64 inline en `data`, no como URL para descargar.
    """

    mimetype: str = ""
    data: str = ""  # Base64 del audio (inline)

    model_config = ConfigDict(extra="allow")


class WebhookContact(BaseModel):
    """Informacion del contacto remitente."""

    name: str = ""
    push_name: str = Field(default="", alias="pushName")

    model_config = ConfigDict(extra="allow")


class WebhookMessageData(BaseModel):
    """Datos del mensaje dentro del campo `data` del webhook de Open-WA.

    Open-WA envia el mensaje en `data`, no en `message`.
    El tipo de mensaje se determina por `type` ("voice", "text", etc.),
    no por hasMedia. Los audios (type=voice) traen `media` como objeto
    con el base64 inline.
    """

    id: str = ""
    from_: str = Field(
        default="",
        alias="from",
        description="Remitente (puede ser @lid o @c.us, no necesariamente E.164)",
    )
    to: str = ""
    chat_id: str = Field(default="", alias="chatId")
    body: str = ""
    type: str = ""  # "voice", "text", "image", etc.
    timestamp: int = 0
    from_me: bool = Field(default=False, alias="fromMe")
    is_group: bool = Field(default=False, alias="isGroup")
    is_status_broadcast: bool = Field(default=False, alias="isStatusBroadcast")
    is_lid_sender: bool = Field(default=False, alias="isLidSender")
    contact: WebhookContact = Field(default_factory=WebhookContact)
    media: WebhookMedia | None = None

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class WebhookPayload(BaseModel):
    """Payload completo del webhook de Open-WA.

    Open-WA envia este JSON en el body del POST al webhook configurado.
    La estructura real es:
    {
      "event": "message.received",
      "timestamp": "...",
      "sessionId": "...",
      "idempotencyKey": "...",
      "deliveryId": "...",
      "data": { ... }
    }
    """

    event: str = ""
    timestamp: str = ""
    session_id: str = Field(default="default", alias="sessionId")
    idempotency_key: str = Field(default="", alias="idempotencyKey")
    delivery_id: str = Field(default="", alias="deliveryId")
    data: WebhookMessageData = Field(default_factory=WebhookMessageData)

    model_config = ConfigDict(extra="allow", populate_by_name=True)
