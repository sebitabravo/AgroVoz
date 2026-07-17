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
from app.core.phone_hash import hash_phone, normalizar_e164

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

    La normalizacion E.164 se delega en normalizar_e164() que:
    - Limpia SOLO separadores conocidos (espacios, guiones, parentesis, puntos, barras)
    - Rechaza caracteres no permitidos (letras, dígitos unicode)
    - Prefija SIEMPRE '+' en el retorno
    - Valida que el largo este entre 10 y 15 digitos ASCII
    - Rechaza números que empiezan con 0 (E.164 prohibe country code 0)

    El removeprefix("+") es seguro: normalizar_e164 garantiza retorno con "+".

    Para @lid que requieren resolucion de numero real, usar
    resolve_contact_phone() ANTES de llamar a esta funcion.

    Raises:
        ValueError: Si el numero contiene caracteres no permitidos, tiene
                    longitud invalida, o empieza con 0.
    """
    stripped = target.strip()
    if stripped.endswith("@c.us") or stripped.endswith("@lid"):
        return stripped

    e164 = normalizar_e164(stripped)
    digits = e164.removeprefix("+")
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

    La sesion de Open-WA (UUID) se cachea a nivel de clase (_cached_session_id)
    para evitar un HTTP GET redundante en cada mensaje. El cache persiste
    mientras el proceso esta vivo (tipicamente toda la vida del contenedor).
    Si Open-WA se reinicia, habria que reiniciar el backend, pero en MVP
    esto es aceptable.
    """

    _cached_session_id: str | None = None

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
        con status='ready'. Cachea el resultado en _cached_session_id
        (a nivel de clase) para no repetir la consulta HTTP en cada mensaje.

        El cache persiste mientras el proceso esta vivo, lo que evita
        un HTTP GET redundante (~50-100ms) en cada mensaje de voz.

        Returns:
            ID de sesion (UUID) para usar en URLs de la API.

        Raises:
            RuntimeError: Si no hay sesiones listas/activas.
            httpx.HTTPError: Si la API de Open-WA no responde.
        """
        if OpenWAService._cached_session_id is not None:
            return OpenWAService._cached_session_id

        url = f"{self._base_url}/api/sessions"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(url, headers=self._headers())
            response.raise_for_status()
            sessions: list[dict[str, object]] = response.json()

        for session in sessions:
            if session.get("status") in ("ready", "active"):
                session_id = str(session["id"])
                OpenWAService._cached_session_id = session_id
                logger.info("Sesion Open-WA resuelta — id=%s", session_id)
                return session_id

        raise RuntimeError("No hay sesiones listas en Open-WA. Escanee el QR para iniciar sesion.")

    async def resolve_contact_phone(self, contact_id: str) -> str | None:
        """Resuelve un identificador de contacto (@lid) a su numero de telefono real.

        Open-WA expone un endpoint REST que mapea LID (identificador de privacidad
        de WhatsApp) a numero MSISDN real llamando a los servidores de WhatsApp.
        Este metodo es best-effort: puede retornar None si el engine no puede
        resolver el LID (contacto no validado aun, sesion incompleta, etc).

        No lanza excepcion: los errores de red o API se loguean como warning y
        se retorna None. El caller debe manejar el fallback (ej: intentar enviar
        al @lid original, o log de error).

        Args:
            contact_id: Identificador del contacto, tipicamente en formato @lid
                       (ej: "248069442560050@lid"). El @lid se preserva en la URL.

        Returns:
            String con los digitos del numero telefonico (ej: "56912345678"),
            o None si no se pudo resolver. Nunca retorna el numero con sufijo.
        """
        try:
            session_id = await self._resolve_session_id()
            safe_id = quote(contact_id, safe="")
            url = f"{self._base_url}/api/sessions/{session_id}/contacts/{safe_id}/phone"

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, headers=self._headers())
                response.raise_for_status()
                data: dict[str, object] = response.json()

            phone = data.get("phone")
            if phone:
                phone_str = str(phone)
                logger.info(
                    "LID resuelto a telefono — contact_id_hash=%s phone_hash=%s",
                    _hash_phone_for_log(contact_id),
                    _hash_phone_for_log(phone_str),
                )
                return phone_str
            else:
                logger.warning(
                    "LID no pudo resolverse (retorno null) — contact_id_hash=%s",
                    _hash_phone_for_log(contact_id),
                )
                return None

        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            logger.warning(
                "Error resolviendo LID (fallback al @lid original) — contact_id_hash=%s error=%s",
                _hash_phone_for_log(contact_id),
                exc,
            )
            return None

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

    async def send_typing_indicator(self, target: str, state: str = "recording") -> None:
        """Muestra o limpia el indicador de escritura/grabando en WhatsApp.

        Mientras el bot procesa el audio, muestra el indicador "grabando..."
        para que el agricultor sepa que está funcionando. Se limpia con
        state="paused" al terminar.

        Args:
            target: Numero E.164 o chatId.
            state: "typing", "recording" (muestra indicador) o "paused" (lo limpia).

        No lanza excepciones: cualquier fallo (HTTP, red, numero invalido) se
        loggea como warning porque el indicador no es critico para el flujo.
        """
        try:
            session_id = await self._resolve_session_id()
            url = f"{self._base_url}/api/sessions/{session_id}/chats/typing"
            payload = {"chatId": phone_to_chat_id(target), "state": state}

            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(url, headers=self._headers(), json=payload)
                response.raise_for_status()
                logger.debug(
                    "Typing indicator %s — target_hash=%s",
                    state,
                    _hash_phone_for_log(target),
                )
        except (httpx.HTTPError, OSError, RuntimeError, ValueError):
            logger.warning(
                "Typing indicator fallo (no critico) — target_hash=%s state=%s",
                _hash_phone_for_log(target),
                state,
            )

    async def send_text(self, target: str, message: str) -> dict[str, object]:
        """Envia un mensaje de texto a un numero de WhatsApp via Open-WA.

        Si target es @lid (Linked ID), intenta resolver el numero real ANTES de enviar.
        Si la resolucion falla, intenta enviar al @lid original como fallback.

        Args:
            target: Numero E.164 ("+56912345678") o chatId ("569@c.us", "248069442560050@lid").
            message: Texto del mensaje a enviar.

        Returns:
            Respuesta JSON de la API de Open-WA.

        Raises:
            httpx.HTTPError: Si la API de Open-WA falla.
            ValueError: Si el numero tiene formato E.164 invalido (propagado de normalizar_e164).
        """
        try:
            # Resolver LID a telefono real si es necesario (best-effort)
            final_target = target
            if target.endswith("@lid"):
                resolved_phone = await self.resolve_contact_phone(target)
                if resolved_phone:
                    final_target = f"{resolved_phone}@c.us"
                else:
                    logger.warning(
                        "No se pudo resolver LID; intentando con @lid original — target_hash=%s",
                        _hash_phone_for_log(target),
                    )

            session_id = await self._resolve_session_id()
            url = f"{self._base_url}/api/sessions/{session_id}/messages/send-text"
            payload = {"chatId": phone_to_chat_id(final_target), "text": message}

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, headers=self._headers(), json=payload)
                response.raise_for_status()
                logger.info(
                    "Texto enviado — phone_hash=%s size_chars=%d",
                    _hash_phone_for_log(target),
                    len(message),
                )
                return dict(response.json())
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
            logger.error(
                "Error enviando texto — target_hash=%s error=%s",
                _hash_phone_for_log(target),
                exc,
            )
            raise

    async def send_audio(self, target: str, audio_path: str, caption: str | None = None) -> dict[str, object]:
        """Envia un mensaje de audio a un numero de WhatsApp via Open-WA.

        Si target es @lid (Linked ID), intenta resolver el numero real ANTES de enviar.
        Si la resolucion falla, intenta enviar al @lid original como fallback.

        Args:
            target: Numero E.164 ("+56912345678") o chatId ("569@c.us", "248069442560050@lid").
            audio_path: Ruta local al archivo .ogg a enviar.
            caption: Texto opcional que acompaña al audio.

        Returns:
            Respuesta JSON de la API de Open-WA.

        Raises:
            httpx.HTTPError: Si la API de Open-WA falla.
            ValueError: Si el numero tiene formato E.164 invalido (propagado de normalizar_e164).
        """
        try:
            # Resolver LID a telefono real si es necesario (best-effort)
            final_target = target
            if target.endswith("@lid"):
                resolved_phone = await self.resolve_contact_phone(target)
                if resolved_phone:
                    final_target = f"{resolved_phone}@c.us"
                else:
                    logger.warning(
                        "No se pudo resolver LID; intentando con @lid original — target_hash=%s",
                        _hash_phone_for_log(target),
                    )
                    # Fallback: intentar con el @lid original, aunque es probable que falle

            session_id = await self._resolve_session_id()
            url = f"{self._base_url}/api/sessions/{session_id}/messages/send-audio"
            # Open-WA espera los campos base64 + mimetype al TOP LEVEL, no anidados.
            # No soporta el campo `ptt` para notas de voz con este endpoint.
            audio_fields = _audio_file_to_base64_payload(audio_path)
            payload: dict[str, object] = {
                "chatId": phone_to_chat_id(final_target),
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
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
            logger.error(
                "Error enviando audio — target_hash=%s error=%s",
                _hash_phone_for_log(target),
                exc,
            )
            raise


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
