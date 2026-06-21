"""Schemas Pydantic para el webhook de Open-WA.

Define los modelos de datos que Open-WA envía en el POST /api/v1/webhook/whatsapp.
"""

from pydantic import BaseModel, ConfigDict, Field


class WebhookMedia(BaseModel):
    """Archivo multimedia adjunto a un mensaje de WhatsApp."""

    mimetype: str = ""
    url: str = ""

    model_config = ConfigDict(extra="allow")


class WebhookMessage(BaseModel):
    """Mensaje individual dentro del payload del webhook."""

    id: str = ""
    from_: str = Field(
        default="",
        alias="from",
        description="Número de teléfono del remitente en formato E.164",
    )
    body: str = ""
    has_media: bool = Field(default=False, alias="hasMedia")
    media: list[WebhookMedia] = Field(default_factory=list)
    timestamp: int = 0

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class WebhookPayload(BaseModel):
    """Payload completo del webhook de Open-WA.

    Open-WA envía este JSON en el body del POST al webhook configurado.
    """

    session_id: str = Field(default="default", alias="sessionId")
    message: WebhookMessage = Field(default_factory=WebhookMessage)

    model_config = ConfigDict(extra="allow", populate_by_name=True)
