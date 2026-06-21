"""Servicio de procesamiento de audio para el pipeline de voz.

Convierte audio .ogg de WhatsApp a .wav 16kHz mono usando ffmpeg,
valida paths, y orquesta la descarga + conversión en background.
"""

import asyncio
import logging
import re
import subprocess
import time
import uuid
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.phone_hash import hash_phone
from app.schemas.webhook import WebhookPayload
from app.services.openwa_service import OpenWAService

logger = logging.getLogger(__name__)

# Directorio donde se almacena temporalmente el audio convertido.
# En Docker: /app/data/audio_temp/ (montado como volumen en backend/data/).
# En local: backend/data/audio_temp/.
_AUDIO_TEMP_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "audio_temp"

# Audio de respuesta fijo: se envía mientras el pipeline TTS completo no está implementado.
# Pregrabado con macOS TTS (Eddy es_ES) → libopus@48kHz mono.
_HELLO_OGG_PATH = Path(__file__).resolve().parent.parent / "static" / "hello.ogg"

# Tamaño máximo de archivo de audio (25 MB). WhatsApp limita audios a ~16 MB,
# pero este límite es defensivo contra archivos maliciosos o corruptos.
_MAX_AUDIO_SIZE_BYTES = 25 * 1024 * 1024


def sanitize_message_id(message_id: str) -> str:
    """Sanitiza un message_id reemplazando el número de teléfono con un hash.

    Los message_id de WhatsApp tienen formato true_<phone>@c.us_<random>.
    El número de teléfono es PII según Ley 21.719 — lo hasheamos con SHA-256
    truncado a 12 chars, preservando trazabilidad sin leakear datos personales.

    Si el message_id no calza con el formato esperado, aplica sanitización
    básica (solo alfanumérico + guiones + underscore).

    Args:
        message_id: ID de mensaje proveniente del webhook (no confiable).

    Returns:
        Versión sanitizada con el teléfono hasheado. Si queda vacío, retorna 'unknown'.
    """
    import hashlib

    # Primero, sanitizar caracteres no seguros preservando @ y . (necesarios para el parseo)
    cleaned = re.sub(r"[^a-zA-Z0-9_\-@.]", "_", message_id)

    # Hashear TODOS los números de teléfono en el message_id.
    # Formato WhatsApp: true_<phone>@c.us (primera ocurrencia) o _<phone>@c.us (resto).
    # \d+ no matchea hashes hex (tienen letras a-f), así que no hay riesgo de doble hasheo.
    phone_re = re.compile(r"(true_|_)(\d+)(@c\.us)")
    result = phone_re.sub(
        lambda m: f"{m.group(1)}{hashlib.sha256(m.group(2).encode()).hexdigest()[:12]}{m.group(3)}",
        cleaned,
    )

    # Si no se encontró ningún teléfono, aplicar sanitización básica
    if result == cleaned:
        sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "_", message_id)
        return sanitized if sanitized else "unknown"

    return result


def validate_path_in_audio_dir(path: Path, audio_dir: Path) -> Path:
    """Valida que un path resuelto esté dentro del directorio de audio.

    Previene path traversal: resuelve el path real y verifica que
    esté dentro del directorio base.

    Args:
        path: Path a validar.
        audio_dir: Directorio base (resuelto).

    Returns:
        Path resuelto si es seguro.

    Raises:
        ValueError: Si el path está fuera de audio_dir (path traversal).
    """
    resolved = path.resolve()
    audio_resolved = audio_dir.resolve()
    if not str(resolved).startswith(str(audio_resolved) + "/") and resolved != audio_resolved:
        logger.warning(
            "Path traversal detectado — path=%s audio_dir=%s resolved=%s",
            path,
            audio_dir,
            resolved,
        )
        raise ValueError(f"Path fuera del directorio de audio: {resolved}")
    return resolved


def _get_audio_temp_dir() -> Path:
    """Devuelve el directorio de audio temporal, creándolo si no existe."""
    _AUDIO_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    return _AUDIO_TEMP_DIR


def convert_ogg_to_wav(input_path: Path, output_path: Path) -> None:
    """Convierte audio .ogg a .wav 16kHz mono con ffmpeg.

    Args:
        input_path: Ruta al archivo .ogg de entrada.
        output_path: Ruta al archivo .wav de salida.

    Raises:
        subprocess.CalledProcessError: Si ffmpeg falla.
        FileNotFoundError: Si ffmpeg no está instalado.
    """
    cmd = [
        "ffmpeg",
        "-y",  # Sobrescribir output si existe
        "-i",
        str(input_path),
        "-acodec",
        "pcm_s16le",  # PCM 16-bit little-endian
        "-ar",
        "16000",  # 16kHz sample rate
        "-ac",
        "1",  # Mono
        "-fs",
        str(100 * 1024 * 1024),  # Límite 100 MB output — previene decompression bomb
        str(output_path),
    ]

    logger.info("Convirtiendo audio — input=%s output=%s", input_path.name, output_path.name)
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=30)
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg timeout (30s) — input=%s", input_path.name)
        raise
    except subprocess.CalledProcessError as exc:
        logger.error(
            "ffmpeg falló — input=%s stderr=%s",
            input_path.name,
            exc.stderr.decode("utf-8", errors="replace") if exc.stderr else "(sin stderr)",
        )
        raise
    logger.info(
        "Audio convertido — input_size=%d output_size=%d",
        input_path.stat().st_size,
        output_path.stat().st_size,
    )


def get_audio_duration_ms(wav_path: Path) -> int:
    """Obtiene la duración del audio WAV en milisegundos usando ffprobe.

    Si ffprobe no está disponible o falla, retorna 0.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(wav_path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        return int(float(result.stdout.strip()) * 1000)
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        OSError,  # Cubre FileNotFoundError, PermissionError, IsADirectoryError, etc.
        ValueError,
        OverflowError,
    ):
        return 0


class AudioService:
    """Servicio para el pipeline de procesamiento de audio de WhatsApp.

    Orquesta: descarga desde Open-WA → conversión .ogg → .wav →
    validación de paths y logging estructurado.

    El directorio de audio temporal se puede inyectar para testing
    (sin monkeypatch frágil). Si no se provee, usa _get_audio_temp_dir().
    """

    def __init__(self, audio_temp_dir: Path | None = None) -> None:
        """Inicializa el servicio con un directorio de audio temporal opcional.

        Args:
            audio_temp_dir: Directorio para archivos temporales.
                           Si es None, usa el default _get_audio_temp_dir().
        """
        self._audio_temp_dir = audio_temp_dir or _get_audio_temp_dir()

    async def process_audio(
        self,
        payload: WebhookPayload,
        phone: str,
        request_id: str,
    ) -> None:
        """Procesa el audio en background: descarga, convierte, guarda.

        Se ejecuta después de que el endpoint ya retornó 200.
        No bloquea la respuesta al webhook de Open-WA.

        Args:
            payload: Payload del webhook parseado.
            phone: Número de teléfono del remitente (para logging).
            request_id: ID del request para trazabilidad en logs.
        """
        raw_message_id = payload.message.id
        message_id = sanitize_message_id(raw_message_id)
        phone_hash = hash_phone(phone, settings.phone_hash_pepper)
        start_time = time.monotonic()

        ogg_path: Path | None = None
        wav_path: Path | None = None

        try:
            # Descargar audio .ogg desde Open-WA (usa ID original — IDs WhatsApp contienen @ que Open-WA espera)
            openwa = OpenWAService()
            ogg_data = await openwa.download_media(raw_message_id)

            # Validar tamaño máximo defensivo
            if len(ogg_data) > _MAX_AUDIO_SIZE_BYTES:
                logger.warning(
                    "Audio excede tamaño máximo — message_id=%s size_bytes=%d max_bytes=%d",
                    message_id,
                    len(ogg_data),
                    _MAX_AUDIO_SIZE_BYTES,
                )
                return

            file_tag = uuid.uuid4().hex[:12]
            ogg_path = validate_path_in_audio_dir(
                self._audio_temp_dir / f"{message_id}_{file_tag}.ogg",
                self._audio_temp_dir,
            )
            wav_path = validate_path_in_audio_dir(
                self._audio_temp_dir / f"{message_id}_{file_tag}.wav",
                self._audio_temp_dir,
            )

            # Guardar .ogg temporal
            ogg_path.write_bytes(ogg_data)
            logger.info(
                "Audio guardado — message_id=%s phone_hash=%s size_bytes=%d path=%s",
                message_id,
                phone_hash,
                len(ogg_data),
                ogg_path,
            )

            # Convertir .ogg → .wav 16kHz mono (en thread aparte para no bloquear event loop)
            await asyncio.to_thread(convert_ogg_to_wav, ogg_path, wav_path)

            audio_duration_ms = await asyncio.to_thread(get_audio_duration_ms, wav_path)
            logger.info(
                "Audio listo para pipeline — message_id=%s phone_hash=%s wav_path=%s "
                "duration_ms=%d",
                message_id,
                phone_hash,
                wav_path,
                audio_duration_ms,
            )

            # Enviar respuesta de audio fija (hola mundo end-to-end).
            # Punto de medición de latencia E2E: desde recepción del webhook hasta envío.
            if _HELLO_OGG_PATH.exists():
                await openwa.send_audio(phone, str(_HELLO_OGG_PATH))
                e2e_ms = int((time.monotonic() - start_time) * 1000)
                logger.info(
                    "Respuesta enviada — message_id=%s phone_hash=%s e2e_ms=%d",
                    message_id,
                    phone_hash,
                    e2e_ms,
                )
            else:
                logger.warning(
                    "hello.ogg no encontrado — message_id=%s path=%s",
                    message_id,
                    _HELLO_OGG_PATH,
                )

            # .ogg se limpia en finally — no duplicar cleanup acá

        except (
            httpx.HTTPError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
            ValueError,
            RuntimeError,
            TypeError,
        ):
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.exception(
                "Error procesando audio en background — message_id=%s phone_hash=%s "
                "elapsed_ms=%d request_id=%s",
                message_id,
                phone_hash,
                int(elapsed_ms),
                request_id,
            )
            # Limpiar archivos temporales en error para no acumular basura en disco
            if ogg_path is not None:
                ogg_path.unlink(missing_ok=True)
            if wav_path is not None:
                wav_path.unlink(missing_ok=True)
        except asyncio.CancelledError:
            # CancelledError DEBE re-lanzarse en Python 3.12+ para que la
            # cancelación de la tarea se propague correctamente.
            # Limpiamos archivos temporales antes de re-lanzar.
            logger.warning(
                "Procesamiento de audio cancelado — message_id=%s phone_hash=%s request_id=%s",
                message_id,
                phone_hash,
                request_id,
            )
            if ogg_path is not None:
                ogg_path.unlink(missing_ok=True)
            if wav_path is not None:
                wav_path.unlink(missing_ok=True)
            raise
        finally:
            # Limpiar archivos temporales SIEMPRE, incluso si la excepción
            # no fue capturada por el bloque except. Previene acumulación
            # de archivos huérfanos en disco (P1-4).
            # Solo limpia el .ogg temporal — el .wav es la salida del pipeline
            # y debe persistir para la etapa de transcripción (Whisper).
            # En error, los bloques except ya limpian ambos.
            if ogg_path is not None:
                ogg_path.unlink(missing_ok=True)
