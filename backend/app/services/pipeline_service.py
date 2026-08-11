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
import unicodedata
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, cast

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.constants import FeedbackAgricultor, Intent
from app.core.phone_hash import validate_phone_hash
from app.schemas.pipeline import AudioResponse
from app.services.alert_pipeline import detect_and_handle_alert_command
from app.services.conversation_state import (
    ConversationLease,
    ConversationRegistry,
    ConversationState,
    TransitionStatus,
)
from app.services.dataset_service import retain_audio
from app.services.llm_keywords import _COMMON_PRODUCTS, _contains_product_keyword
from app.services.llm_service import FALLBACK_TEXT, NO_RESPONSE_TEXT
from app.services.tts_service import PiperModelNotFoundError, TTSService
from app.services.whisper_service import WhisperService

if TYPE_CHECKING:
    from app.schemas.variables import ExtractedVariables, TipoConsulta

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Keywords para inferir el tipo de consulta en _extract_variables (issue #191).
# Extracción keyword-based (no LLM) para no sumar latencia al pipeline (#165).
_PRECIO_KEYWORDS = frozenset(
    {
        "precio",
        "cuanto",
        "cuánto",
        "como",
        "cómo",
        "cuesta",
        "vale",
        "kilo",
        "saco",
        "luca",
        "peso",
        "vender",
        "vendi",
        "vendí",
        "comprar",
    }
)
_CLIMA_KEYWORDS = frozenset(
    {
        "clima",
        "tiempo",
        "lluvia",
        "llover",
        "temperatura",
        "frio",
        "frío",
        "calor",
        "helada",
        "viento",
        "pronostico",
        "pronóstico",
        "grados",
    }
)
_CALENDARIO_KEYWORDS = frozenset(
    {
        "calendario",
        "siembra",
        "siembro",
        "sembrar",
        "sembré",
        "sembre",
        "plantar",
        "planto",
        "plantación",
        "plantacion",
        "cosecha",
        "cosechar",
        "cosecho",
        "coseché",
        "coseche",
        "cuando saco",
    }
)
_AGRONOMICA_KEYWORDS = _CALENDARIO_KEYWORDS | frozenset(
    {
        "rotación",
        "rotacion",
        "rotar",
        "va antes",
        "síntoma",
        "sintoma",
        "problema de mi cultivo",
        "manchas",
        "plaga",
        "tizón",
        "tizon",
        "enfermedad",
    }
)

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

# Timeout para transcripcion Whisper (segundos).
# 60s da margen al cold-load del modelo (~32s en CPU ARM64 en VPS CX43
# para Whisper small 242M params). Solo el primer request paga cold-load;
# requests posteriores completan en <5s.
_WHISPER_TIMEOUT = 60.0

# Cache singleton de TTSService: el modelo Piper se carga UNA vez.
_tts_service: TTSService | None = None

# Registro operacional efímero. Se crea solo cuando el feature flag está
# activo; con el gate apagado el pipeline conserva su flujo stateless.
_conversation_registry: ConversationRegistry | None = None
_conversation_registry_lock = Lock()

_CONVERSATION_BUSY_TEXT = "Ya tienes una consulta en proceso. Espera un momento antes de enviar otra."
_COMPREHENSION_ESCALATION_TEXTS = (
    "No pude entender bien. Por favor, repite tu consulta más despacio y con una pregunta a la vez.",
    "Todavía no pude entender. Si puedes, escribe tu consulta por texto en este mismo chat.",
    (
        "Sigo sin poder entender tu consulta. AgroVoz no cuenta con atención humana en este chat. "
        "Para recibir ayuda de una persona, puedes contactar directamente a tu extensionista de "
        "PRODESAL o a INDAP."
    ),
)

# Solo estas intenciones pueden necesitar contenido transitorio para copiarlo
# al historial consentido después de confirmar la entrega por WhatsApp.
_HISTORY_STAGING_INTENTS = frozenset({"precio", "clima", "credito", "corpus"})

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

# Pedidos cerrados de contexto. La detección exige que toda la frase, salvo
# fórmulas breves de cortesía, corresponda al pedido: nunca se interpreta un
# "lo mismo" aislado ni se intenta resolver referencias ambiguas con el LLM.
_EXPLICIT_HISTORY_QUERIES = frozenset(
    {
        "cual fue mi ultima consulta",
        "cual fue mi consulta anterior",
        "que pregunte antes",
        "que fue lo que pregunte antes",
        "consulta anterior",
        "la consulta anterior",
        "mi consulta anterior",
    }
)
_HISTORY_QUERY_PREFIXES = (
    "por favor ",
    "dime ",
    "muestrame ",
    "recuerdame ",
    "quiero saber ",
)
_HISTORY_DELETION_QUERIES = frozenset(
    {
        "borra mi historial",
        "borra todo mi historial",
        "elimina mi historial",
        "elimina todo mi historial",
        "borra mis consultas",
        "elimina mis consultas",
        "desactiva mi historial",
    }
)
_LOCATION_DELETION_QUERIES = frozenset(
    {
        "borra mi ubicacion",
        "elimina mi ubicacion",
        "olvida mi ubicacion",
        "borra el gps de mi parcela",
        "elimina el gps de mi parcela",
        "deja de guardar mi ubicacion",
    }
)

# Segundo límite, menor al del servicio de historial, para que una respuesta
# hablada no se vuelva interminable aunque la fila persistida sea extensa.
_HISTORY_VOICE_QUERY_MAX_CHARS = 160
_HISTORY_VOICE_RESPONSE_MAX_CHARS = 320

# Texto fijo de bienvenida para primer contacto (issue #86).
# No requiere LLM: es un mensaje predefinido sintetizado con TTS.
# Sin tildes a proposito: Piper las verbaliza mal en algunas palabras.
# Se mencionan las DOS vias de entrada (audio y texto) y se aclara de entrada
# que entregamos datos y no recomendaciones, que es la limitacion de alcance
# que el productor firma en el acuerdo de consentimiento (docs/piloto/06).
_WELCOME_TEXT = (
    "Hola, te doy la bienvenida a AgroVoz. "
    "Soy un asistente que te ayuda a consultar "
    "precios de productos agricolas y el clima. "
    "Mandame un audio con tu pregunta, o escribimela si prefieres. "
    "Te entrego datos oficiales, no consejos: la decision siempre es tuya."
)

# Aviso de responsabilidad que se envia como TEXTO en el primer contacto.
# Va aparte del audio de bienvenida a proposito: un descargo legal leido en voz
# alta es inusable, y en texto el productor puede releerlo o mostrarselo a alguien.
# Requisito legal previo al piloto — ver docs/legal/aviso-responsabilidad.md.
WELCOME_DISCLAIMER_TEXT = (
    "⚠️ Antes de empezar, algo importante:\n\n"
    "• AgroVoz te entrega precios de ODEPA y clima de Open-Meteo. "
    "Son datos oficiales, pero son informacion, NO una recomendacion.\n"
    "• Los precios de ODEPA son de mercados mayoristas (como Lo Valledor en Santiago). "
    "En tu predio te van a ofrecer menos, porque incluye transporte e intermediacion. "
    "El dato te sirve para saber cual es el piso del mercado al negociar.\n"
    "• El dato puede tener horas de antiguedad. Verifica siempre con tu comprador.\n"
    "• AgroVoz no se hace responsable de las decisiones de venta que tomes.\n\n"
    "Para asesoria tecnica habla con tu extensionista de PRODESAL o con INDAP."
)


def _get_tts_service() -> TTSService:
    """Retorna la instancia singleton de TTSService."""
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service


def _get_conversation_registry() -> ConversationRegistry | None:
    """Obtiene el registro lazy solo cuando el feature flag está activo."""
    if not settings.use_conversation_state:
        return None

    global _conversation_registry
    with _conversation_registry_lock:
        if _conversation_registry is None:
            _conversation_registry = ConversationRegistry(
                timeout_minutes=settings.conversation_timeout_minutes,
            )
        return _conversation_registry


def _claim_conversation(
    phone_hash: str,
) -> tuple[ConversationLease | None, bool]:
    """Reclama un turno válido sin crear tracking para identidades inválidas."""
    if not settings.use_conversation_state:
        return None, False
    if not validate_phone_hash(phone_hash):
        logger.debug("State machine omitida: identidad operacional no es HMAC-SHA256")
        return None, False

    registry = _get_conversation_registry()
    if registry is None:
        return None, False

    claim = registry.claim(phone_hash)
    if claim.status == TransitionStatus.OCUPADA:
        return None, True
    return claim.lease, False


def _detect_feedback(text: str) -> FeedbackAgricultor | None:
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


def _truncate_for_voice(text: str, max_chars: int) -> str:
    """Compacta y recorta texto en un límite estable, idealmente entre palabras."""
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact

    fragment = compact[: max_chars - 1].rstrip()
    last_space = fragment.rfind(" ")
    if last_space >= max_chars // 2:
        fragment = fragment[:last_space].rstrip()
    return f"{fragment}…"


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
        intent: Intent,
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
    def _detect_intent(query_text: str, llm_response: str) -> Intent:
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

        # Calendario y reglas agronómicas tienen prioridad sobre palabras como
        # "precio" que pueden aparecer al citar una consulta compuesta. La
        # respuesta determinista ya validó la fuente antes de llegar aquí.
        calendario_patterns = [
            "siembra",
            "siembro",
            "sembrar",
            "plantar",
            "plantación",
            "plantacion",
            "cosecha",
            "cosechar",
            "rotación",
            "rotacion",
            "fuente publicada el",
        ]
        if any(pattern in text for pattern in calendario_patterns):
            return "agronomica"

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

        # Indicadores de RAG/corpus (boletines, documentos, contexto).
        # "segun" solo no basta porque "segun OpenMeteo" es clima.
        corpus_patterns = [
            "boletin",
            "boletín",
            "odepa dice",
            "fuente: odepa",
            "segun el boletin",
            "segun odepa el",
            "documento oficial",
            "corpus odepa",
        ]
        if any(p in text for p in corpus_patterns):
            return "corpus"

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
    def _is_reporte_pdf_query(query_text: str) -> bool:
        """Detecta pedidos explícitos de un documento semanal.

        La detección ocurre antes del resumen hablado para que frases como
        ``mándame un resumen de la semana`` generen un archivo, mientras que
        el comando corto ``resumen`` conserva su comportamiento histórico.
        """
        decomposed = unicodedata.normalize("NFD", query_text.casefold())
        normalized = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
        normalized = " ".join("".join(char if char.isalnum() else " " for char in normalized).split())

        if any(phrase in normalized for phrase in ("no quiero", "no necesito", "no me mandes", "sin reporte")):
            return False
        if any(keyword in normalized for keyword in ("pdf", "reporte", "informe")):
            return True
        if "resumen" not in normalized:
            return False
        return any(keyword in normalized for keyword in ("semana", "precios y clima", "precio y clima"))

    @staticmethod
    async def _generate_report_response(phone_hash: str) -> tuple[str, str | None]:
        """Genera el archivo PDF y devuelve el texto corto para WhatsApp."""
        from app.services.report_service import ReportGenerationError, generate_weekly_report

        try:
            report_path = await generate_weekly_report(phone_hash)
        except ReportGenerationError:
            logger.warning("Reporte PDF no disponible — identidad seudonimizada")
            if settings.pdf_reports_enabled:
                return "No pude generar el reporte ahora. ¿Probamos de nuevo más tarde?", None
            return "Los reportes PDF todavía no están habilitados.", None
        return "Listo, te envío el reporte semanal en PDF.", str(report_path)

    @staticmethod
    def _is_explicit_history_query(query_text: str) -> bool:
        """Detecta únicamente pedidos inequívocos de la consulta anterior."""
        decomposed = unicodedata.normalize("NFD", query_text.casefold())
        without_accents = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
        normalized = " ".join("".join(char if char.isalnum() else " " for char in without_accents).split())

        # Se aceptan fórmulas de cortesía encadenadas, pero no texto adicional.
        prefix_removed = True
        while prefix_removed:
            prefix_removed = False
            for prefix in _HISTORY_QUERY_PREFIXES:
                if normalized.startswith(prefix):
                    normalized = normalized.removeprefix(prefix).strip()
                    prefix_removed = True
                    break
        normalized = normalized.removesuffix(" por favor").strip()
        return normalized in _EXPLICIT_HISTORY_QUERIES

    @staticmethod
    def _is_history_deletion_query(query_text: str) -> bool:
        """Detecta solo órdenes inequívocas de revocar y borrar historial."""
        decomposed = unicodedata.normalize("NFD", query_text.casefold())
        without_accents = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
        normalized = " ".join("".join(char if char.isalnum() else " " for char in without_accents).split())
        normalized = normalized.removeprefix("por favor ").strip()
        normalized = normalized.removesuffix(" por favor").strip()
        return normalized in _HISTORY_DELETION_QUERIES

    @staticmethod
    def _is_location_deletion_query(query_text: str) -> bool:
        """Detecta órdenes inequívocas de revocar las coordenadas GPS."""
        decomposed = unicodedata.normalize("NFD", query_text.casefold())
        without_accents = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
        normalized = " ".join("".join(char if char.isalnum() else " " for char in without_accents).split())
        normalized = normalized.removeprefix("por favor ").strip()
        normalized = normalized.removesuffix(" por favor").strip()
        return normalized in _LOCATION_DELETION_QUERIES

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
        # Priorizar nombres compuestos y exigir límites de palabra. El
        # substring simple confundía, por ejemplo, "papaya" con "papa".
        for product in sorted(_COMMON_PRODUCTS, key=len, reverse=True):
            if _contains_product_keyword(q, product):
                return product
        return None

    @staticmethod
    def _extract_comuna(query_text: str) -> str:
        """Extrae una comuna explícita sin inventar una ubicación.

        El piloto solo tiene una equivalencia verificada para Traiguén. Si la
        consulta nombra otra comuna, se conserva el texto para que el motor de
        calendario falle cerrado en vez de responder con la comuna por defecto.
        """
        decomposed = unicodedata.normalize("NFKD", query_text.casefold())
        normalized = "".join(char for char in decomposed if not unicodedata.combining(char))
        if "traiguen" in normalized:
            return "Traiguén"

        marker = " en "
        if marker not in normalized:
            return "Traiguén"
        candidate = normalized.split(marker, 1)[1]
        for stop in (" para ", " y ", " cuando ", " que ", " con "):
            candidate = candidate.split(stop, 1)[0]
        candidate = candidate.strip(" ?!.,;:")
        return candidate or "Traiguén"

    @staticmethod
    def _extract_variables(query_text: str) -> ExtractedVariables:
        """Extrae variables tipadas de la consulta por keyword matching (issue #191).

        Gate de validación temprana ANTES del tool calling: detecta producto y
        tipo de consulta (precio / clima / agronómica / ambos / desconocido) con
        keywords.

        Decisión de diseño: la extracción es keyword-based, NO vía LLM. Agregar
        una llamada LLM de extracción empeoraría la latencia crítica del
        pipeline (#165, ~46s solo-LLM en CPU). Además el tool calling nativo ya
        extrae los parámetros finos (producto, mercado) al invocar las tools;
        este paso solo aporta un contrato tipado barato y determinista para el
        pipeline y la auditoría de qué consultan los agricultores.

        Args:
            query_text: Texto transcrito por Whisper.

        Returns:
            ExtractedVariables con producto y consulta_tipo detectados.
        """
        from app.schemas.variables import ExtractedVariables

        producto = AgroVozPipeline._extract_producto(query_text)
        q = query_text.lower()
        tiene_agronomica = any(kw in q for kw in _AGRONOMICA_KEYWORDS)
        tiene_precio = any(kw in q for kw in _PRECIO_KEYWORDS if kw not in {"como", "cómo"})
        tiene_clima = any(kw in q for kw in _CLIMA_KEYWORDS)
        # "¿a cómo está la papa?" es una forma válida de preguntar precio,
        # pero "¿cómo está el clima?" no debe convertirse en consulta mixta.
        if not tiene_precio and producto is not None and not tiene_clima:
            tiene_precio = "como" in q or "cómo" in q

        consulta_tipo: TipoConsulta
        # La regla agronómica tiene precedencia solo cuando está habilitada o
        # cuando la consulta es exclusivamente agronómica. Con el gate
        # apagado, una palabra como "cosecha" no puede tapar un precio o el
        # clima que el agricultor sí pidió explícitamente.
        if tiene_agronomica and (
            settings.agronomic_rules_enabled or not (tiene_precio or tiene_clima)
        ):
            consulta_tipo = "agronomica"
        elif tiene_precio and tiene_clima:
            consulta_tipo = "ambos"
        elif tiene_precio:
            consulta_tipo = "precio"
        elif tiene_clima:
            consulta_tipo = "clima"
        else:
            consulta_tipo = "desconocido"

        return ExtractedVariables(producto=producto, consulta_tipo=consulta_tipo)

    @staticmethod
    async def _handle_alert_commands(
        transcribed_text: str,
        phone_hash: str,
        wa_chat_id: str | None,
    ) -> tuple[str | None, str | None]:
        """Delegador a la pipeline de comandos de alerta (issue #88).

        Ver app.services.alert_pipeline.detect_and_handle_alert_command.
        """
        return await detect_and_handle_alert_command(transcribed_text, phone_hash, wa_chat_id)

    # Marcas de consulta compuesta o conversacional. Si aparecen, el fast-path
    # no aplica: son casos donde el productor pregunta dos cosas, compara, o
    # pide una explicacion, y ahi el LLM si aporta.
    _FAST_PATH_BLOQUEANTES = (
        " y ademas",
        " y además",
        " tambien",
        " también",
        " o sea",
        " por que",
        " por qué",
        " porque",
        " conviene",
        " me sirve",
        " comparado",
        " diferencia",
        " deberia",
        " debería",
        " recomend",
    )

    # Tope de largo para el fast-path. Una consulta larga suele traer contexto o
    # varias preguntas; el atajo esta pensado para la pregunta directa y corta.
    _FAST_PATH_MAX_CHARS = 120

    @staticmethod
    def _puede_usar_fast_path(
        texto: str,
        extracted: ExtractedVariables,
        system_tip: str | None,
    ) -> bool:
        """Decide si la consulta puede responderse sin invocar al LLM.

        Solo con certeza total: tipo clasificado como precio o clima, producto
        identificado si es precio, consulta corta, sin marcas de pregunta
        compuesta y sin el tip de margen (que necesita razonamiento del LLM).

        Los mercados nombrados SI usan fast-path: ``_force_keyword_tool``
        extrae el alias ("vega central", "valledor") y ``get_price_for_llm``
        lo resuelve por substring. Antes se bloqueaban y caian al LLM (~25s).

        Args:
            texto: Consulta transcrita del agricultor.
            extracted: Variables tipadas detectadas por ``_extract_variables``.
            system_tip: Tip de margen si se detecto venta realizada.

        Returns:
            True si conviene el atajo determinista.
        """
        if system_tip is not None:
            return False
        if extracted.consulta_tipo not in ("precio", "clima"):
            return False
        if extracted.consulta_tipo == "precio" and not extracted.producto:
            return False

        limpio = texto.strip().lower()
        if len(limpio) > AgroVozPipeline._FAST_PATH_MAX_CHARS:
            return False

        # Se normalizan los signos de apertura y se rodea de espacios para que
        # las marcas (que llevan espacio inicial, para no matchear dentro de otra
        # palabra) tambien peguen al principio de la frase: "¿por que ..." debe
        # detectarse igual que "... por que ...".
        normalizado = f" {limpio.replace('¿', ' ').replace('¡', ' ')} "
        return all(marca not in normalizado for marca in AgroVozPipeline._FAST_PATH_BLOQUEANTES)

    @staticmethod
    def _load_user_cultivos(phone_hash: str) -> list[str] | None:
        """Carga los cultivos de interés de un productor desde user_prefs.

        Args:
            phone_hash: Hash HMAC-SHA256 del número de teléfono.

        Returns:
            Lista de cultivos (ej: ["papa", "trigo"]) o None si no tiene.
        """
        import json

        from app.core.database import SessionLocal
        from app.models.user_prefs import UserPrefs

        session = SessionLocal()
        try:
            prefs = session.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
            if prefs is None or not prefs.cultivos:
                return None
            parsed = json.loads(prefs.cultivos)
            if isinstance(parsed, list) and parsed:
                return [str(c) for c in parsed]
            return None
        except (SQLAlchemyError, json.JSONDecodeError, TypeError, ValueError):
            logger.error("Error cargando cultivos de user_prefs")
            return None
        finally:
            session.close()

    @staticmethod
    async def _generate_response(
        transcribed_text: str,
        chat_id_hash: str,
        origen_ref: list[str] | None = None,
    ) -> tuple[str, Intent]:
        """Genera respuesta textual: historial, saludo, resumen o Tool Calling.

        Detecta en orden (por eficiencia):
        1. Revocación/borrado explícito de historial.
        2. Pedido explícito de consulta anterior → historial consentido.
        3. Crédito o saludo puro → respuesta rápida, sin LLM.
        4. Resumen (por keyword) → estadísticas del agricultor.
        5. Consulta normal → LLM con Tool Calling.

        El hash también se usa para resolver el mercado más cercano según
        comuna (Issue #89). Los cultivos de interés del agricultor se cargan
        de user_prefs para personalizar el contexto del LLM (Issue #125).

        Args:
            transcribed_text: Texto transcrito por Whisper.
            chat_id_hash: Hash anonimizado del chat (resumen y mercado cercano).
            origen_ref: Lista de un elemento donde se deja COMO se genero la
                respuesta ("historial", "saludo", "resumen", "fast_path", "llm",
                "fallback_keywords", "openrouter", "generico"). El caller lo usa
                para loguear la verdad: antes el log decia "Respuesta LLM
                generada" tambien cuando respondio el fast-path sin tocar el
                LLM, y eso hacia imposible distinguir la latencia real del LLM
                de la del atajo. Opcional para no romper los llamadores de tests.

        Returns:
            Tupla (texto_respuesta, intent).
        """

        def _marcar(origen: str) -> None:
            """Registra el origen de la respuesta si el caller lo pidio."""
            if origen_ref is not None:
                origen_ref[0] = origen

        if not transcribed_text or not transcribed_text.strip():
            _marcar("sin_texto")
            return (
                "No entendi tu mensaje. ¿Podrias enviar un audio mas claro?",
                "desconocido",
            )

        if AgroVozPipeline._is_location_deletion_query(transcribed_text):
            from app.services.location_service import clear_user_location

            try:
                await asyncio.to_thread(clear_user_location, chat_id_hash)
            except (SQLAlchemyError, OSError, RuntimeError, ValueError):
                logger.error("Revocación de ubicación GPS no confirmada")
                _marcar("ubicacion_borrada_error")
                return (
                    "No pude confirmar el borrado de tu ubicación. Inténtalo nuevamente en unos minutos.",
                    "clima",
                )

            logger.info("Ubicación GPS revocada desde WhatsApp")
            _marcar("ubicacion_borrada")
            return (
                "Listo. Eliminé la ubicación GPS guardada de tu parcela.",
                "clima",
            )

        if AgroVozPipeline._is_history_deletion_query(transcribed_text):
            from app.services.consultation_history_service import (
                HistoryOperationError,
                delete_history,
            )

            try:
                await asyncio.to_thread(
                    delete_history,
                    chat_id_hash,
                    reason="consent_revoked",
                    requested_via="verified_whatsapp",
                )
            except HistoryOperationError:
                logger.error("Borrado de historial solicitado por WhatsApp no confirmado")
                _marcar("historial_borrado_error")
                return (
                    "No pude confirmar el borrado de tu historial. Inténtalo nuevamente en unos minutos.",
                    "resumen",
                )

            logger.info("Historial revocado y borrado desde WhatsApp")
            _marcar("historial_borrado")
            return (
                "Listo. Eliminé tu historial y desactivé el guardado de consultas futuras.",
                "resumen",
            )

        # El historial solo se consulta ante una frase explícita. El servicio
        # aplica feature gate, consentimiento y aislamiento por phone_hash. Si
        # no entrega contexto, este bloque es invisible y el flujo stateless
        # continúa exactamente por crédito, saludo, resumen o LLM.
        if AgroVozPipeline._is_explicit_history_query(transcribed_text):
            from app.services.consultation_history_service import (
                get_latest_consultation_context,
            )

            try:
                context = await asyncio.to_thread(
                    get_latest_consultation_context,
                    chat_id_hash,
                )
            except (SQLAlchemyError, TimeoutError, RuntimeError, OSError, ValueError):
                logger.warning("Lectura contextual no disponible — continuando sin historial")
                context = None

            if context is not None:
                previous_query = _truncate_for_voice(
                    context.query_text,
                    _HISTORY_VOICE_QUERY_MAX_CHARS,
                )
                previous_response = _truncate_for_voice(
                    context.response_text,
                    _HISTORY_VOICE_RESPONSE_MAX_CHARS,
                )
                logger.info("Respuesta contextual generada sin LLM")
                _marcar("historial")
                return (
                    f"Tu consulta anterior fue: {previous_query}. La respuesta que recibiste fue: {previous_response}.",
                    "resumen",
                )

        # P0 #174/#245: crédito y programas se detectan ANTES de saludo,
        # extracción de producto, fuzzy matching, RAG y LLM. Sin esta
        # precedencia, "programa para un motocultivador" podía caer en clima
        # o en precio por similitudes del texto transcrito.
        from app.services.indap_credit_service import (
            get_indap_credit_referral,
            get_programas_indap,
            is_programas_indap_query,
        )

        if is_programas_indap_query(transcribed_text):
            logger.info("Derivación informativa INDAP de programas sin LLM")
            _marcar("fast_path_programas_indap")
            return get_programas_indap(transcribed_text), "credito"

        credit_referral = get_indap_credit_referral(transcribed_text)
        if credit_referral is not None:
            logger.info("Derivación informativa INDAP sin LLM")
            _marcar("fast_path_credito")
            return credit_referral, "credito"

        # 0. Detectar saludos puros ANTES del LLM: fast-path determinista sin latencia.
        # Saludos simples ("hola", "buenos días") sin pregunta real no requieren LLM.
        from app.services.llm_keywords import _detect_greeting

        if _detect_greeting(transcribed_text):
            logger.info(
                "Saludo puro detectado — omitiendo LLM — chars=%d",
                len(transcribed_text),
            )
            _marcar("saludo")
            return (
                "¡Hola! Preguntame por el precio de algún producto o por el clima de Traiguén.",
                "saludo",
            )

        # 0.5. Extraer variables tipadas de la consulta (issue #191).
        # Gate tipado temprano + trazabilidad de qué consultan los agricultores.
        # El tool calling nativo extrae los parámetros finos; esto registra
        # producto y tipo detectados para auditoría, sin costo de latencia LLM.
        extracted = AgroVozPipeline._extract_variables(transcribed_text)
        logger.debug(
            "Variables extraídas — producto=%s consulta_tipo=%s",
            extracted.producto,
            extracted.consulta_tipo,
        )

        # 1. Detectar "resumen" por keyword ANTES del LLM: es mas rapido y determinista.
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
                _marcar("resumen")
                return response_text, "resumen"
            except (TimeoutError, RuntimeError, OSError, ValueError):
                logger.error("Error generando resumen")
                _marcar("resumen_error")
                return (
                    "Tuve un problema al generar tu resumen. ¿Podrias intentar de nuevo?",
                    "resumen",
                )

        # Consultas agronómicas se resuelven de forma determinista para que el
        # LLM no pueda completar una ventana o recomendación que no exista en
        # una fuente verificada. El camino de calendario cita INIA y falla
        # cerrado para cultivos o comunas fuera del snapshot.
        if extracted.consulta_tipo == "agronomica":
            if any(keyword in transcribed_text.lower() for keyword in _CALENDARIO_KEYWORDS):
                from app.services.agricultural_calendar_service import get_calendario_agricola

                response_text = get_calendario_agricola(
                    extracted.producto or "",
                    AgroVozPipeline._extract_comuna(transcribed_text),
                )
                _marcar("calendario")
            else:
                from app.services.agronomic_rules_service import get_agronomic_rule_for_llm

                response_text = get_agronomic_rule_for_llm(
                    transcribed_text,
                    extracted.producto or "",
                )
                _marcar("regla_agronomica")
            return response_text, "agronomica"

        # 2. Pipeline normal: OpenRouter primero y Qwen después si el remoto
        # no responde dentro de su deadline. El orquestador mantiene la
        # política de no reintentar escrituras con otro proveedor.
        from app.services.llm_keywords import (
            _force_compound_keyword_tools,
            _force_keyword_tool,
        )
        from app.services.llm_service import answer_with_provider_order

        # Cargar cultivos de interés del agricultor para personalizar el
        # contexto del LLM. Si no tiene cultivos registrados, se pasa None
        # y el LLM funciona sin personalización (issue #125).
        cultivos = AgroVozPipeline._load_user_cultivos(chat_id_hash)

        # Detectar keywords de venta realizada para sugerir calculate_margin
        # al LLM via system_tip (Issue #91). El LLM ya tiene la tool en su
        # definicion, pero el system_tip refuerza la seleccion cuando el
        # agricultor habla en pasado ("vendi", "recibi").
        margin_keywords = [
            "vendí",
            "vendi",
            "vender",
            "vendido",
            "vendió",
            "vendio",
            "vendiste",
            "vendieron",
            "recibí",
            "recibi",
            "recibiste",
            "recibió",
            "recibio",
            "me pagaron",
            "me pagó",
            "me pago",
            "acabo de vender",
            "recién vendí",
            "recien vendi",
        ]
        system_tip = None
        if any(kw in transcribed_text.strip().lower() for kw in margin_keywords):
            system_tip = (
                "El agricultor menciona una venta YA REALIZADA. "
                "Si te da producto, cantidad, unidad y monto total, "
                "usa calculate_margin para comparar contra la referencia ODEPA."
            )
            logger.info(
                "Margin keywords detectados — system_tip activado para calculate_margin — producto=%s",
                extracted.producto,
            )

        # 1.8. Precio + clima: ambas fuentes son deterministas y deben
        # consultarse aunque una falle. El orquestador compuesto conserva el
        # bloque válido y explica el faltante, sin pagar la latencia del LLM.
        if extracted.consulta_tipo == "ambos" and system_tip is None:
            compound = await _force_compound_keyword_tools(
                transcribed_text.strip(),
                phone_hash=chat_id_hash,
            )
            logger.info(
                "Fast-path compuesto sin LLM — producto=%s",
                extracted.producto,
            )
            _marcar("fast_path_compuesto")
            return compound, "precio"

        # 1.9. Fast-path determinista: consulta simple y clasificada con certeza.
        # El LLM en CPU limitada tarda decenas de segundos casi todo en leer el
        # prompt de tools. Para la consulta tipica ("a cuanto esta la papa") las
        # tools deterministas ya arman la frase final con el dato de ODEPA, y el
        # LLM no aporta nada que el productor escuche. Solo se toma este atajo
        # cuando NO hay ambiguedad; ante la menor duda se usa el LLM completo.
        if AgroVozPipeline._puede_usar_fast_path(transcribed_text, extracted, system_tip):
            fast = await _force_keyword_tool(transcribed_text.strip(), phone_hash=chat_id_hash)
            if fast:
                # El gate ya garantizo precio|clima; se reafirma aca para que el
                # tipo calce con Intent (que no tiene "ambos").
                intent_fast: Intent = "precio" if extracted.consulta_tipo == "precio" else "clima"
                logger.info(
                    "Fast-path sin LLM — tipo=%s producto=%s",
                    intent_fast,
                    extracted.producto,
                )
                _marcar("fast_path")
                return fast, intent_fast

        try:
            response_text, provider = await answer_with_provider_order(
                transcribed_text.strip(),
                phone_hash=chat_id_hash,
                cultivos=cultivos,
                system_tip=system_tip,
                consulta_tipo=extracted.consulta_tipo,
            )
            _marcar(provider)
        except (TimeoutError, RuntimeError, OSError, ValueError):
            logger.error("Error en proveedores LLM — probando fallback determinista")
            try:
                fallback_result = await _force_keyword_tool(
                    transcribed_text.strip(), phone_hash=chat_id_hash
                )
            except (TimeoutError, RuntimeError, OSError, ValueError, SQLAlchemyError):
                logger.error("Fallback determinista tambien fallo")
                fallback_result = None

            if fallback_result:
                _marcar("fallback_keywords")
                logger.warning(
                    "Respuesta DEGRADADA (sin LLM) — producto=%s consulta_tipo=%s",
                    extracted.producto,
                    extracted.consulta_tipo,
                )
                response_text = fallback_result
            else:
                _marcar("generico")
                response_text = "Tuve un problema al procesar tu consulta. ¿Podrias intentar de nuevo?"

        intent = AgroVozPipeline._detect_intent(transcribed_text, response_text)
        return response_text, intent

    @staticmethod
    def _save_consultation(
        phone_hash: str,
        intent: Intent,
        query_text: str,
        response_text: str,
        audio_duration_ms: int,
        start_time: float,
        whisper_ms: int = 0,
        llm_ms: int = 0,
        tts_ms: int = 0,
        producto: str | None = None,
        requires_review: bool = False,
    ) -> int | None:
        """Guarda métricas y, solo con opt-in, contenido transitorio.

        Fire-and-forget: si falla, loguea el error pero no interrumpe
        el pipeline. La consulta se pierde, pero el audio se responde igual.
        Reintenta una vez si el primer intento falla por error transitorio
        de SQLite (WAL corruption, conexion en estado inconsistente tras
        llamadas largas a to_thread, bug B-11).

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

        Returns:
            ID de la consulta persistida, o None si ambos intentos fallan.
        """
        import time as _time

        from app.core.database import SessionLocal
        from app.models.consultation import Consultation
        from app.models.user_prefs import UserPrefs

        latency_ms = int((_time.monotonic() - start_time) * 1000)

        # Retry una vez si el primer intento falla por error transitorio.
        # Cada intento usa una sesion NUEVA (conexion fresca via NullPool)
        # para evitar reutilizar una conexion sqlite3 en estado inconsistente
        # tras llamadas largas a asyncio.to_thread (bug B-11).
        for _attempt in range(2):
            try:
                session = SessionLocal()
                try:
                    stage_history_content = False
                    if settings.consultation_history_enabled and intent in _HISTORY_STAGING_INTENTS:
                        stage_history_content = (
                            session.scalar(
                                select(UserPrefs.history_consent).where(
                                    UserPrefs.phone_hash == phone_hash,
                                )
                            )
                            is True
                        )

                    consulta = Consultation(
                        phone_hash=phone_hash,
                        intent=intent,
                        producto=producto,
                        query_text=query_text if stage_history_content else "",
                        response_text=response_text if stage_history_content else "",
                        audio_duration_ms=audio_duration_ms,
                        latency_ms=latency_ms,
                        whisper_ms=whisper_ms,
                        llm_ms=llm_ms,
                        tts_ms=tts_ms,
                        requires_review=requires_review,
                    )
                    session.add(consulta)
                    session.commit()
                    consultation_id = consulta.id
                    logger.debug(
                        "Consulta guardada — consultation_id=%s intent=%s latency_ms=%d",
                        consultation_id,
                        intent,
                        latency_ms,
                    )
                    return consultation_id
                except SQLAlchemyError:
                    session.rollback()
                    raise
                finally:
                    session.close()
            except SQLAlchemyError:
                if _attempt == 0:
                    logger.warning(
                        "Error guardando consulta (reintento) — intent=%s",
                        intent,
                    )
                else:
                    logger.error(
                        "Error guardando consulta en DB — intent=%s",
                        intent,
                    )
        return None

    @staticmethod
    def _update_previous_feedback(
        phone_hash: str,
        feedback: FeedbackAgricultor,
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
                    "Feedback sin consulta previa — feedback=%s",
                    feedback,
                )
                return False

            consulta.feedback = feedback
            session.commit()
            logger.info(
                "Feedback actualizado — consultation_id=%d feedback=%s",
                consulta.id,
                feedback,
            )
            return True
        except SQLAlchemyError:
            session.rollback()
            logger.error(
                "Error actualizando feedback — feedback=%s",
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
            return bool(count == 0)
        except SQLAlchemyError:
            logger.error("Error consultando consultas previas")
            return False
        finally:
            session.close()

    async def process(
        self,
        wav_path: Path | None,
        audio_duration_ms: int,
        message_id: str,
        chat_id_hash: str,
        request_id: str,
        chat_id: str | None = None,
        texto_directo: str | None = None,
        generar_audio: bool = True,
    ) -> AudioResponse:
        """Ejecuta el pipeline completo: Whisper → LLM → TTS.

        El pipeline tiene timeout de 120s. Si se excede, retorna
        AudioResponse con texto de error predefinido.

        Args:
            wav_path: Path al .wav 16kHz mono para Whisper. None si la consulta
                      ya viene en texto (``texto_directo``).
            audio_duration_ms: Duracion del audio en milisegundos. 0 para texto.
            message_id: ID del mensaje para trazabilidad en logs.
            chat_id_hash: Hash anonimizado del chat para guardar consulta.
            request_id: ID del request para trazabilidad.
            chat_id: Chat ID real de WhatsApp (opcional, para alertas proactivas).
            texto_directo: Consulta ya en texto (mensaje escrito de WhatsApp).
                           Salta Whisper: no hay audio que transcribir.
            generar_audio: Si False, salta el TTS y responde solo texto. Quien
                           escribe puede leer, y ahorrarse la sintesis baja
                           varios segundos de latencia.

        Returns:
            AudioResponse con ruta del audio TTS, texto, latencia e intent.
        """
        pipeline_start = time.monotonic()
        whisper_ms_ref = [0]
        llm_ms_ref = [0]
        tts_ms_ref = [0]
        welcome_ogg_ref: list[str | None] = [None]
        report_pdf_ref: list[str | None] = [None]
        primer_contacto_ref: list[bool] = [False]
        conversation_lease, conversation_busy = _claim_conversation(chat_id_hash)

        if conversation_busy:
            total_ms = int((time.monotonic() - pipeline_start) * 1000)
            return AudioResponse(
                audio_path="",
                text_response=_CONVERSATION_BUSY_TEXT,
                latency_ms=total_ms,
                intent="desconocido",
                whisper_ms=0,
                llm_ms=0,
                tts_ms=0,
            )

        try:
            if conversation_lease is not None and not conversation_lease.transition(ConversationState.BUSCANDO_DATOS):
                conversation_lease.abort()
                conversation_lease = None

            try:
                # Ejecutar pipeline con timeout.
                response = await asyncio.wait_for(
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
                        report_pdf_ref=report_pdf_ref,
                        primer_contacto_ref=primer_contacto_ref,
                        texto_directo=texto_directo,
                        generar_audio=generar_audio,
                        conversation_lease=conversation_lease,
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

            if conversation_lease is not None and conversation_lease.transition(ConversationState.ESPERANDO_CONSULTA):
                conversation_lease.finish()
            # Solo al devolver AudioResponse se transfiere el ownership del PDF
            # al caller, que lo elimina después de intentar el envío.
            report_pdf_ref[0] = None
            return response
        finally:
            # Si el pipeline fue cancelado, expiró o falló después de crear el
            # reporte, el path nunca llegó a AudioService: todavía es nuestro.
            if report_pdf_ref[0]:
                Path(report_pdf_ref[0]).unlink(missing_ok=True)
            # En timeout, cancelación o excepción se retira solo la sesión que
            # aún pertenece a esta lease. Una sesión reemplazante queda intacta.
            if conversation_lease is not None:
                conversation_lease.abort()

    async def _process_stages(
        self,
        wav_path: Path | None,
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
        report_pdf_ref: list[str | None],
        primer_contacto_ref: list[bool],
        texto_directo: str | None = None,
        generar_audio: bool = True,
        conversation_lease: ConversationLease | None = None,
    ) -> AudioResponse:
        """Ejecuta las etapas del pipeline secuencialmente con benchmark.

        Los refs se pasan como listas de 1 elemento para mutarlos
        dentro de la coroutine (Python no permite asignar nonlocal
        en closures anidadas de forma limpia).
        """
        if chat_id and chat_id_hash and chat_id_hash != "sin_chat":
            try:
                from app.services.alert_service import remember_price_variation_route

                await asyncio.to_thread(
                    remember_price_variation_route,
                    chat_id_hash,
                    chat_id,
                )
            except (SQLAlchemyError, RuntimeError, OSError, ValueError):
                logger.warning("Ruta de alertas proactivas no actualizada")

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
        # La DETECCION corre siempre, tambien para consultas escritas: el aviso de
        # responsabilidad se manda por texto en ambos caminos y es requisito legal.
        # Lo que depende de generar_audio es solo el TTS de la bienvenida, que no
        # tiene sentido mandarle a quien escribio.
        if chat_id_hash and chat_id_hash != "sin_chat":
            try:
                is_first = await asyncio.to_thread(self._is_first_contact, chat_id_hash)
                if is_first:
                    primer_contacto_ref[0] = True
                    if generar_audio:
                        tts_welcome = _get_tts_service()
                        welcome_ogg_ref[0] = await asyncio.to_thread(tts_welcome.synthesize, _WELCOME_TEXT)
                    logger.info(
                        "Primer contacto detectado — bienvenida=%s — message_id=%s request_id=%s",
                        "audio" if generar_audio else "solo texto",
                        message_id,
                        request_id,
                    )
            except (SQLAlchemyError, RuntimeError, OSError, ValueError):
                logger.warning(
                    "Deteccion de primer contacto o TTS de bienvenida fallo — "
                    "continuando sin bienvenida: message_id=%s request_id=%s",
                    message_id,
                    request_id,
                )

        # ── Etapa 1: Transcripcion Whisper ──────────────────────────
        # Si la consulta llego escrita, no hay nada que transcribir: se usa el
        # texto tal cual y el pipeline sigue igual desde la etapa 2.
        transcribed_text = ""
        t_whisper_start = time.monotonic()

        if texto_directo is not None:
            transcribed_text = texto_directo
            logger.info(
                "Consulta de texto (sin Whisper) — message_id=%s chars=%d request_id=%s",
                message_id,
                len(transcribed_text),
                request_id,
            )
        elif wav_path is None:
            logger.warning(
                "Sin audio ni texto para procesar — message_id=%s request_id=%s",
                message_id,
                request_id,
            )
        elif audio_duration_ms > _MAX_WHISPER_AUDIO_MS:
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
                    timeout=_WHISPER_TIMEOUT,
                )
                transcribed_text = str(transcription.get("text", ""))
                whisper_ms_ref[0] = int((time.monotonic() - t_whisper_start) * 1000)
                logger.info(
                    "Audio transcrito — message_id=%s chars=%d whisper_ms=%d request_id=%s",
                    message_id,
                    len(transcribed_text),
                    whisper_ms_ref[0],
                    request_id,
                )
            except (RuntimeError, FileNotFoundError, ValueError, TimeoutError):
                logger.warning(
                    "Whisper fallo — continuando sin transcripcion: message_id=%s request_id=%s",
                    message_id,
                    request_id,
                )

        # ── Etapa 1.5: Retención selectiva para dataset de voz rural (#96) ─
        # Solo si Whisper produjo transcripcion y el chat no es anonimo.
        # La retencion es opt-in (dataset_consent=True en user_prefs).
        # El cleanup posterior de audio_temp/ NO toca data/dataset/.
        # wav_path is not None: una consulta escrita no genera audio que retener.
        if (
            wav_path is not None
            and transcribed_text
            and transcribed_text.strip()
            and chat_id_hash
            and chat_id_hash != "sin_chat"
        ):
            try:
                await asyncio.to_thread(
                    retain_audio,
                    wav_path,
                    chat_id_hash,
                    transcribed_text,
                    audio_duration_ms,
                )
            except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
                logger.error(
                    "Error reteniendo audio para dataset — continuando pipeline: message_id=%s request_id=%s",
                    message_id,
                    request_id,
                )

        # ── Etapa 2: Generacion de respuesta (feedback, resumen o LLM) ───
        response_text = ""
        intent: Intent = "desconocido"
        producto: str | None = None
        report_pdf_path: str | None = None
        t_llm_start = time.monotonic()

        if transcribed_text and transcribed_text.strip():
            # Comandos de alerta proactiva (issue #88). Se evaluan antes
            # del feedback y del LLM para ser deterministas y rapidos.
            alert_response, alert_intent = await self._handle_alert_commands(transcribed_text, chat_id_hash, chat_id)
            if alert_response is not None:
                response_text = alert_response
                intent = cast(Intent, alert_intent or "alerta")
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

                    origen_ref: list[str] = ["desconocido"]
                    if settings.pdf_reports_enabled and self._is_reporte_pdf_query(transcribed_text):
                        response_text, report_pdf_path = await self._generate_report_response(chat_id_hash)
                        report_pdf_ref[0] = report_pdf_path
                        intent = "resumen"
                        origen_ref[0] = "reporte_pdf"
                    else:
                        # _generate_response detecta internamente si es resumen o LLM,
                        # maneja su propia lógica y error handling.
                        response_text, intent = await self._generate_response(
                            transcribed_text,
                            chat_id_hash,
                            origen_ref,
                        )
                        from app.services.report_service import REPORT_TOOL_SIGNAL

                        if response_text == REPORT_TOOL_SIGNAL:
                            response_text, report_pdf_path = await self._generate_report_response(chat_id_hash)
                            report_pdf_ref[0] = report_pdf_path
                            intent = "resumen"
                            origen_ref[0] = "reporte_pdf_tool"
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
                            "Respuesta generada — origen=%s message_id=%s intent=%s chars=%d gen_ms=%d request_id=%s",
                            origen_ref[0],
                            message_id,
                            intent,
                            len(response_text),
                            llm_ms_ref[0],
                            request_id,
                        )

        if conversation_lease is not None:
            comprehension_failed = intent == "desconocido" or not response_text.strip()
            if comprehension_failed:
                failure_count = conversation_lease.register_comprehension_failure()
                if failure_count is not None:
                    response_text = _COMPREHENSION_ESCALATION_TEXTS[failure_count - 1]
                response_state = ConversationState.ACLARANDO
            else:
                conversation_lease.reset_comprehension_failures()
                response_state = ConversationState.RESPONDIENDO
            conversation_lease.transition(response_state)

        # ── Etapa 3: Sintesis TTS ────────────────────────────────────
        # generar_audio=False para consultas escritas: quien escribe puede leer,
        # y saltarse Piper ahorra ~2s de los pocos que tenemos en 1 vCPU.
        response_ogg_path: str = ""
        t_tts_start = time.monotonic()

        if response_text and generar_audio:
            try:
                tts = _get_tts_service()
                voice_response = response_text
                if intent == "credito":
                    from app.services.indap_credit_service import format_indap_response_for_voice

                    voice_response = format_indap_response_for_voice(response_text)
                response_ogg_path = await asyncio.to_thread(tts.synthesize, voice_response)
                tts_ms_ref[0] = int((time.monotonic() - t_tts_start) * 1000)
                logger.info(
                    "TTS sintetizado — message_id=%s tts_ms=%d request_id=%s",
                    message_id,
                    tts_ms_ref[0],
                    request_id,
                )
            except (PiperModelNotFoundError, RuntimeError, ValueError, OSError):
                logger.warning(
                    "TTS fallo — message_id=%s request_id=%s",
                    message_id,
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
        consultation_id: int | None = None
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
                consultation_id = await asyncio.to_thread(
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
                logger.error(
                    "Error guardando consulta — continuando pipeline: message_id=%s intent=%s request_id=%s",
                    message_id,
                    intent,
                    request_id,
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
            consultation_id=consultation_id,
            whisper_ms=whisper_ms_ref[0],
            llm_ms=llm_ms_ref[0],
            tts_ms=tts_ms_ref[0],
            welcome_audio_path=welcome_ogg_ref[0],
            es_primer_contacto=primer_contacto_ref[0],
            report_pdf_path=report_pdf_path,
        )
