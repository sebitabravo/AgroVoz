"""Orquestador del pipeline de voz: audio → Whisper → LLM → TTS.

Issue #18 — Checkpoint C. AgroVozPipeline coordina las etapas del pipeline
con timeout interno de 120s y benchmark de latencia por etapa.

El pipeline recibe el path al archivo .wav (ya convertido por audio_service)
y ejecuta: transcripcion Whisper → generacion LLM con Tool Calling →
sintesis Piper TTS → guardado de consulta en DB.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.schemas.pipeline import AudioResponse
from app.services.tts_service import PiperModelNotFoundError, TTSService
from app.services.whisper_service import WhisperService

logger = logging.getLogger(__name__)

# Timeout interno del pipeline. Si el pipeline completo excede este limite,
# se aborta y se retorna AudioResponse con texto de error. Distinto del
# target de producto <15s.
# Aumentado a 120s para desarrollo: el LLM en CPU emulada (ARM64 Docker)
# necesita ~5-9s cold start + 20-40s por generacion, y el tool calling loop
# requiere 2 generaciones. En produccion (x86_64), cold start solo 1 vez.
_PIPELINE_TIMEOUT = 120.0

# Duracion maxima de audio para transcripcion Whisper (ms).
# asyncio.wait_for cancela la coroutine pero NO el thread subyacente.
# Con RTF CPU ~2x, limitamos a 12s para evitar threads zombie.
_MAX_WHISPER_AUDIO_MS = 12_000

# Cache singleton de TTSService: el modelo Piper se carga UNA vez.
_tts_service: TTSService | None = None

# Texto fijo de bienvenida para primer contacto (issue #86).
# No requiere LLM: es un mensaje predefinido sintetizado con TTS.
# Corto (<200 chars) para que el audio dure <10s y no fatigue al agricultor.
_WELCOME_TEXT = (
    "Hola, te doy la bienvenida a AgroVoz. "
    "Soy un asistente de voz que te ayuda a consultar "
    "precios de productos agricolas y el clima. "
    "Solo mandame un audio con tu pregunta y te respondere."
)


def _get_tts_service() -> TTSService:
    """Retorna la instancia singleton de TTSService."""
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service


class AgroVozPipeline:
    """Orquestador del pipeline de voz completo.

    Coordina las etapas: Whisper → LLM → TTS con timeout de 120s
    y benchmark de latencia por etapa para monitoreo.

    Uso:
        pipeline = AgroVozPipeline()
        response = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=duration_ms,
            message_id=msg_id,
            chat_id_hash=chat_hash,
            request_id=req_id,
        )
    """

    def __init__(self, pipeline_timeout: float = _PIPELINE_TIMEOUT) -> None:
        """Inicializa el pipeline con timeout configurable.

        Args:
            pipeline_timeout: Timeout total del pipeline en segundos.
                              Default 120s (red de seguridad, no target).
        """
        self._timeout = pipeline_timeout

    @staticmethod
    def _detect_intent(query_text: str, llm_response: str) -> str:
        """Detecta la intencion de la consulta para metrica.

        Derivada principalmente de la respuesta del LLM (que contiene
        datos reales de tools ejecutadas), no de keywords en la consulta.
        Esto evita falsos positivos como "mercado" o "vale" en saludos.

        Args:
            query_text: Texto original transcrito.
            llm_response: Respuesta generada por el LLM.

        Returns:
            "precio", "clima", o "desconocido".
        """
        # Priorizar respuesta del LLM: si ejecuto tools, la respuesta
        # contiene datos concretos (precios, grados, etc).
        text = (llm_response + " " + query_text).lower()

        # Indicadores fuertes de precio (datos reales, no keywords ambiguos).
        precio_patterns = [
            "pesos el kilo",
            "pesos kilo",
            "precio del",
            "precio de la",
            "precio de el",
            "precios en",
            "está a",
            "cuesta $",
            "el kilo de",
            "la malla de",
            "el saco de",
            "la caja de",
            "pesos la",
            "pesos el",
        ]
        if any(p in text for p in precio_patterns):
            return "precio"

        # Indicadores de precio mas debiles (solo si no matcheo clima).
        precio_kw = [
            "precio",
            "kilo",
            "saco",
            "malla",
            "caja",
            "pesos",
            "luca",
            "feria",
            "mayorista",
            "lo valledor",
            "la vega",
        ]

        # Indicadores de clima (datos reales).
        clima_patterns = [
            "grados",
            "nublado",
            "despejado",
            "lluvia",
            "viento",
            "humedad",
            "temperatura",
            "pronóstico",
            "pronostico",
            "clima en",
            "tiempo en",
        ]
        if any(p in text for p in clima_patterns):
            return "clima"

        # Fallback: keywords en la consulta original (menos preciso).
        clima_kw = [
            "clima",
            "tiempo",
            "lloviendo",
            "frio",
            "calor",
        ]
        if any(kw in text for kw in clima_kw):
            return "clima"
        if any(kw in text for kw in precio_kw):
            return "precio"

        return "desconocido"

    @staticmethod
    async def _generate_response(transcribed_text: str) -> tuple[str, str]:
        """Genera respuesta textual usando el LLM con Tool Calling.

        Args:
            transcribed_text: Texto transcrito por Whisper.

        Returns:
            Tupla (texto_respuesta, intent).
        """
        from app.services.llm_service import answer

        if not transcribed_text or not transcribed_text.strip():
            return (
                "No entendi tu mensaje. ¿Podrias enviar un audio mas claro?",
                "desconocido",
            )

        try:
            response_text = await answer(transcribed_text.strip())
        except (TimeoutError, RuntimeError, OSError, ValueError):
            logger.exception("Error en generacion LLM — usando fallback")
            response_text = "Tuve un problema al procesar tu consulta. ¿Podrias intentar de nuevo?"

        intent = AgroVozPipeline._detect_intent(transcribed_text, response_text)
        return response_text, intent

    @staticmethod
    def _save_consultation(
        phone_hash: str,
        intent: str,
        query_text: str,
        response_text: str,
        audio_duration_ms: int,
        start_time: float,
        whisper_ms: int = 0,
        llm_ms: int = 0,
        tts_ms: int = 0,
    ) -> None:
        """Guarda la consulta en SQLite para metricas anonimizadas.

        Fire-and-forget: si falla, loguea el error pero no interrumpe
        el pipeline. La consulta se pierde, pero el audio se responde igual.

        Args:
            phone_hash: Hash del numero de telefono.
            intent: "precio", "clima", o "desconocido".
            query_text: Texto transcrito por Whisper.
            response_text: Texto de respuesta del LLM.
            audio_duration_ms: Duracion del audio en ms.
            start_time: time.monotonic() del inicio del pipeline.
        """
        import time as _time

        from app.core.database import SessionLocal
        from app.models.consultation import Consultation

        latency_ms = int((_time.monotonic() - start_time) * 1000)
        try:
            session = SessionLocal()
            try:
                consulta = Consultation(
                    phone_hash=phone_hash,
                    intent=intent,
                    query_text=query_text,
                    response_text=response_text,
                    audio_duration_ms=audio_duration_ms,
                    latency_ms=latency_ms,
                    whisper_ms=whisper_ms,
                    llm_ms=llm_ms,
                    tts_ms=tts_ms,
                )
                session.add(consulta)
                session.commit()
                logger.debug(
                    "Consulta guardada — phone_hash=%s intent=%s latency_ms=%d",
                    phone_hash[:8],
                    intent,
                    latency_ms,
                )
            except SQLAlchemyError:
                session.rollback()
                raise
            finally:
                session.close()
        except SQLAlchemyError:
            logger.exception(
                "Error guardando consulta en DB — phone_hash=%s intent=%s",
                phone_hash[:8],
                intent,
            )

    @staticmethod
    def _is_first_contact(phone_hash: str) -> bool:
        """Retorna True si el phone_hash no tiene consultas previas en DB.

        Consulta SELECT COUNT(*) en consultations WHERE phone_hash = ?.
        Si la consulta falla, retorna False (safe default: no enviar
        bienvenida a todos los mensajes si la DB no responde).

        Args:
            phone_hash: Hash HMAC-SHA256 del numero de telefono.

        Returns:
            True si es el primer contacto (0 consultas previas).
        """
        from sqlalchemy import func, select

        from app.core.database import SessionLocal
        from app.models.consultation import Consultation

        session = SessionLocal()
        try:
            count = session.scalar(
                select(func.count()).select_from(Consultation).where(Consultation.phone_hash == phone_hash)
            )
            return count == 0
        except SQLAlchemyError:
            logger.exception(
                "Error consultando consultas previas — phone_hash=%s",
                phone_hash[:8],
            )
            return False
        finally:
            session.close()

    async def process(
        self,
        wav_path: Path,
        audio_duration_ms: int,
        message_id: str,
        chat_id_hash: str,
        request_id: str,
    ) -> AudioResponse:
        """Ejecuta el pipeline completo: Whisper → LLM → TTS.

        El pipeline tiene timeout de 120s. Si se excede, retorna
        AudioResponse con texto de error predefinido.

        Args:
            wav_path: Path al archivo .wav 16kHz mono listo para Whisper.
            audio_duration_ms: Duracion del audio en milisegundos.
            message_id: ID del mensaje para trazabilidad en logs.
            chat_id_hash: Hash anonimizado del chat para guardar consulta.
            request_id: ID del request para trazabilidad.

        Returns:
            AudioResponse con ruta del audio TTS, texto, latencia e intent.
        """
        pipeline_start = time.monotonic()
        whisper_ms_ref = [0]
        llm_ms_ref = [0]
        tts_ms_ref = [0]
        welcome_ogg_ref: list[str | None] = [None]

        try:
            # Ejecutar pipeline con timeout.
            return await asyncio.wait_for(
                self._process_stages(
                    wav_path=wav_path,
                    audio_duration_ms=audio_duration_ms,
                    message_id=message_id,
                    chat_id_hash=chat_id_hash,
                    request_id=request_id,
                    pipeline_start=pipeline_start,
                    whisper_ms_ref=whisper_ms_ref,
                    llm_ms_ref=llm_ms_ref,
                    tts_ms_ref=tts_ms_ref,
                    welcome_ogg_ref=welcome_ogg_ref,
                ),
                timeout=self._timeout,
            )
        except TimeoutError:
            total_ms = int((time.monotonic() - pipeline_start) * 1000)
            logger.warning(
                "Pipeline timeout (%ss) — message_id=%s total_ms=%d request_id=%s",
                self._timeout,
                message_id,
                total_ms,
                request_id,
            )
            # Si se genero bienvenida antes del timeout, limpiar el archivo
            # para evitar leak en audio_temp/ (el ref se captura antes del cancel).
            if welcome_ogg_ref[0]:
                Path(welcome_ogg_ref[0]).unlink(missing_ok=True)
            return AudioResponse(
                audio_path="",
                text_response=("Tuve problemas para responder a tiempo. ¿Podrias preguntar de nuevo mas breve?"),
                latency_ms=total_ms,
                intent="desconocido",
                whisper_ms=whisper_ms_ref[0],
                llm_ms=llm_ms_ref[0],
                tts_ms=tts_ms_ref[0],
            )

    async def _process_stages(
        self,
        wav_path: Path,
        audio_duration_ms: int,
        message_id: str,
        chat_id_hash: str,
        request_id: str,
        pipeline_start: float,
        whisper_ms_ref: list[int],
        llm_ms_ref: list[int],
        tts_ms_ref: list[int],
        welcome_ogg_ref: list[str | None],
    ) -> AudioResponse:
        """Ejecuta las etapas del pipeline secuencialmente con benchmark.

        Los refs se pasan como listas de 1 elemento para mutarlos
        dentro de la coroutine (Python no permite asignar nonlocal
        en closures anidadas de forma limpia).
        """
        # ── Etapa 0: Onboarding — deteccion de primer contacto (#86) ─
        # Si el phone_hash no tiene consultas previas, se sintetiza un
        # audio de bienvenida (TTS de texto fijo, sin LLM). AudioService
        # lo enviara ANTES de la respuesta normal. Fire-and-forget: si
        # la deteccion o el TTS fallan, el pipeline continua sin bienvenida.
        if chat_id_hash and chat_id_hash != "sin_chat":
            try:
                is_first = await asyncio.to_thread(self._is_first_contact, chat_id_hash)
                if is_first:
                    tts_welcome = _get_tts_service()
                    welcome_ogg_ref[0] = await asyncio.to_thread(tts_welcome.synthesize, _WELCOME_TEXT)
                    logger.info(
                        "Primer contacto detectado — bienvenida generada — phone_hash=%s message_id=%s request_id=%s",
                        chat_id_hash[:8],
                        message_id,
                        request_id,
                    )
            except (SQLAlchemyError, RuntimeError, OSError, ValueError) as exc:
                logger.warning(
                    "Deteccion de primer contacto o TTS de bienvenida fallo — "
                    "continuando sin bienvenida: message_id=%s error=%s request_id=%s",
                    message_id,
                    exc,
                    request_id,
                )

        # ── Etapa 1: Transcripcion Whisper ──────────────────────────
        transcribed_text = ""
        t_whisper_start = time.monotonic()

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
                whisper_ms_ref[0] = int((time.monotonic() - t_whisper_start) * 1000)
                logger.info(
                    "Audio transcrito — message_id=%s text=%.200s chars=%d whisper_ms=%d request_id=%s",
                    message_id,
                    transcribed_text,
                    len(transcribed_text),
                    whisper_ms_ref[0],
                    request_id,
                )
            except (RuntimeError, FileNotFoundError, ValueError, TimeoutError) as exc:
                logger.warning(
                    "Whisper fallo — continuando sin transcripcion: message_id=%s error=%s request_id=%s",
                    message_id,
                    exc,
                    request_id,
                )

        # ── Etapa 2: Generacion LLM + guardar consulta ───────────────
        response_text = ""
        intent = "desconocido"
        t_llm_start = time.monotonic()

        if transcribed_text and transcribed_text.strip():
            try:
                response_text, intent = await self._generate_response(transcribed_text)
                llm_ms_ref[0] = int((time.monotonic() - t_llm_start) * 1000)
                logger.info(
                    "Respuesta LLM generada — message_id=%s intent=%s chars=%d llm_ms=%d request_id=%s",
                    message_id,
                    intent,
                    len(response_text),
                    llm_ms_ref[0],
                    request_id,
                )
            except (TimeoutError, RuntimeError, OSError, ValueError):
                logger.exception(
                    "Error generando respuesta LLM — message_id=%s request_id=%s",
                    message_id,
                    request_id,
                )
                response_text = "Tuve un problema al procesar tu consulta. ¿Podrias intentar de nuevo?"

        # ── Etapa 3: Sintesis TTS ────────────────────────────────────
        response_ogg_path: str = ""
        t_tts_start = time.monotonic()

        if response_text:
            try:
                tts = _get_tts_service()
                response_ogg_path = await asyncio.to_thread(tts.synthesize, response_text)
                tts_ms_ref[0] = int((time.monotonic() - t_tts_start) * 1000)
                logger.info(
                    "TTS sintetizado — message_id=%s tts_ms=%d request_id=%s",
                    message_id,
                    tts_ms_ref[0],
                    request_id,
                )
            except (PiperModelNotFoundError, RuntimeError, ValueError, OSError) as exc:
                logger.warning(
                    "TTS fallo — message_id=%s error=%s request_id=%s",
                    message_id,
                    exc,
                    request_id,
                )

        total_ms = int((time.monotonic() - pipeline_start) * 1000)

        # Guardar consulta en DB para metricas (en thread aparte para no
        # bloquear el event loop con session.commit() sincrono). Se guarda
        # al FINAL del pipeline para persistir el desglose por etapa completo
        # (whisper/llm/tts). Fire-and-forget: si falla, loguea y continua.
        # Se persiste SIEMPRE: incluso si Whisper fallo, se guarda un stub
        # con query_text="" para que _is_first_contact() no retorne True
        # en el siguiente audio (evita bienvenida repetida, fix #105).
        try:
            await asyncio.to_thread(
                self._save_consultation,
                phone_hash=chat_id_hash,
                intent=intent,
                query_text=transcribed_text.strip() if transcribed_text else "",
                response_text=response_text,
                audio_duration_ms=audio_duration_ms,
                start_time=pipeline_start,
                whisper_ms=whisper_ms_ref[0],
                llm_ms=llm_ms_ref[0],
                tts_ms=tts_ms_ref[0],
            )
        except (RuntimeError, OSError, SQLAlchemyError, TypeError, AttributeError, KeyError):
            logger.exception(
                "Error guardando consulta — continuando pipeline: phone_hash=%s intent=%s",
                chat_id_hash[:8],
                intent,
            )

        # Log de benchmark agregado: latencia total + breakdown por etapa.
        logger.info(
            "Pipeline benchmark — message_id=%s total_ms=%d whisper_ms=%d llm_ms=%d tts_ms=%d intent=%s request_id=%s",
            message_id,
            total_ms,
            whisper_ms_ref[0],
            llm_ms_ref[0],
            tts_ms_ref[0],
            intent,
            request_id,
        )

        return AudioResponse(
            audio_path=response_ogg_path,
            text_response=response_text,
            latency_ms=total_ms,
            intent=intent,
            whisper_ms=whisper_ms_ref[0],
            llm_ms=llm_ms_ref[0],
            tts_ms=tts_ms_ref[0],
            welcome_audio_path=welcome_ogg_ref[0],
        )
