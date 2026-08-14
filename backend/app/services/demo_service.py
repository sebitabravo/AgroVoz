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
from app.services.llm_keywords import _detect_greeting, _force_keyword_tool
from app.services.llm_service import FALLBACK_TEXT, answer_with_provider_order
from app.services.pipeline_service import AgroVozPipeline
from app.services.tts_service import TTSService
from app.services.whisper_service import WhisperService

logger = logging.getLogger(__name__)

_DEMO_CHAT_ID_HASH = "demo"
_MAX_AUDIO_BYTES = 5 * 1024 * 1024
_DEMO_GREETING_TEXT = (
    "¡Hola! Pregúntame por el precio de algún producto o por el clima de Traiguén."
)
_DEMO_CLIMATE_MARKERS = (
    "clima",
    "tiempo",
    "llov",
    "temperatura",
    "humedad",
    "viento",
    "helad",
    "granizo",
    "nieve",
)
_DEMO_FOLLOW_UP_MARKERS = ("mañana", "manana")


def _decode_audio_base64(audio_base64: str) -> bytes:
    """Decodifica audio base64 validando límites de tamaño."""
    try:
        audio_bytes = base64.b64decode(audio_base64, validate=True)
    except ValueError as exc:
        raise ValueError("El audio no está en formato base64 válido") from exc

    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        raise ValueError(
            f"El audio excede el tamaño máximo permitido de {_MAX_AUDIO_BYTES} bytes"
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


def _resolve_demo_weather_follow_up(
    query_text: str,
    history: list[dict[str, object]],
) -> str:
    """Completa un seguimiento climático breve con la última comuna conocida."""
    normalized = query_text.casefold()
    if not any(marker in normalized for marker in _DEMO_FOLLOW_UP_MARKERS):
        return query_text

    from app.services.weather_service import extraer_comuna_de_consulta

    if extraer_comuna_de_consulta(query_text) is not None:
        return query_text

    for turn in reversed(history):
        if turn.get("role") != "user":
            continue
        previous_raw = turn.get("content", "")
        if not isinstance(previous_raw, str):
            continue
        previous = previous_raw
        if not any(marker in previous.casefold() for marker in _DEMO_CLIMATE_MARKERS):
            continue
        comuna = extraer_comuna_de_consulta(previous)
        if comuna is None:
            continue
        periodo = "pasado mañana" if "pasado" in normalized else "mañana"
        return f"¿Va a llover {periodo} en {comuna}?"

    return query_text


async def _generate_demo_response(
    query_text: str,
    history: list[dict[str, object]] | None = None,
) -> tuple[str, str]:
    """Genera la respuesta de la demo por el MISMO camino que WhatsApp.

    Antes esto iba directo al LLM, saltandose el fast-path. El efecto era que
    la demo mostraba algo PEOR que el producto: "a cuanto esta la papa" tardaba
    31s y terminaba en timeout, cuando por WhatsApp se responde en ~100 ms sin
    tocar el LLM. Una demo que se comporta distinto al producto no demuestra
    nada; por eso replica el orden real: fast-path primero, LLM despues.
    """
    history = history or []
    resolved_query = _resolve_demo_weather_follow_up(query_text, history)

    if _detect_greeting(resolved_query):
        return _DEMO_GREETING_TEXT, "saludo"

    # Fast-path deterministico: mismo gate que usa el pipeline de voz y texto.
    # Para la consulta tipica las tools ya arman la frase final con el dato de
    # ODEPA y el LLM no aporta nada que el productor escuche.
    consulta_tipo: str | None = None
    try:
        extracted = AgroVozPipeline._extract_variables(resolved_query)
        consulta_tipo = extracted.consulta_tipo
        if "semilla" in resolved_query.casefold():
            semilla = await _force_keyword_tool(resolved_query, phone_hash=_DEMO_CHAT_ID_HASH)
            if semilla:
                return semilla, "precio"
        if extracted.consulta_tipo == "desconocido":
            return FALLBACK_TEXT, "desconocido"
        if extracted.consulta_tipo == "agronomica":
            # La demo debe usar exactamente el fast path citado de WhatsApp:
            # el LLM no puede completar una regla o calendario que no exista.
            response_text, intent = await AgroVozPipeline._generate_response(
                resolved_query,
                _DEMO_CHAT_ID_HASH,
            )
            return response_text, intent
        if extracted.consulta_tipo == "precio" and extracted.producto is None:
            return "No tengo datos ODEPA para ese producto. ¿Podrías consultar otro producto?", "precio"
        if AgroVozPipeline._puede_usar_fast_path(resolved_query, extracted, None):
            rapida = await _force_keyword_tool(resolved_query, phone_hash=_DEMO_CHAT_ID_HASH)
            if rapida:
                intent_rapido = "precio" if extracted.consulta_tipo == "precio" else "clima"
                logger.info(
                    "Demo fast-path sin LLM — tipo=%s producto=%s",
                    intent_rapido,
                    extracted.producto,
                )
                return rapida, intent_rapido
    except (RuntimeError, OSError, ValueError):
        # El fast-path es un atajo, no un requisito: si falla se sigue al LLM.
        logger.warning("Fast-path de demo falló — fallback=llm")

    try:
        response_text, provider = await answer_with_provider_order(
            resolved_query,
            history=history,
            phone_hash=_DEMO_CHAT_ID_HASH,
            consulta_tipo=consulta_tipo,
        )
        logger.info("Proveedor LLM demo seleccionado — provider=%s", provider)
    except (TimeoutError, RuntimeError, OSError, ValueError) as exc:
        logger.error(
            "Error en generación LLM para demo — error=%s",
            type(exc).__name__,
        )
        response_text = "Tuve un problema al procesar tu consulta. ¿Podrías intentarlo de nuevo?"

    intent = AgroVozPipeline._detect_intent(resolved_query, response_text)
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
        if request.historial:
            raise ValueError("El historial solo está disponible para consultas de texto")
        query_text = await _transcribe_audio_base64(request.audio_base64, audio_temp_dir)
    else:
        raise ValueError("Debes enviar texto o audio")

    history: list[dict[str, object]] = [
        {"role": message.rol, "content": message.texto}
        for message in request.historial
    ]
    response_text, intent = await _generate_demo_response(query_text, history=history)

    if not response_text.strip():
        response_text = "No entendí tu consulta. ¿Podrías intentarlo de nuevo?"
        intent = "desconocido"

    audio_base64 = ""
    if response_text.strip():
        try:
            audio_base64 = await _synthesize_response(response_text, audio_temp_dir)
        except (RuntimeError, ValueError, OSError) as exc:
            logger.warning(
                "Demo TTS falló — error=%s",
                type(exc).__name__,
            )
            audio_base64 = ""

    latency_ms = int((time.monotonic() - start_time) * 1000)

    return DemoRespuestaResponse(
        texto=response_text,
        audio_base64=audio_base64,
        intent=intent,
        latency_ms=latency_ms,
    )
