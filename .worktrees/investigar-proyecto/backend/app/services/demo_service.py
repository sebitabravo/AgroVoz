"""Servicio para el endpoint demo de chat web.

Issue #119 — permite a visitantes de la landing probar AgroVoz con texto
(o audio opcional) sin pasar por WhatsApp ni Open-WA. Las consultas demo
NO se persisten en la tabla de consultations.
"""

import asyncio
import base64
import logging
import time
import uuid
from pathlib import Path

from app.schemas.demo import DemoPreguntaRequest, DemoRespuestaResponse
from app.services.audio_service import (
    _get_audio_temp_dir,
    convert_ogg_to_wav,
    get_audio_duration_ms,
    validate_path_in_audio_dir,
)
from app.services.llm_keywords import _detect_greeting
from app.services.llm_service import answer
from app.services.pipeline_service import AgroVozPipeline
from app.services.tts_service import TTSService
from app.services.whisper_service import WhisperService

logger = logging.getLogger(__name__)

_DEMO_CHAT_ID_HASH = "demo"
_MAX_AUDIO_BYTES = 5 * 1024 * 1024
_DEMO_GREETING_TEXT = (
    "Hola! Preguntame por el precio de algun producto o por el clima de Traiguen."
)


def _decode_audio_base64(audio_base64: str) -> bytes:
    """Decodifica audio base64 validando limites de tamano."""
    try:
        audio_bytes = base64.b64decode(audio_base64, validate=True)
    except ValueError as exc:
        raise ValueError("El audio no esta en formato base64 valido") from exc

    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        raise ValueError(
            f"El audio excede el tamano maximo permitido de {_MAX_AUDIO_BYTES} bytes"
        )

    return audio_bytes


async def _transcribe_audio_base64(audio_base64: str, audio_temp_dir: Path) -> str:
    """Transcribe audio base64 a texto usando Whisper."""
    audio_bytes = _decode_audio_base64(audio_base64)
    file_tag = uuid.uuid4().hex[:12]
    ogg_path = validate_path_in_audio_dir(
        audio_temp_dir / f"demo_{file_tag}.ogg", audio_temp_dir,
    )
    wav_path = validate_path_in_audio_dir(
        audio_temp_dir / f"demo_{file_tag}.wav", audio_temp_dir,
    )

    try:
        ogg_path.write_bytes(audio_bytes)
        await asyncio.to_thread(convert_ogg_to_wav, ogg_path, wav_path)
        audio_duration_ms = await asyncio.to_thread(get_audio_duration_ms, wav_path)

        whisper = WhisperService()
        transcription: dict[str, object] = await asyncio.wait_for(
            asyncio.to_thread(whisper.transcribe, str(wav_path)),
            timeout=30.0,
        )
        transcribed_text = str(transcription.get("text", "")).strip()
        logger.info(
            "Demo audio transcrito — file_tag=%s duration_ms=%d chars=%d",
            file_tag, audio_duration_ms, len(transcribed_text),
        )
        return transcribed_text
    finally:
        ogg_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)


async def _synthesize_response(text: str, audio_temp_dir: Path) -> str:
    """Sintetiza texto a audio OGG/Opus y retorna base64."""
    tts = TTSService()
    ogg_path = await asyncio.to_thread(tts.synthesize, text, output_dir=audio_temp_dir)
    try:
        audio_bytes = Path(ogg_path).read_bytes()
        return base64.b64encode(audio_bytes).decode("utf-8")
    finally:
        Path(ogg_path).unlink(missing_ok=True)


async def _generate_demo_response(query_text: str) -> tuple[str, str]:
    """Genera respuesta de texto para la demo reutilizando el LLM."""
    if _detect_greeting(query_text):
        return _DEMO_GREETING_TEXT, "saludo"

    try:
        response_text = await answer(query_text, phone_hash=_DEMO_CHAT_ID_HASH)
    except (TimeoutError, RuntimeError, OSError, ValueError):
        logger.exception("Error en generacion LLM para demo")
        response_text = "Tuve un problema al procesar tu consulta. Podrias intentar de nuevo?"

    intent = AgroVozPipeline._detect_intent(query_text, response_text)
    return response_text, intent


async def process_demo_request(request: DemoPreguntaRequest) -> DemoRespuestaResponse:
    """Procesa una consulta demo: texto/audio -> LLM -> TTS.

    No persiste la consulta en DB. Reusa el LLM y TTS del pipeline real.
    """
    start_time = time.monotonic()
    audio_temp_dir = _get_audio_temp_dir()

    if request.texto.strip():
        query_text = request.texto.strip()
    elif request.audio_base64:
        query_text = await _transcribe_audio_base64(request.audio_base64, audio_temp_dir)
    else:
        raise ValueError("Debes enviar texto o audio")

    response_text, intent = await _generate_demo_response(query_text)

    if not response_text.strip():
        response_text = "No entendi tu consulta. Podrias intentar de nuevo?"
        intent = "desconocido"

    audio_base64 = ""
    if response_text.strip():
        try:
            audio_base64 = await _synthesize_response(response_text, audio_temp_dir)
        except (RuntimeError, ValueError, OSError) as exc:
            logger.warning("Demo TTS fallo — error=%s", exc)
            audio_base64 = ""

    latency_ms = int((time.monotonic() - start_time) * 1000)

    return DemoRespuestaResponse(
        texto=response_text,
        audio_base64=audio_base64,
        intent=intent,
        latency_ms=latency_ms,
    )
