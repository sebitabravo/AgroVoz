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

# Tamaño máximo de archivo de audio (25 MB). WhatsApp limita audios a ~16 MB,
# pero este límite es defensivo contra archivos maliciosos o corruptos.
_MAX_AUDIO_SIZE_BYTES = 25 * 1024 * 1024


def sanitize_message_id(message_id: str) -> str:
    """Sanitiza un message_id para uso seguro en paths y URLs.

    Solo permite caracteres alfanuméricos, guiones y underscores.
    Cualquier otro carácter se reemplaza por '_'.

    Args:
        message_id: ID de mensaje proveniente del webhook (no confiable).

    Returns:
        Versión sanitizada del message_id. Si queda vacío después de
        sanitizar, retorna 'unknown'.
    """
    sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "_", message_id)
    return sanitized if sanitized else "unknown"


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
        FileNotFoundError,
        ValueError,
        OverflowError,
    ):
        return 0


class AudioService:
    """Servicio para el pipeline de procesamiento de audio de WhatsApp.

    Orquesta: descarga desde Open-WA → conversión .ogg → .wav →
    validación de paths y logging estructurado.
    """

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

            audio_dir = _get_audio_temp_dir()
            file_tag = uuid.uuid4().hex[:12]
            ogg_path = validate_path_in_audio_dir(
                audio_dir / f"{message_id}_{file_tag}.ogg", audio_dir
            )
            wav_path = validate_path_in_audio_dir(
                audio_dir / f"{message_id}_{file_tag}.wav", audio_dir
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

            elapsed_ms = (time.monotonic() - start_time) * 1000
            audio_duration_ms = await asyncio.to_thread(get_audio_duration_ms, wav_path)
            logger.info(
                "Audio listo para pipeline — message_id=%s phone_hash=%s wav_path=%s "
                "duration_ms=%d elapsed_ms=%d",
                message_id,
                phone_hash,
                wav_path,
                audio_duration_ms,
                elapsed_ms,
            )

            # Borrar .ogg temporal (ya tenemos el .wav)
            ogg_path.unlink(missing_ok=True)

        except (httpx.HTTPError, subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError):
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
