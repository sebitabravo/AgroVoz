"""Cliente HTTP para la API de Open-WA (gateway WhatsApp).

Provee métodos asíncronos para descargar media y enviar mensajes
a través del gateway WhatsApp self-hosted.
"""

import logging
import warnings
from base64 import b64encode
from pathlib import Path
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.core.phone_hash import hash_phone

logger = logging.getLogger(__name__)

_AUDIO_MIMETYPE_BY_SUFFIX: dict[str, str] = {
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".amr": "audio/amr",
    ".wav": "audio/wav",
}


def phone_to_chat_id(target: str) -> str:
    """Convierte un numero E.164 o chatId al formato chatId de Open-WA.

    Open-WA espera `chatId` con formato `<numero>@c.us` o `<lid>@lid`.
    Si ya viene como chatId valido (@c.us o @lid), lo retorna sin cambios.
    Si es un numero E.164 (ej: +56912345678), agrega el sufijo @c.us.
    """
    stripped = target.strip()
    if stripped.endswith("@c.us") or stripped.endswith("@lid"):
        return stripped

    digits = stripped.removeprefix("+").replace(" ", "")
    return f"{digits}@c.us"


def _audio_file_to_base64_payload(audio_path: str) -> dict[str, str]:
    """Codifica un archivo de audio local como base64 para Open-WA.

    Open-WA espera base64 sin prefijo `data:` — el `atob` nativo de
    JavaScript no soporta data URIs.

    Usamos base64 porque el backend y Open-WA corren en contenedores distintos:
    un `path` local del backend no necesariamente existe dentro de Open-WA.
    """
    path = Path(audio_path)
    mimetype = _AUDIO_MIMETYPE_BY_SUFFIX.get(path.suffix.lower(), "audio/ogg")
    audio_data = b64encode(path.read_bytes()).decode("ascii")
    return {
        "base64": audio_data,
        "mimetype": mimetype,
    }


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

        El session ID se descubre automaticamente consultando la API de
        Open-WA, o se puede preconfigurar via settings.openwa_session_id.
        """
        self._base_url: str = settings.openwa_api_url.rstrip("/")
        self._api_key: str = settings.openwa_api_key
        self._timeout: float = 30.0
        self._session_id: str | None = None  # Se resuelve lazy en _resolve_session_id

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

    async def _resolve_session_id(self) -> str:
        """Descubre el session ID de Open-WA consultando su API.

        Lista las sesiones activas y retorna el ID de la primera
        con status='ready'. Cachea el resultado en self._session_id
        para no repetir la consulta HTTP en cada llamada.

        Returns:
            ID de sesion (UUID) para usar en URLs de la API.

        Raises:
            RuntimeError: Si no hay sesiones listas/activas.
            httpx.HTTPError: Si la API de Open-WA no responde.
        """
        if self._session_id:
            return self._session_id

        url = f"{self._base_url}/api/sessions"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(url, headers=self._headers())
            response.raise_for_status()
            sessions: list[dict[str, object]] = response.json()

        for session in sessions:
            if session.get("status") in ("ready", "active"):
                session_id = str(session["id"])
                self._session_id = session_id
                logger.info("Sesion Open-WA resuelta — id=%s", session_id)
                return session_id

        raise RuntimeError(
            "No hay sesiones listas en Open-WA. "
            "Escanee el QR para iniciar sesion."
        )

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
        session_id = await self._resolve_session_id()
        url = f"{self._base_url}/api/sessions/{session_id}/messages/{safe_id}/media"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(url, headers=self._headers())
            response.raise_for_status()
            logger.info(
                "Audio descargado — message_id=%s size_bytes=%d",
                safe_id,
                len(response.content),
            )
            return response.content

    async def send_text(self, target: str, message: str) -> dict[str, object]:
        """Envia un mensaje de texto a un numero de WhatsApp via Open-WA.

        Args:
            target: Numero E.164 ("+56912345678") o chatId ("569@c.us", "lid@lid").
            message: Texto del mensaje a enviar.

        Returns:
            Respuesta JSON de la API de Open-WA.

        Raises:
            httpx.HTTPError: Si la API de Open-WA falla.
        """
        session_id = await self._resolve_session_id()
        url = f"{self._base_url}/api/sessions/{session_id}/messages/send-text"
        payload = {"chatId": phone_to_chat_id(target), "text": message}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, headers=self._headers(), json=payload)
            response.raise_for_status()
            logger.info(
                "Texto enviado — phone_hash=%s size_chars=%d",
                _hash_phone_for_log(target),
                len(message),
            )
            return dict(response.json())

    async def send_audio(
        self, target: str, audio_path: str, caption: str | None = None
    ) -> dict[str, object]:
        """Envia un mensaje de audio a un numero de WhatsApp via Open-WA.

        Args:
            target: Numero E.164 ("+56912345678") o chatId ("569@c.us", "lid@lid").
            audio_path: Ruta local al archivo .ogg a enviar.
            caption: Texto opcional que acompaña al audio.

        Returns:
            Respuesta JSON de la API de Open-WA.

        Raises:
            httpx.HTTPError: Si la API de Open-WA falla.
        """
        session_id = await self._resolve_session_id()
        url = f"{self._base_url}/api/sessions/{session_id}/messages/send-audio"
        # Open-WA espera los campos base64 + mimetype al TOP LEVEL, no anidados.
        # No soporta el campo `ptt` para notas de voz con este endpoint.
        audio_fields = _audio_file_to_base64_payload(audio_path)
        payload: dict[str, object] = {
            "chatId": phone_to_chat_id(target),
            "base64": audio_fields["base64"],
            "mimetype": audio_fields["mimetype"],
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, headers=self._headers(), json=payload)
            response.raise_for_status()
            # P2-4: Loguear solo el nombre del archivo, no el path completo.
            # El path completo puede leakear la estructura del sistema de archivos.
            audio_filename = Path(audio_path).name
            logger.info(
                "Audio enviado — target_hash=%s file=%s",
                _hash_phone_for_log(target),
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
