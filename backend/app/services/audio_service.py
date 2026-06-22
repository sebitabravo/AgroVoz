"""Servicio de procesamiento de audio para el pipeline de voz.

Convierte audio .ogg de WhatsApp a .wav 16kHz mono usando ffmpeg,
valida paths, y orquesta la decodificacion + conversion en background.

El audio llega como base64 inline en el webhook de Open-WA (type=voice),
no se descarga via API.
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
from app.services.openwa_service import OpenWAService
from app.services.tts_service import PiperModelNotFoundError, TTSService
from app.services.whisper_service import WhisperService

logger = logging.getLogger(__name__)

# Directorio donde se almacena temporalmente el audio convertido.
# En Docker: /app/data/audio_temp/ (montado como volumen en backend/data/).
# En local: backend/data/audio_temp/.
_AUDIO_TEMP_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "audio_temp"

# Audio de respuesta fijo: se envia mientras el pipeline TTS completo no esta implementado.
# Pregrabado con macOS TTS (Eddy es_ES) -> libopus@48kHz mono.
_HELLO_OGG_PATH = Path(__file__).resolve().parent.parent / "static" / "hello.ogg"

# Tamano maximo de archivo de audio (25 MB). WhatsApp limita audios a ~16 MB,
# pero este limite es defensivo contra archivos maliciosos o corruptos.
_MAX_AUDIO_SIZE_BYTES = 25 * 1024 * 1024

# Duracion maxima de audio para transcripcion Whisper.
# asyncio.wait_for cancela la coroutine pero NO el thread subyacente; Python
# no soporta terminacion forzada de threads. Con RTF CPU ~2x, un audio de
# 15s tarda ~30s en transcribir (justo el timeout). Limitamos a 12s para
# dejar margen y evitar threads zombie que saturen el VPS.
_MAX_WHISPER_AUDIO_MS = 12_000


def sanitize_message_id(message_id: str) -> str:
    """Sanitiza un message_id reemplazando el numero de telefono con un hash.

    Los message_id de WhatsApp tienen formato true_<phone>@c.us_<random>.
    El numero de telefono es PII segun Ley 21.719 — lo hasheamos con SHA-256
    truncado a 12 chars, preservando trazabilidad sin leakear datos personales.

    Si el message_id no calza con el formato esperado, aplica sanitizacion
    basica (solo alfanumerico + guiones + underscore).

    Args:
        message_id: ID de mensaje proveniente del webhook (no confiable).

    Returns:
        Version sanitizada con el telefono hasheado. Si queda vacio, retorna 'unknown'.
    """
    import hashlib

    # Primero, sanitizar caracteres no seguros preservando @ y . (necesarios para el parseo)
    cleaned = re.sub(r"[^a-zA-Z0-9_\-\@.]", "_", message_id)

    # Hashear TODOS los numeros de telefono en el message_id.
    # Formato WhatsApp: true_<phone>@c.us (primera ocurrencia) o _<phone>@c.us (resto).
    # Los IDs modernos pueden ser @lid en vez de @c.us — buscamos digitos antes de @.
    phone_re = re.compile(r"(true_|_)(\d+)(@c\.us|@lid)")
    result = phone_re.sub(
        lambda m: f"{m.group(1)}{hashlib.sha256(m.group(2).encode()).hexdigest()[:12]}{m.group(3)}",
        cleaned,
    )

    # Si no se encontro ningun telefono, aplicar sanitizacion basica
    if result == cleaned:
        sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "_", message_id)
        return sanitized if sanitized else "unknown"

    return result


def validate_path_in_audio_dir(path: Path, audio_dir: Path) -> Path:
    """Valida que un path resuelto este dentro del directorio de audio.

    Previene path traversal: resuelve el path real y verifica que
    este dentro del directorio base.

    Args:
        path: Path a validar.
        audio_dir: Directorio base (resuelto).

    Returns:
        Path resuelto si es seguro.

    Raises:
        ValueError: Si el path esta fuera de audio_dir (path traversal).
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
    """Devuelve el directorio de audio temporal, creandolo si no existe."""
    _AUDIO_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    return _AUDIO_TEMP_DIR


def convert_ogg_to_wav(input_path: Path, output_path: Path) -> None:
    """Convierte audio .ogg a .wav 16kHz mono con ffmpeg.

    Args:
        input_path: Ruta al archivo .ogg de entrada.
        output_path: Ruta al archivo .wav de salida.

    Raises:
        subprocess.CalledProcessError: Si ffmpeg falla.
        FileNotFoundError: Si ffmpeg no esta instalado.
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
        str(100 * 1024 * 1024),  # Limite 100 MB output — previene decompression bomb
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
            "ffmpeg fallo — input=%s stderr=%s",
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
    """Obtiene la duracion del audio WAV en milisegundos usando ffprobe.

    Si ffprobe no esta disponible o falla, retorna 0.
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

    Orquesta: decodificar base64 inline -> disco -> conversion .ogg -> .wav ->
    validacion de paths y logging estructurado.

    El audio llega como base64 inline en el webhook de Open-WA (type=voice),
    no se descarga via API de Open-WA.

    El directorio de audio temporal se puede inyectar para testing
    (sin monkeypatch fragil). Si no se provee, usa _get_audio_temp_dir().
    """

    def __init__(self, audio_temp_dir: Path | None = None) -> None:
        """Inicializa el servicio con un directorio de audio temporal opcional.

        Args:
            audio_temp_dir: Directorio para archivos temporales.
                           Si es None, usa el default _get_audio_temp_dir().
        """
        self._audio_temp_dir = audio_temp_dir or _get_audio_temp_dir()

    @staticmethod
    def _build_response_text(transcribed_text: str) -> str:
        """Construye el texto de respuesta a partir de la transcripcion.

        En MVP, repite la transcripcion al productor para validar que el
        pipeline completo funciona (bucle cerrado voz->texto->voz).
        Cuando se integre el LLM, este metodo se reemplazara por la
        invocacion al modelo con los resultados de precio/clima.

        Args:
            transcribed_text: Texto transcrito por Whisper.

        Returns:
            Texto listo para sintetizar con Piper.
        """
        return (
            f"Usted dijo: {transcribed_text.strip()}. "
            "Estamos procesando su consulta."
        )

    async def process_audio(
        self,
        audio_bytes: bytes,
        chat_id: str,
        request_id: str,
    ) -> None:
        """Procesa el audio en background: guarda, convierte, responde.

        Se ejecuta despues de que el endpoint ya retorno 200.
        No bloquea la respuesta al webhook de Open-WA.

        El audio ya viene decodificado del base64 inline del webhook.
        No se descarga via API de Open-WA.

        Args:
            audio_bytes: Contenido del archivo .ogg (decodificado de base64).
            chat_id: Identificador del chat para responder (formato @c.us o @lid).
            request_id: ID del request para trazabilidad en logs.
        """
        message_id = f"audio_{uuid.uuid4().hex[:8]}"
        chat_id_hash = hash_phone(chat_id, settings.phone_hash_pepper) if chat_id else "sin_chat"
        start_time = time.monotonic()

        ogg_path: Path | None = None
        wav_path: Path | None = None

        try:
            # Validar bytes de audio antes de cualquier procesamiento
            if not audio_bytes:
                logger.warning(
                    "Audio vacio recibido — message_id=%s request_id=%s",
                    message_id,
                    request_id,
                )
                return

            # Validar tamano maximo defensivo
            if len(audio_bytes) > _MAX_AUDIO_SIZE_BYTES:
                logger.warning(
                    "Audio excede tamano maximo — message_id=%s size_bytes=%d max_bytes=%d",
                    message_id,
                    len(audio_bytes),
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

            # Guardar .ogg temporal desde los bytes recibidos
            ogg_path.write_bytes(audio_bytes)
            logger.info(
                "Audio guardado — message_id=%s chat_id_hash=%s size_bytes=%d path=%s request_id=%s",
                message_id,
                chat_id_hash,
                len(audio_bytes),
                ogg_path,
                request_id,
            )

            # Convertir .ogg -> .wav 16kHz mono (en thread aparte para no bloquear event loop)
            await asyncio.to_thread(convert_ogg_to_wav, ogg_path, wav_path)

            audio_duration_ms = await asyncio.to_thread(get_audio_duration_ms, wav_path)
            logger.info(
                "Audio listo para pipeline — message_id=%s chat_id_hash=%s wav_path=%s duration_ms=%d request_id=%s",
                message_id,
                chat_id_hash,
                wav_path,
                audio_duration_ms,
                request_id,
            )

            # Transcripcion Whisper (en thread aparte para no bloquear event loop).
            # El modelo se carga lazy en la primera llamada.
            # Si Whisper falla (OOM, cold start, audio corrupto), se loguea
            # pero el pipeline CONTINUA para que el agricultor reciba respuesta
            # de voz (P1 del code review).
            transcribed_text = ""
            if audio_duration_ms > _MAX_WHISPER_AUDIO_MS:
                logger.warning(
                    "Audio demasiado largo para transcripcion Whisper — "
                    "message_id=%s duration_ms=%d limite_ms=%d request_id=%s",
                    message_id,
                    audio_duration_ms,
                    _MAX_WHISPER_AUDIO_MS,
                    request_id,
                )
            else:
                try:
                    whisper = WhisperService()
                    transcription: dict[str, object] = await asyncio.wait_for(
                        asyncio.to_thread(whisper.transcribe, str(wav_path)),
                        timeout=30.0,
                    )
                    transcribed_text = str(transcription.get("text", ""))
                    logger.info(
                        "Audio transcrito — message_id=%s text=%s chars=%d whisper_ms=%d request_id=%s",
                        message_id,
                        transcribed_text[:200],
                        len(transcribed_text),
                        transcription.get("duration_ms", 0),
                        request_id,
                    )
                except (RuntimeError, FileNotFoundError, ValueError, TimeoutError) as exc:
                    logger.warning(
                        "Whisper fallo — continuando sin transcripcion: message_id=%s "
                        "error=%s request_id=%s",
                        message_id,
                        exc,
                        request_id,
                    )

            # Sintetizar respuesta de audio con Piper TTS.
            # Estrategia de fallback: si el modelo Piper no esta disponible
            # (no descargado, primer deploy) o falla, se envia hello.ogg.
            # Esto permite que el pipeline funcione sin modelo TTS durante
            # desarrollo y CI.
            response_ogg_path: str | None = None
            if transcribed_text:
                try:
                    tts = TTSService()
                    if transcribed_text.strip():
                        response_text = self._build_response_text(transcribed_text)
                        response_ogg_path = tts.synthesize(response_text)
                except (PiperModelNotFoundError, RuntimeError, ValueError, OSError) as exc:
                    logger.warning(
                        "TTS fallo — message_id=%s error=%s request_id=%s",
                        message_id,
                        exc,
                        request_id,
                    )

            # Fallback a hello.ogg si TTS no genero audio
            if response_ogg_path is None:
                if _HELLO_OGG_PATH.exists():
                    response_ogg_path = str(_HELLO_OGG_PATH)
                else:
                    logger.warning(
                        "hello.ogg no encontrado — message_id=%s path=%s",
                        message_id,
                        _HELLO_OGG_PATH,
                    )

            # Enviar respuesta de audio
            if response_ogg_path:
                openwa = OpenWAService()
                await openwa.send_audio(chat_id, response_ogg_path)
                e2e_ms = int((time.monotonic() - start_time) * 1000)
                logger.info(
                    "Respuesta enviada — message_id=%s chat_id_hash=%s audio=%s e2e_ms=%d request_id=%s",
                    message_id,
                    chat_id_hash,
                    Path(response_ogg_path).name,
                    e2e_ms,
                    request_id,
                )

                # Limpiar archivo TTS generado (no limpiar hello.ogg que es static)
                if response_ogg_path != str(_HELLO_OGG_PATH):
                    Path(response_ogg_path).unlink(missing_ok=True)

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
                "Error procesando audio en background — message_id=%s chat_id_hash=%s elapsed_ms=%d request_id=%s",
                message_id,
                chat_id_hash,
                int(elapsed_ms),
                request_id,
            )
        except asyncio.CancelledError:
            logger.warning(
                "Procesamiento de audio cancelado — message_id=%s chat_id_hash=%s request_id=%s",
                message_id,
                chat_id_hash,
                request_id,
            )
            raise
        finally:
            # Limpiar archivos temporales SIEMPRE, incluso si la excepcion
            # no fue capturada por el bloque except. Previene acumulacion
            # de archivos huerfanos en disco (P1-4).
            if ogg_path is not None:
                ogg_path.unlink(missing_ok=True)
            if wav_path is not None:
                wav_path.unlink(missing_ok=True)
