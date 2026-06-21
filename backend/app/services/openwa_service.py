"""Cliente HTTP para la API de Open-WA (gateway WhatsApp).

Provee métodos asíncronos para descargar media y enviar mensajes
a través del gateway WhatsApp self-hosted.
"""

import logging
import warnings
from pathlib import Path
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.core.phone_hash import hash_phone

logger = logging.getLogger(__name__)


class OpenWAService:
    """Cliente HTTP asíncrono para la API REST de Open-WA.

    Encapsula las llamadas a la API de Open-WA: descargar media,
    enviar texto y enviar audio. Cada método crea su propio httpx.AsyncClient
    con async with (connection pool se libera al salir del contexto).
    Para MVP con <10 usuarios concurrentes, el overhead de crear un
    connection pool por request es negligible. En producción, mover a un
    cliente compartido manejado por el lifespan de FastAPI.
    """

    def __init__(self) -> None:
        """Inicializa el cliente con la URL base y API key desde settings.

        Valida que la API key esté configurada en desarrollo (solo warning,
        no bloquea el arranque). En producción, main.py bloquea si está vacía
        (lifespan validation).
        """
        self._base_url: str = settings.openwa_api_url.rstrip("/")
        self._api_key: str = settings.openwa_api_key
        self._timeout: float = 30.0

        # P1-3: Advertir si la API key no está configurada en desarrollo.
        # El warning usa stacklevel=2 para apuntar al caller (AudioService),
        # no a este __init__.
        if not self._api_key and settings.app_env == "development":
            warnings.warn(
                "OPENWA_API_KEY no está configurada. "
                "Las llamadas a Open-WA fallarán con 401. "
                "Configúrela en .env o en el entorno de desarrollo.",
                RuntimeWarning,
                stacklevel=2,
            )

    def _headers(self) -> dict[str, str]:
        """Headers HTTP para requests a la API de Open-WA."""
        headers: dict[str, str] = {}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        return headers

    async def download_media(self, message_id: str) -> bytes:
        """Descarga el archivo de audio de un mensaje vía la API de Open-WA.

        Args:
            message_id: ID del mensaje en Open-WA (ej: "true_56912345678@c.us_3EB0...").
                       Se URL-encodea con quote() para preservar caracteres especiales
                       que Open-WA requiere (como '@' en IDs de WhatsApp).

        Returns:
            Contenido binario del archivo de audio (.ogg).

        Raises:
            httpx.HTTPError: Si la API de Open-WA no responde o retorna error.
        """
        safe_id = quote(message_id, safe="")
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
            # P2-4: Loguear solo el nombre del archivo, no el path completo.
            # El path completo puede leakear la estructura del sistema de archivos.
            audio_filename = Path(audio_path).name
            logger.info(
                "Audio enviado — phone_hash=%s file=%s",
                _hash_phone_for_log(phone),
                audio_filename,
            )
            return dict(response.json())


def _hash_phone_for_log(phone: str) -> str:
    """Hash corto del número para logging (sin PII en claro).

    Delega en hash_phone() de app.core.phone_hash para consistencia
    entre módulos (mismo hash en logs del webhook y del servicio).
    Trunca a 8 caracteres para legibilidad.

    Nunca lanza excepción: si el pepper está vacío o hash_phone falla,
    retorna 'unknown' en vez de interrumpir la operación que se está logueando.
    """
    try:
        full = hash_phone(phone, settings.phone_hash_pepper)
        return full[:8]
    except (ValueError, AttributeError):
        return "unknown"
