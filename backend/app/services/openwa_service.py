"""Cliente HTTP para la API de Open-WA (gateway WhatsApp).

Provee métodos asíncronos para descargar media y enviar mensajes
a través del gateway WhatsApp self-hosted.
"""

import logging
import re

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class OpenWAService:
    """Cliente HTTP asíncrono para la API REST de Open-WA.

    Encapsula las llamadas a la API de Open-WA: descargar media,
    enviar texto y enviar audio. Usa httpx con timeout explícito
    y logging estructurado.
    """

    def __init__(self) -> None:
        """Inicializa el cliente con la URL base y API key desde settings."""
        self._base_url: str = settings.openwa_api_url.rstrip("/")
        self._api_key: str = settings.openwa_api_key
        self._timeout: float = 30.0

    def _headers(self) -> dict[str, str]:
        """Headers HTTP para requests a la API de Open-WA."""
        headers: dict[str, str] = {}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        return headers

    async def download_media(self, message_id: str) -> bytes:
        """Descarga el archivo de audio de un mensaje vía la API de Open-WA.

        Args:
            message_id: ID del mensaje en Open-WA (ej: "msg_abc123").
                       Se sanitiza para prevenir path traversal.

        Returns:
            Contenido binario del archivo de audio (.ogg).

        Raises:
            httpx.HTTPError: Si la API de Open-WA no responde o retorna error.
        """
        safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", message_id) or "unknown"
        url = f"{self._base_url}/api/sessions/default/messages/{safe_id}/media"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(url, headers=self._headers())
            response.raise_for_status()
            logger.info(
                "Audio descargado — message_id=%s size_bytes=%d",
                safe_id,
                len(response.content),
            )
            return response.content

    async def send_text(self, phone: str, message: str) -> dict[str, object]:
        """Envía un mensaje de texto a un número de WhatsApp vía Open-WA.

        Args:
            phone: Número en formato E.164 (ej: "+56912345678").
            message: Texto del mensaje a enviar.

        Returns:
            Respuesta JSON de la API de Open-WA.

        Raises:
            httpx.HTTPError: Si la API de Open-WA falla.
        """
        url = f"{self._base_url}/api/sessions/default/messages/send-text"
        payload = {"phone": phone, "text": message}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, headers=self._headers(), json=payload)
            response.raise_for_status()
            logger.info(
                "Texto enviado — phone_hash=%s size_chars=%d",
                _hash_phone_for_log(phone),
                len(message),
            )
            return dict(response.json())

    async def send_audio(
        self, phone: str, audio_path: str, caption: str | None = None
    ) -> dict[str, object]:
        """Envía un mensaje de audio a un número de WhatsApp vía Open-WA.

        Args:
            phone: Número en formato E.164.
            audio_path: Ruta local al archivo .ogg a enviar.
            caption: Texto opcional que acompaña al audio.

        Returns:
            Respuesta JSON de la API de Open-WA.

        Raises:
            httpx.HTTPError: Si la API de Open-WA falla.
        """
        url = f"{self._base_url}/api/sessions/default/messages/send-audio"
        payload: dict[str, object] = {"phone": phone, "audio": audio_path}
        if caption:
            payload["caption"] = caption

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, headers=self._headers(), json=payload)
            response.raise_for_status()
            logger.info(
                "Audio enviado — phone_hash=%s path=%s",
                _hash_phone_for_log(phone),
                audio_path,
            )
            return dict(response.json())


def _hash_phone_for_log(phone: str) -> str:
    """Hash corto del número para logging (sin PII en claro).

    Usa los primeros 8 caracteres del HMAC-SHA256 completo.
    En desarrollo (sin pepper configurado), usa SHA-256 simple.
    """
    import hashlib

    pepper = settings.phone_hash_pepper
    if pepper and pepper != "agrovoz-dev-pepper":
        import hmac

        full = hmac.new(
            pepper.encode("utf-8"), phone.encode("utf-8"), hashlib.sha256
        ).hexdigest()
    else:
        full = hashlib.sha256(phone.encode("utf-8")).hexdigest()
    return full[:8]
