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
import re
import time
from decimal import InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.schemas.pipeline import AudioResponse
from app.services.llm_keywords import _COMMON_PRODUCTS
from app.services.llm_service import FALLBACK_TEXT, NO_RESPONSE_TEXT
from app.services.tts_service import PiperModelNotFoundError, TTSService
from app.services.whisper_service import WhisperService

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

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

# Keywords de feedback del agricultor. Se detectan como intent especial
# y actualizan la consulta ANTERIOR (no generan nueva consulta).
_FEEDBACK_UTIL = [
    "me sirvió",
    "me sirve",
    "me sirvio",
    "util",
    "útil",
    "gracias",
    "eso era",
    "eso es",
    "perfecto",
    "bacán",
    "bakan",
    "buena",
    "buenísimo",
    "buenisimo",
    "ok",
    "dale",
]
_FEEDBACK_NO_UTIL = [
    "no me sirvió",
    "no me sirve",
    "no me sirvio",
    "no útil",
    "no util",
    "no entendi",
    "no entendí",
    "no cache",
    "no cacho",
    "mal",
    "malo",
    "pesimo",
    "pésimo",
    "no es eso",
]

# Texto fijo de bienvenida para primer contacto (issue #86).
# No requiere LLM: es un mensaje predefinido sintetizado con TTS.
# Corto (<200 chars) para que el audio dure <10s y no fatigue al agricultor.
_WELCOME_TEXT = (
    "Hola, te doy la bienvenida a AgroVoz. "
    "Soy un asistente de voz que te ayuda a consultar "
    "precios de productos agricolas y el clima. "
    "Solo mandame un audio con tu pregunta y te respondere."
)

# Regex para comandos de alerta proactiva (issue #88).
# Ejemplos:
#   "avisame cuando la papa pase de 10000 pesos"
#   "avisame si viene helada"
#   "avisame si viene lluvia"
#   "cancelar alertas"
_ALERTA_PRECIO_RE = re.compile(
    r"avis[aá]me\s+(?:cuando|si)\s+(?:la|el|los|las)?\s*(\w+)\s+"
    r"(pase\s+de|suba\s+a|suba\s+de|baje\s+a|baje\s+de|"
    r"est[eé]\s+sobre|est[eé]\s+encima\s+de|est[eé]\s+bajo|est[eé]\s+debajo\s+de)"
    r"\s+(\d[\d.]*)\s*(?:pesos)?",
    re.IGNORECASE,
)
_ALERTA_CLIMA_RE = re.compile(
    r"avis[aá]me\s+si\s+viene\s+(helada|lluvia)",
    re.IGNORECASE,
)
_ALERTA_CANCELAR_RE = re.compile(
    r"cancelar\s+(?:mis\s+)?alertas?",
    re.IGNORECASE,
)

# Palabras de accion del agricultor a condicion de comparacion.
_CONDICION_POR_ACCION: dict[str, str] = {
    "pase de": ">",
    "suba a": ">",
    "suba de": ">",
    "baje a": "<",
    "baje de": "<",
    "esté sobre": ">",
    "este sobre": ">",
    "esté encima de": ">",
    "este encima de": ">",
    "esté bajo": "<",
    "este bajo": "<",
    "esté debajo de": "<",
    "este debajo de": "<",
}


def _get_tts_service() -> TTSService:
    """Retorna la instancia singleton de TTSService."""
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service


def _detect_feedback(text: str) -> str | None:
    """Detecta si el texto es feedback del agricultor sobre la consulta anterior.

    Args:
        text: Texto transcrito por Whisper.

    Returns:
        "util", "no_util", o None si no es feedback.
    """
    text_lower = text.lower().strip()
    # Priorizar feedback negativo (más específico).
    for kw in _FEEDBACK_NO_UTIL:
        if kw in text_lower:
            return "no_util"
    for kw in _FEEDBACK_UTIL:
        if kw in text_lower:
            return "util"
    return None


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
    def _should_mark_for_review(
        response_text: str,
        intent: str,
        whisper_ms: int,
        llm_ms: int,
        transcribed_text: str,
    ) -> bool:
        """Determina si una consulta requiere revisión humana.

        Se marca automáticamente cuando:
        - La respuesta del LLM es FALLBACK_TEXT o NO_RESPONSE_TEXT
        - El intent es "desconocido"
        - La transcripción falló (whisper_ms=0 con texto vacío)
        - El LLM falló (llm_ms=0 con texto transcrito válido)

        Args:
            response_text: Texto de respuesta generado por el LLM.
            intent: Intención detectada ("precio", "clima", "desconocido").
            whisper_ms: Latencia de Whisper en ms (0 si falló).
            llm_ms: Latencia del LLM en ms (0 si falló).
            transcribed_text: Texto transcrito por Whisper.

        Returns:
            True si la consulta debe marcarse para revisión.
        """
        # Respuesta de fallback del LLM.
        if response_text in (FALLBACK_TEXT, NO_RESPONSE_TEXT):
            return True

        # Intent no clasificado.
        if intent == "desconocido":
            return True

        # Transcripción falló pero había audio (whisper_ms=0 y texto vacío).
        if whisper_ms == 0 and not transcribed_text.strip():
            return True

        # LLM falló (llm_ms=0) cuando debería haber procesado.
        return bool(llm_ms == 0 and transcribed_text.strip())

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
            "precio", "clima", "resumen", o "desconocido".
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
    def _is_resumen_query(query_text: str) -> bool:
        """Detecta si la consulta es un pedido de resumen por keyword.

        Keywords: "resumen", "mi resumen", "como va el mes",
        "como va mi mes", "resumen del mes".

        Args:
            query_text: Texto transcrito por Whisper.

        Returns:
            True si la consulta pide un resumen de actividad.
        """
        q = query_text.strip().lower()
        resumen_keywords = [
            "resumen",
            "mi resumen",
            "como va el mes",
            "como va mi mes",
            "resumen del mes",
        ]
        return any(kw in q for kw in resumen_keywords)

    @staticmethod
    def _extract_producto(query_text: str) -> str | None:
        """Extrae el nombre de un producto agrícola de la consulta.

        Busca nombres de productos comunes en el texto transcrito.
        Retorna None si no detecta ningún producto.

        Args:
            query_text: Texto transcrito por Whisper.

        Returns:
            Nombre del producto en minúscula, o None.
        """
        q = query_text.strip().lower()
        # Ordenar por largo descendente para que "pimentón" matchee antes
        # que "pimenton" y "sandía" antes que "sandia".
        for product in sorted(_COMMON_PRODUCTS, key=len, reverse=True):
            if product in q:
                return product
        return None

    @staticmethod
    async def _handle_alert_commands(
        transcribed_text: str,
        phone_hash: str,
        wa_chat_id: str | None,
    ) -> tuple[str | None, str | None]:
        """Detecta comandos de alerta proactiva y ejecuta la accion.

        Soporta:
        - "avisame cuando la papa pase de 10000 pesos" -> alerta precio > 10000.
        - "avisame si viene helada" -> alerta clima helada.
        - "avisame si viene lluvia" -> alerta clima lluvia extrema.
        - "cancelar alertas" -> desactiva alertas activas.

        Args:
            transcribed_text: Texto transcrito por Whisper.
            phone_hash: Hash anonimizado del numero.
            wa_chat_id: Chat ID real de WhatsApp para enviar avisos.

        Returns:
            Tupla (texto_respuesta, intent). Si no es comando de alerta,
            retorna (None, None).
        """
        if not transcribed_text or not transcribed_text.strip():
            return None, None

        q = transcribed_text.strip().lower()

        # Cancelar alertas.
        if _ALERTA_CANCELAR_RE.search(q):
            from app.core.database import SessionLocal
            from app.services.alert_service import cancelar_alertas

            session = SessionLocal()
            try:
                count = await cancelar_alertas(session, phone_hash)
                return ("He cancelado tus alertas." if count else "No tienes alertas activas."), "alerta"
            finally:
                session.close()

        # Alerta climatica.
        clima_match = _ALERTA_CLIMA_RE.search(q)
        if clima_match:
            tipo_umbral = clima_match.group(1).lower()
            umbral_clima = "helada" if tipo_umbral == "helada" else "lluvia_extrema"
            from app.core.database import SessionLocal
            from app.services.alert_service import AlertServiceError, create_clima_alert

            session = SessionLocal()
            try:
                mensaje = await create_clima_alert(session, phone_hash, wa_chat_id, umbral_clima)
                return mensaje, "alerta"
            except AlertServiceError as exc:
                return str(exc), "alerta"
            finally:
                session.close()

        # Alerta de precio.
        precio_match = _ALERTA_PRECIO_RE.search(q)
        if precio_match:
            producto_raw = precio_match.group(1).lower()
            accion = precio_match.group(2).lower()
            umbral_str = precio_match.group(3)
            condicion = _CONDICION_POR_ACCION.get(accion)
            if not condicion:
                return None, None

            producto = AgroVozPipeline._extract_producto(transcribed_text)
            if not producto:
                producto = producto_raw

            try:
                from decimal import Decimal

                umbral = Decimal(umbral_str)
            except InvalidOperation:
                return "No entendi el precio. Repite el numero.", "alerta"

            from app.core.database import SessionLocal
            from app.services.alert_service import AlertServiceError, create_price_alert

            session = SessionLocal()
            try:
                mensaje = await create_price_alert(session, phone_hash, wa_chat_id, producto, condicion, umbral)
                return mensaje, "alerta"
            except AlertServiceError as exc:
                return str(exc), "alerta"
            finally:
                session.close()

        return None, None

    @staticmethod
    async def _generate_response(transcribed_text: str, chat_id_hash: str) -> tuple[str, str]:
        """Genera respuesta textual: resumen o LLM con Tool Calling.

        Detecta si la consulta pide un resumen (por keyword). Si es así,
        consulta la DB para generar estadísticas del agricultor. Si no,
        ejecuta el LLM con Tool Calling normal. El hash tambien se usa
        para resolver el mercado mas cercano segun comuna (Issue #89).

        Args:
            transcribed_text: Texto transcrito por Whisper.
            chat_id_hash: Hash anonimizado del chat (resumen y mercado cercano).

        Returns:
            Tupla (texto_respuesta, intent).
        """
        if not transcribed_text or not transcribed_text.strip():
            return (
                "No entendi tu mensaje. ¿Podrias enviar un audio mas claro?",
                "desconocido",
            )

        # Detectar "resumen" por keyword ANTES del LLM: es mas rapido y determinista.
        if AgroVozPipeline._is_resumen_query(transcribed_text):
            try:
                # Import local para evitar ciclo con summary_service
                from app.core.database import SessionLocal
                from app.services.summary_service import get_consultation_summary

                session = SessionLocal()
                try:
                    response_text = await asyncio.to_thread(get_consultation_summary, session, chat_id_hash)
                finally:
                    session.close()
                return response_text, "resumen"
            except (TimeoutError, RuntimeError, OSError, ValueError):
                logger.exception("Error generando resumen")
                return (
                    "Tuve un problema al generar tu resumen. ¿Podrias intentar de nuevo?",
                    "resumen",
                )

        # Pipeline normal: LLM con tool calling.
        # Import local para permitir mocking en tests
        from app.services.llm_service import answer

        try:
            response_text = await answer(transcribed_text.strip(), phone_hash=chat_id_hash)
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
        producto: str | None = None,
        requires_review: bool = False,
    ) -> None:
        """Guarda la consulta en SQLite para metricas anonimizadas.

        Fire-and-forget: si falla, loguea el error pero no interrumpe
        el pipeline. La consulta se pierde, pero el audio se responde igual.

        Args:
            phone_hash: Hash del numero de telefono.
            intent: "precio", "clima", "resumen", o "desconocido".
            query_text: Texto transcrito por Whisper.
            response_text: Texto de respuesta del LLM.
            audio_duration_ms: Duracion del audio en ms.
            start_time: time.monotonic() del inicio del pipeline.
            whisper_ms: Latencia de Whisper en ms.
            llm_ms: Latencia del LLM en ms.
            tts_ms: Latencia del TTS en ms.
            producto: Producto detectado en la consulta (opcional).
            requires_review: Si la consulta debe marcarse para revisión humana.
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
                    producto=producto,
                    query_text=query_text,
                    response_text=response_text,
                    audio_duration_ms=audio_duration_ms,
                    latency_ms=latency_ms,
                    whisper_ms=whisper_ms,
                    llm_ms=llm_ms,
                    tts_ms=tts_ms,
                    requires_review=requires_review,
                )
                session.add(consulta)
                session.commit()
                logger.debug(
                    "Consulta guardada — phone_hash=%s intent=%s producto=%s latency_ms=%d",
                    phone_hash[:8],
                    intent,
                    producto,
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
    def _update_previous_feedback(
        phone_hash: str,
        feedback: str,
        session: Session | None = None,
    ) -> bool:
        """Actualiza el feedback de la ultima consulta del phone_hash.

        Busca la consulta más reciente del mismo phone_hash y actualiza
        su campo feedback. Si no hay consulta previa, retorna False.

        Args:
            phone_hash: Hash del numero de telefono.
            feedback: "util" o "no_util".
            session: Sesión de SQLAlchemy opcional. Si no se provee, crea una nueva.

        Returns:
            True si se actualizó una consulta, False si no había previa.
        """
        from app.core.database import SessionLocal
        from app.models.consultation import Consultation

        own_session = session is None
        if session is None:
            session = SessionLocal()
        try:
            # Buscar la última consulta del mismo phone_hash.
            # Ordenar por id DESC (más confiable que created_at con
            # server_default que puede tener el mismo timestamp para
            # filas insertadas en la misma transacción).
            stmt = (
                select(Consultation)
                .where(Consultation.phone_hash == phone_hash)
                .order_by(Consultation.id.desc())
                .limit(1)
            )
            consulta = session.scalars(stmt).first()
            if consulta is None:
                logger.warning(
                    "Feedback sin consulta previa — phone_hash=%s feedback=%s",
                    phone_hash[:8],
                    feedback,
                )
                return False

            consulta.feedback = feedback
            session.commit()
            logger.info(
                "Feedback actualizado — consultation_id=%d phone_hash=%s feedback=%s",
                consulta.id,
                phone_hash[:8],
                feedback,
            )
            return True
        except SQLAlchemyError:
            session.rollback()
            logger.exception(
                "Error actualizando feedback — phone_hash=%s feedback=%s",
                phone_hash[:8],
                feedback,
            )
            return False
        finally:
            if own_session:
                session.close()

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
        chat_id: str | None = None,
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
            chat_id: Chat ID real de WhatsApp (opcional, para alertas proactivas).

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
                    chat_id=chat_id,
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
        chat_id: str | None,
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
        #
        # TRADE-OFF ACEPTADO: la deteccion de primer contacto es racy bajo
        # concurrencia. Si dos audios del mismo numero llegan simultaneamente,
        # ambos pueden ver count=0 y generar dos bienvenidas (race between
        # SELECT COUNT y INSERT). El stub con query_text="" en _save_consultation
        # solo previene repeticion en el SIGUIENTE request. Aceptado para piloto
        # MVP de 3-5 productores; post-MVP considerar flag de bienvenida_enviada
        # con unique constraint para atomicidad.
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

        # ── Etapa 2: Generacion de respuesta (feedback, resumen o LLM) ───
        response_text = ""
        intent = "desconocido"
        producto: str | None = None
        t_llm_start = time.monotonic()

        if transcribed_text and transcribed_text.strip():
            # Comandos de alerta proactiva (issue #88). Se evaluan antes
            # del feedback y del LLM para ser deterministas y rapidos.
            alert_response, alert_intent = await self._handle_alert_commands(transcribed_text, chat_id_hash, chat_id)
            if alert_response is not None:
                response_text = alert_response
                intent = alert_intent or "alerta"
                llm_ms_ref[0] = int((time.monotonic() - t_llm_start) * 1000)
                # Extraer producto para metricas si es alerta de precio.
                producto = self._extract_producto(transcribed_text)
                logger.info(
                    "Comando de alerta procesado — message_id=%s intent=%s request_id=%s",
                    message_id,
                    intent,
                    request_id,
                )
            else:
                # Detectar si el mensaje es feedback del agricultor. El feedback
                # actualiza la consulta ANTERIOR (no crea una nueva) y responde
                # con un TTS corto sin pasar por el LLM.
                feedback = _detect_feedback(transcribed_text)
                if feedback is not None:
                    intent = "feedback"
                    updated = await asyncio.to_thread(
                        self._update_previous_feedback,
                        phone_hash=chat_id_hash,
                        feedback=feedback,
                    )
                    if updated:
                        response_text = (
                            "Me alegra haberte ayudado." if feedback == "util" else "Gracias, lo tendré en cuenta."
                        )
                        logger.info(
                            "Feedback procesado — message_id=%s feedback=%s request_id=%s",
                            message_id,
                            feedback,
                            request_id,
                        )
                    else:
                        response_text = "Gracias por tu respuesta."
                        logger.warning(
                            "Feedback sin consulta previa — message_id=%s feedback=%s request_id=%s",
                            message_id,
                            feedback,
                            request_id,
                        )
                    llm_ms_ref[0] = int((time.monotonic() - t_llm_start) * 1000)
                else:
                    # Extraer producto antes de generar respuesta (para guardar en consulta).
                    producto = self._extract_producto(transcribed_text)

                    # _generate_response detecta internally si es resumen o LLM,
                    # maneja su propia lógica y error handling.
                    response_text, intent = await self._generate_response(transcribed_text, chat_id_hash)
                    llm_ms_ref[0] = int((time.monotonic() - t_llm_start) * 1000)

                    if intent == "resumen":
                        logger.info(
                            "Resumen generado — message_id=%s chars=%d llm_ms=%d request_id=%s",
                            message_id,
                            len(response_text),
                            llm_ms_ref[0],
                            request_id,
                        )
                    else:
                        logger.info(
                            "Respuesta LLM generada — message_id=%s intent=%s chars=%d llm_ms=%d request_id=%s",
                            message_id,
                            intent,
                            len(response_text),
                            llm_ms_ref[0],
                            request_id,
                        )

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
        # Se persiste SIEMPRE salvo feedback: incluso si Whisper fallo, se
        # guarda un stub con query_text="" para que _is_first_contact() no
        # retorne True en el siguiente audio (evita bienvenida repetida, #105).
        # El feedback NO crea consulta nueva: actualiza la anterior (#97), y
        # como siempre tiene transcripcion valida no necesita el stub.
        if intent != "feedback":
            # Determinar si esta consulta requiere revision humana (issue #99);
            # una transcripcion fallida tambien queda marcada para revision.
            mark_review = self._should_mark_for_review(
                response_text=response_text,
                intent=intent,
                whisper_ms=whisper_ms_ref[0],
                llm_ms=llm_ms_ref[0],
                transcribed_text=transcribed_text or "",
            )
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
                    producto=producto,
                    requires_review=mark_review,
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
