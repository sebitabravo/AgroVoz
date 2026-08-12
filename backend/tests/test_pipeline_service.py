"""Tests para app.services.pipeline_service: AgroVozPipeline.

Cubre: detect_intent (precio, clima, desconocido), _generate_response con
mock_answer, _save_consultation (DB y error), process (happy path, timeout,
Whisper falla, TTS falla, audio largo), y benchmark por etapa.

Sin modelo real: todos los tests corren en CI sin Whisper, llama-cpp-python,
ni Piper TTS.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.constants import Intent
from app.schemas.pipeline import AudioResponse
from app.services import pipeline_service as pipeline_module
from app.services.consultation_history_service import LatestConsultationContext
from app.services.conversation_state import (
    ConversationClaim,
    ConversationLease,
    ConversationRegistry,
    ConversationState,
    TransitionStatus,
)
from app.services.pipeline_service import (
    _CONVERSATION_BUSY_TEXT,
    _HISTORY_VOICE_QUERY_MAX_CHARS,
    _HISTORY_VOICE_RESPONSE_MAX_CHARS,
    _MAX_WHISPER_AUDIO_MS,
    _PIPELINE_TIMEOUT,
    _WHISPER_TIMEOUT,
    AgroVozPipeline,
)

_VALID_STATE_HASH = "9" * 64


def _assert_datos_sensibles_ausentes(
    caplog: pytest.LogCaptureFixture,
    *valores: str,
) -> None:
    """Verifica que ningún valor sensible llegue al texto de los logs."""
    for valor in valores:
        assert valor not in caplog.text


# ── Helpers ────────────────────────────────────────────────────────


def _fake_whisper_output() -> dict[str, object]:
    """Transcripcion falsa de Whisper para tests."""
    return {
        "text": "¿cual es el precio de la papa en lo valledor?",
        "language": "es",
        "segments": [],
        "duration_ms": 1200,
    }


def _mock_whisper_transcribe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mockea WhisperService.transcribe para inyectar una transcripcion falsa."""

    def fake_transcribe(_self: object, audio_path: str) -> dict[str, object]:
        return _fake_whisper_output()

    monkeypatch.setattr(
        "app.services.pipeline_service.WhisperService.transcribe",
        fake_transcribe,
    )


def _mock_llm_answer(monkeypatch: pytest.MonkeyPatch, answer_text: str) -> None:
    """Mockea el camino LLM y desactiva el fast-path determinista."""

    async def fake_answer(query: str, phone_hash: str | None = None, **kwargs: object) -> str:
        return answer_text

    async def fake_force_none(_query: str, phone_hash: str | None = None) -> None:
        return None

    monkeypatch.setattr("app.services.llm_service.answer", fake_answer)
    # Estos tests verifican el contrato del proveedor LLM. No deben usar una
    # base vacía como bypass accidental del fast-path de precio/clima.
    monkeypatch.setattr("app.services.llm_keywords._force_keyword_tool", fake_force_none)


def _mock_tts_synthesize(monkeypatch: pytest.MonkeyPatch, output_path: str) -> None:
    """Mockea TTSService.synthesize para retornar un path predefinido."""

    def fake_synthesize(_self: object, text: str, output_dir: str | Path | None = None) -> str:
        return output_path

    monkeypatch.setattr(
        "app.services.pipeline_service.TTSService.synthesize",
        fake_synthesize,
    )


def _mock_tts_fail(monkeypatch: pytest.MonkeyPatch, exc: type[Exception] = RuntimeError, msg: str = "TTS roto") -> None:
    """Mockea TTSService.synthesize para lanzar excepcion."""

    def fake_synthesize_err(_self: object, text: str, output_dir: str | Path | None = None) -> str:
        raise exc(msg)

    monkeypatch.setattr(
        "app.services.pipeline_service.TTSService.synthesize",
        fake_synthesize_err,
    )


def _mock_db_save(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Mockea _save_consultation para capturar la consulta guardada.

    Retorna una lista mutable donde se acumulan los kwargs de cada llamada.
    """
    calls: list[dict[str, object]] = []

    def fake_save(
        _self: object,
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
    ) -> int:
        calls.append(
            {
                "phone_hash": phone_hash,
                "intent": intent,
                "query_text": query_text,
                "response_text": response_text,
                "audio_duration_ms": audio_duration_ms,
                "whisper_ms": whisper_ms,
                "llm_ms": llm_ms,
                "tts_ms": tts_ms,
                "producto": producto,
                "requires_review": requires_review,
            }
        )
        return 42

    monkeypatch.setattr(AgroVozPipeline, "_save_consultation", fake_save)
    return calls


def _enable_conversation_state(
    monkeypatch: pytest.MonkeyPatch,
    *,
    timeout_minutes: int = 30,
) -> None:
    """Activa el gate con un registro global limpio para una prueba."""
    monkeypatch.setattr(settings, "use_conversation_state", True)
    monkeypatch.setattr(
        settings,
        "conversation_timeout_minutes",
        timeout_minutes,
    )
    monkeypatch.setattr(pipeline_module, "_conversation_registry", None)


def _mock_generated_response(
    monkeypatch: pytest.MonkeyPatch,
    response_text: str = "La papa está a 500 pesos el kilo.",
) -> None:
    """Aísla la generación para probar únicamente la orquestación."""

    async def fake_generate(
        _transcribed_text: str,
        _chat_id_hash: str,
        origen_ref: list[str] | None = None,
    ) -> tuple[str, Intent]:
        if origen_ref is not None:
            origen_ref[0] = "test"
        return response_text, "precio"

    monkeypatch.setattr(
        AgroVozPipeline,
        "_generate_response",
        staticmethod(fake_generate),
    )


# ── _detect_intent ─────────────────────────────────────────────────


class TestDetectIntent:
    """Keyword matching para metrica de intencion."""

    def test_precio_por_keyword_papa(self) -> None:
        intent = AgroVozPipeline._detect_intent("precio de la papa en lo valledor", "")
        assert intent == "precio"

    def test_precio_por_keyword_luca(self) -> None:
        intent = AgroVozPipeline._detect_intent("a cuanto estan las papas", "en la feria estan a 200 pesos el kilo")
        assert intent == "precio"

    def test_precio_por_keyword_cuesta_en_respuesta(self) -> None:
        intent = AgroVozPipeline._detect_intent(
            "como esta la papa",
            "la papa cuesta 450 pesos el kilo en lo valledor",
        )
        assert intent == "precio"

    def test_clima_por_keyword_temperatura(self) -> None:
        intent = AgroVozPipeline._detect_intent("cual es la temperatura en traiguen", "")
        assert intent == "clima"

    def test_clima_por_keyword_lluvia(self) -> None:
        # "llover" NO esta en keywords de clima, "lluvia" y "lloviendo" si
        intent_sin_match = AgroVozPipeline._detect_intent("va a llover manana", "")
        assert intent_sin_match != "clima"  # "llover" no es keyword
        # Con keyword correcta
        intent = AgroVozPipeline._detect_intent("habra lluvia manana", "")
        assert intent == "clima"

    def test_clima_por_keyword_frio_en_respuesta(self) -> None:
        intent = AgroVozPipeline._detect_intent(
            "como esta el dia",
            "hace frio en traiguen con 5 grados",
        )
        assert intent == "clima"

    def test_desconocido_sin_keywords(self) -> None:
        intent = AgroVozPipeline._detect_intent("hola buenos dias", "en que puedo ayudarte")
        assert intent == "desconocido"

    def test_desconocido_textos_vacios(self) -> None:
        intent = AgroVozPipeline._detect_intent("", "")
        assert intent == "desconocido"

    def test_precio_gana_sobre_clima_cuando_ambos(self) -> None:
        """Si hay keywords de ambos, precio tiene precedencia (se evalua primero)."""
        intent = AgroVozPipeline._detect_intent(
            "cual es el precio de la papa y el clima en la vega",
            "",
        )
        assert intent == "precio"

    def test_mayusculas_insensibles(self) -> None:
        intent = AgroVozPipeline._detect_intent("PRECIO DE LA PAPA EN LO VALLEDOR", "")
        assert intent == "precio"


# ── _is_resumen_query ──────────────────────────────────────────────


class TestIsResumenQuery:
    """Deteccion de keyword 'resumen' para saltear el LLM."""

    def test_resumen_keyword(self) -> None:
        assert AgroVozPipeline._is_resumen_query("resumen") is True

    def test_mi_resumen(self) -> None:
        assert AgroVozPipeline._is_resumen_query("mi resumen") is True

    def test_como_va_el_mes(self) -> None:
        assert AgroVozPipeline._is_resumen_query("como va el mes") is True

    def test_como_va_mi_mes(self) -> None:
        assert AgroVozPipeline._is_resumen_query("como va mi mes") is True

    def test_resumen_del_mes(self) -> None:
        assert AgroVozPipeline._is_resumen_query("resumen del mes") is True

    def test_no_resumen_precio(self) -> None:
        assert AgroVozPipeline._is_resumen_query("precio de la papa") is False

    def test_no_resumen_clima(self) -> None:
        assert AgroVozPipeline._is_resumen_query("clima en traiguen") is False

    def test_no_resumen_vacio(self) -> None:
        assert AgroVozPipeline._is_resumen_query("") is False

    def test_resumen_mayusculas(self) -> None:
        assert AgroVozPipeline._is_resumen_query("RESUMEN") is True

    def test_resumen_en_oracion(self) -> None:
        assert AgroVozPipeline._is_resumen_query("dame mi resumen por favor") is True


class TestIsReportePdfQuery:
    """Detecta pedidos de archivo sin cambiar el resumen histórico corto."""

    @pytest.mark.parametrize(
        "query",
        [
            "mándame un resumen de la semana",
            "envíame un informe en PDF",
            "quiero mi reporte semanal de precios y clima",
            "reporte semanal",
        ],
    )
    def test_detecta_pedido_explicito(self, query: str) -> None:
        assert AgroVozPipeline._is_reporte_pdf_query(query) is True

    @pytest.mark.parametrize(
        "query",
        [
            "resumen",
            "dame mi resumen",
            "precio de la papa",
            "no quiero un reporte semanal",
        ],
    )
    def test_no_confunde_resumen_historico_con_pdf(self, query: str) -> None:
        assert AgroVozPipeline._is_reporte_pdf_query(query) is False


# ── _is_explicit_history_query ─────────────────────────────────────


class TestIsExplicitHistoryQuery:
    """Detección cerrada del pedido de contexto anterior."""

    @pytest.mark.parametrize(
        "query",
        [
            "¿Cuál fue mi última consulta?",
            "qué pregunté antes",
            "CONSULTA ANTERIOR",
            "Por favor, dime cuál fue mi consulta anterior",
        ],
    )
    def test_detecta_solo_frases_inequivocas(self, query: str) -> None:
        assert AgroVozPipeline._is_explicit_history_query(query) is True

    @pytest.mark.parametrize(
        "query",
        [
            "lo mismo",
            "haz lo mismo",
            "antes de consultar dime el clima",
            "esta es una consulta anterior al almuerzo",
            "precio de la papa",
            "",
        ],
    )
    def test_evade_referencias_ambiguas_y_falsos_positivos(
        self,
        query: str,
    ) -> None:
        assert AgroVozPipeline._is_explicit_history_query(query) is False


# ── _extract_producto ──────────────────────────────────────────────


class TestExtractProducto:
    """Extraccion de producto agricola del texto transcrito."""

    def test_papa(self) -> None:
        assert AgroVozPipeline._extract_producto("precio de la papa") == "papa"

    def test_tomate(self) -> None:
        assert AgroVozPipeline._extract_producto("a cuanto esta el tomate") == "tomate"

    def test_cebolla(self) -> None:
        assert AgroVozPipeline._extract_producto("cebolla en lo valledor") == "cebolla"

    def test_sin_producto(self) -> None:
        assert AgroVozPipeline._extract_producto("clima en traiguen") is None

    def test_temperatura_no_es_producto(self) -> None:
        assert AgroVozPipeline._extract_producto("qué temperatura hace en temuco") is None

    def test_vacio(self) -> None:
        assert AgroVozPipeline._extract_producto("") is None

    def test_producto_en_oracion_larga(self) -> None:
        assert AgroVozPipeline._extract_producto("cuanto esta el kilo de papa en la vega") == "papa"

    def test_producto_con_acento(self) -> None:
        """Productos con acento matchean (limón, brócoli)."""
        assert AgroVozPipeline._extract_producto("precio del limón") == "limón"

    def test_no_confunde_papaya_con_papa(self) -> None:
        assert AgroVozPipeline._extract_producto("calendario de papaya") is None

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("siembra de zapallo italiano", "zapallo italiano"),
            ("cosecha de poroto verde", "poroto verde"),
            ("siembra de poroto granado", "poroto granado"),
        ],
    )
    def test_prioriza_producto_compuesto(self, query: str, expected: str) -> None:
        assert AgroVozPipeline._extract_producto(query) == expected


# ── _generate_response ─────────────────────────────────────────────


@pytest.mark.asyncio
class TestGenerateResponse:
    """Generacion de respuesta LLM con mock de answer()."""

    async def test_respuesta_normal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _mock_llm_answer(
            monkeypatch,
            "La papa cuesta 450 pesos el kilo en Lo Valledor",
        )
        text, intent = await AgroVozPipeline._generate_response("precio de la papa en lo valledor", "test-chat-hash")
        assert "450" in text
        assert intent == "precio"

    async def test_precio_sin_datos_no_cae_en_respuesta_generica(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Una ausencia ODEPA se entrega como dato faltante, no como timeout."""

        async def provider_no_deberia_correr(*_args: object, **_kwargs: object) -> str:
            raise AssertionError("el precio sin datos no debe invocar al LLM")

        monkeypatch.setattr(
            "app.services.llm_service.answer_with_provider_order",
            provider_no_deberia_correr,
        )

        text, intent = await AgroVozPipeline._generate_response(
            "a cuanto esta el pepino en temuco",
            "test-chat-hash",
        )

        assert text == "No tengo datos de precio para pepino en temuco."
        assert intent == "precio"

    async def test_query_vacia(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Si el texto transcrito esta vacio, retorna mensaje de error y desconocido."""
        _mock_llm_answer(monkeypatch, "no deberia llamarse")
        text, intent = await AgroVozPipeline._generate_response("", "test-chat-hash")
        assert "entendi" in text.lower() or "entendí" in text.lower()
        assert intent == "desconocido"

    async def test_query_solo_espacios(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Si el texto transcrito son solo espacios, retorna mensaje de error."""
        _mock_llm_answer(monkeypatch, "no deberia llamarse")
        text, intent = await AgroVozPipeline._generate_response("   ", "test-chat-hash")
        assert "entendi" in text.lower() or "entendí" in text.lower()
        assert intent == "desconocido"

    async def test_saludo_no_loguea_texto_ni_hash(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """El fast-path de saludo conserva solo la longitud del mensaje."""
        secreto = "SALUDO-PRIVADO-NO-LOGUEAR"
        phone_hash = "prefijohash-saludo-privado"
        monkeypatch.setattr(
            "app.services.llm_keywords._detect_greeting",
            lambda _text: True,
        )
        caplog.set_level(logging.DEBUG, logger="app.services.pipeline_service")

        _text, intent = await AgroVozPipeline._generate_response(
            secreto,
            phone_hash,
        )

        assert intent == "saludo"
        assert f"chars={len(secreto)}" in caplog.text
        _assert_datos_sensibles_ausentes(
            caplog,
            secreto,
            phone_hash,
            phone_hash[:8],
        )

    @pytest.mark.parametrize(
        "query",
        [
            "borra mi historial",
            "¿Elimina todo mi historial, por favor?",
            "borra mis consultas",
            "desactiva mi historial",
        ],
    )
    async def test_borrado_historial_revoca_sin_llm(
        self,
        monkeypatch: pytest.MonkeyPatch,
        query: str,
    ) -> None:
        """Una orden exacta usa la identidad verificada y evita el modelo."""
        delete_calls: list[tuple[str, dict[str, object]]] = []

        def fake_delete(phone_hash: str, **kwargs: object) -> object:
            delete_calls.append((phone_hash, kwargs))
            return object()

        async def fail_llm(*_args: object, **_kwargs: object) -> str:
            raise AssertionError("Un comando de privacidad no debe llegar al LLM")

        monkeypatch.setattr(
            "app.services.consultation_history_service.delete_history",
            fake_delete,
        )
        monkeypatch.setattr("app.services.llm_service.answer", fail_llm)
        origin = ["desconocido"]

        text, intent = await AgroVozPipeline._generate_response(
            query,
            "a" * 64,
            origin,
        )

        assert intent == "resumen"
        assert "desactivé" in text
        assert origin == ["historial_borrado"]
        assert delete_calls == [
            (
                "a" * 64,
                {
                    "reason": "consent_revoked",
                    "requested_via": "verified_whatsapp",
                },
            )
        ]

    @pytest.mark.parametrize(
        "query",
        [
            "borra el precio de la papa",
            "quizás borra mi historial mañana",
            "cuál fue mi historial",
            "lo mismo",
        ],
    )
    async def test_borrado_historial_rechaza_frases_ambiguas(
        self,
        query: str,
    ) -> None:
        """Texto adicional o ambiguo no dispara una acción destructiva."""
        assert AgroVozPipeline._is_history_deletion_query(query) is False

    async def test_historial_consentido_responde_sin_llm_y_sin_filtrar_logs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """El contexto se recorta otra vez y nunca se envía al modelo."""
        phone_hash = "hash-historico-secreto"
        previous_query = "consulta secreta " + ("q" * 300)
        previous_response = "respuesta secreta " + ("r" * 500)
        context = LatestConsultationContext(
            query_text=previous_query,
            response_text=previous_response,
            intent="precio",
            producto="papa",
        )
        thread_calls: list[tuple[object, tuple[object, ...]]] = []
        reader_calls: list[str] = []

        def fake_reader(received_hash: str) -> LatestConsultationContext:
            reader_calls.append(received_hash)
            return context

        async def inline_to_thread(
            function: object,
            *args: object,
            **kwargs: object,
        ) -> object:
            thread_calls.append((function, args))
            assert callable(function)
            return function(*args, **kwargs)

        async def fail_llm(*_args: object, **_kwargs: object) -> str:
            raise AssertionError("El historial explícito no debe invocar al LLM")

        monkeypatch.setattr(
            "app.services.consultation_history_service.get_latest_consultation_context",
            fake_reader,
        )
        monkeypatch.setattr(
            "app.services.pipeline_service.asyncio.to_thread",
            inline_to_thread,
        )
        monkeypatch.setattr("app.services.llm_service.answer", fail_llm)
        caplog.set_level(logging.DEBUG)
        origin = ["desconocido"]

        text, intent = await AgroVozPipeline._generate_response(
            "¿Cuál fue mi última consulta?",
            phone_hash,
            origin,
        )

        assert intent == "resumen"
        assert origin == ["historial"]
        assert reader_calls == [phone_hash]
        assert len(thread_calls) == 1
        assert "Tu consulta anterior fue:" in text
        assert "consulta secreta" in text
        assert "La respuesta que recibiste fue:" in text
        assert "respuesta secreta" in text
        assert previous_query not in text
        assert previous_response not in text
        assert len(text) <= (_HISTORY_VOICE_QUERY_MAX_CHARS + _HISTORY_VOICE_RESPONSE_MAX_CHARS + 80)
        assert phone_hash not in caplog.text
        assert previous_query not in caplog.text
        assert previous_response not in caplog.text

    @pytest.mark.parametrize(
        "unavailable_reason",
        ["gate_apagado", "sin_consentimiento", "sin_fila"],
    )
    async def test_historial_no_disponible_conserva_camino_stateless(
        self,
        monkeypatch: pytest.MonkeyPatch,
        unavailable_reason: str,
    ) -> None:
        """Cualquier None del lector cae al mismo LLM que existía antes."""
        reader_calls: list[str] = []
        llm_calls: list[str] = []

        def unavailable_reader(phone_hash: str) -> None:
            reader_calls.append(phone_hash)
            return None

        async def stateless_answer(
            query: str,
            **_kwargs: object,
        ) -> str:
            llm_calls.append(query)
            return f"respuesta stateless para {unavailable_reason}"

        monkeypatch.setattr(
            "app.services.consultation_history_service.get_latest_consultation_context",
            unavailable_reader,
        )
        monkeypatch.setattr(
            AgroVozPipeline,
            "_load_user_cultivos",
            staticmethod(lambda _phone_hash: None),
        )
        monkeypatch.setattr("app.services.llm_service.answer", stateless_answer)
        origin = ["desconocido"]

        text, _intent = await AgroVozPipeline._generate_response(
            "qué pregunté antes",
            "test-chat-hash",
            origin,
        )

        assert reader_calls == ["test-chat-hash"]
        assert llm_calls == ["qué pregunté antes"]
        assert text == f"respuesta stateless para {unavailable_reason}"
        assert origin == ["llm"]

    async def test_error_del_lector_conserva_camino_stateless(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Un fallo inesperado de almacenamiento tampoco cambia la respuesta."""
        sensitive_error = "contenido-historico-que-no-debe-loguearse"

        def broken_reader(_phone_hash: str) -> None:
            raise SQLAlchemyError(sensitive_error)

        async def stateless_answer(query: str, **_kwargs: object) -> str:
            return f"respuesta actual para {query}"

        monkeypatch.setattr(
            "app.services.consultation_history_service.get_latest_consultation_context",
            broken_reader,
        )
        monkeypatch.setattr(
            AgroVozPipeline,
            "_load_user_cultivos",
            staticmethod(lambda _phone_hash: None),
        )
        monkeypatch.setattr("app.services.llm_service.answer", stateless_answer)
        caplog.set_level(logging.DEBUG)
        origin = ["desconocido"]

        text, _intent = await AgroVozPipeline._generate_response(
            "consulta anterior",
            "test-chat-hash",
            origin,
        )

        assert text == "respuesta actual para consulta anterior"
        assert origin == ["llm"]
        assert sensitive_error not in caplog.text

    async def test_answer_lanza_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Si answer() lanza excepcion y no hay keywords, retorna disculpa generica.

        Path (d) del fallback determinista (Issue #121): sin producto, clima
        ni venta en la query → mensaje generico + intent "desconocido".
        """

        async def fake_answer_error(_query: str, phone_hash: str | None = None, **kwargs: object) -> str:
            raise RuntimeError("LLM colapso")

        # Mockear _force_keyword_tool → None: sin keywords, no hay fallback real.
        async def fake_force_none(_query: str, phone_hash: str | None = None) -> str | None:
            return None

        monkeypatch.setattr("app.services.llm_service.answer", fake_answer_error)
        monkeypatch.setattr(
            "app.services.llm_keywords._force_keyword_tool",
            fake_force_none,
        )
        # Query SIN keywords de precio ni clima → intent debe ser "desconocido"
        text, intent = await AgroVozPipeline._generate_response("hola como estas", "test-chat-hash")
        assert "problema" in text.lower() or "intentar" in text.lower()
        assert intent == "desconocido"

    async def test_answer_lanza_exception_fallback_precio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Path (a): LLM cae, keyword de producto → fallback get_price real.

        Issue #121: si el LLM falla pero la query contiene un producto
        agricola, se llama get_price_for_llm via _force_keyword_tool
        y se responde con datos reales de ODEPA.
        """

        async def fake_answer_error(_query: str, phone_hash: str | None = None, **kwargs: object) -> str:
            raise RuntimeError("LLM colapso")

        async def fake_force_precio(query: str, phone_hash: str | None = None) -> str:
            return "Papa esta a $1.200 el kilo en Lo Valledor, precio del 14/07/2026 segun ODEPA."

        monkeypatch.setattr("app.services.llm_service.answer", fake_answer_error)
        monkeypatch.setattr(
            "app.services.llm_keywords._force_keyword_tool",
            fake_force_precio,
        )

        text, intent = await AgroVozPipeline._generate_response(
            "a cuanto esta la papa en lo valledor", "test-chat-hash"
        )
        assert "$1.200" in text
        assert "papa" in text.lower()
        assert intent == "precio"

    async def test_answer_lanza_exception_fallback_clima(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Path (b): LLM cae, keyword de clima → fallback get_weather real.

        Issue #121: si el LLM falla pero la query contiene keywords
        climaticos, se llama get_weather(lat=-38.23, lon=-72.68) via
        _force_keyword_tool y se responde con datos de OpenMeteo.
        """

        async def fake_answer_error(_query: str, phone_hash: str | None = None, **kwargs: object) -> str:
            raise RuntimeError("LLM colapso")

        async def fake_force_clima(query: str, phone_hash: str | None = None) -> str:
            return "En Traiguen ahora: 8 grados, nublado, humedad 80%, viento 3.5 m/s, lluvia 0.8 mm, segun OpenMeteo."

        monkeypatch.setattr("app.services.llm_service.answer", fake_answer_error)
        monkeypatch.setattr(
            "app.services.llm_keywords._force_keyword_tool",
            fake_force_clima,
        )

        text, intent = await AgroVozPipeline._generate_response("como esta el clima en traiguen", "test-chat-hash")
        assert "grados" in text.lower()
        assert "traiguen" in text.lower()
        assert intent == "clima"

    async def test_answer_lanza_exception_fallback_venta(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Path (c): LLM cae, patron "N kilos de producto" → fallback venta.

        Issue #121: si el LLM falla pero la query contiene un patron
        de venta ("N kilos de producto"), se llama calculate_sale_value
        via _force_keyword_tool.
        """

        async def fake_answer_error(_query: str, phone_hash: str | None = None, **kwargs: object) -> str:
            raise RuntimeError("LLM colapso")

        async def fake_force_venta(query: str, phone_hash: str | None = None) -> str:
            return (
                "Por 30 kilos de papa recibiras aproximadamente "
                "$36.000 pesos chilenos a precio de $1.200 el kilo "
                "en Lo Valledor, segun ODEPA."
            )

        monkeypatch.setattr("app.services.llm_service.answer", fake_answer_error)
        monkeypatch.setattr(
            "app.services.llm_keywords._force_keyword_tool",
            fake_force_venta,
        )

        text, intent = await AgroVozPipeline._generate_response("voy a vender 30 kilos de papa", "test-chat-hash")
        assert "kilos" in text.lower()
        assert "papa" in text.lower()
        assert "recibiras" in text.lower()
        assert intent == "precio"

    async def test_margin_y_fallback_no_loguean_consulta_hash_ni_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Los caminos degradados publican solo producto/tipo estructurados."""
        secreto = "DATO-PRIVADO-MARGEN-NO-LOGUEAR"
        phone_hash = "prefijohash-margen-privado"
        query = f"vendí 30 kilos de papa {secreto}"

        async def fail_answer(
            _query: str,
            **_kwargs: object,
        ) -> str:
            raise RuntimeError(f"{secreto} {phone_hash}")

        async def no_openrouter(
            _query: str,
            **_kwargs: object,
        ) -> None:
            return None

        async def fallback_estructurado(
            _query: str,
            phone_hash: str | None = None,
        ) -> str:
            return "Referencia estructurada de papa: 500 pesos."

        monkeypatch.setattr(
            AgroVozPipeline,
            "_load_user_cultivos",
            staticmethod(lambda _phone_hash: None),
        )
        monkeypatch.setattr("app.services.llm_service.answer", fail_answer)
        monkeypatch.setattr(
            "app.services.llm_service.answer_via_openrouter",
            no_openrouter,
        )
        monkeypatch.setattr(
            "app.services.llm_keywords._force_keyword_tool",
            fallback_estructurado,
        )
        caplog.set_level(logging.DEBUG, logger="app.services.pipeline_service")

        text, intent = await AgroVozPipeline._generate_response(
            query,
            phone_hash,
        )

        assert text == "Referencia estructurada de papa: 500 pesos."
        assert intent == "precio"
        assert "producto=papa" in caplog.text
        _assert_datos_sensibles_ausentes(
            caplog,
            secreto,
            query,
            phone_hash,
            phone_hash[:8],
        )


# ── process() pipeline completo ────────────────────────────────────


@pytest.mark.asyncio
class TestProcess:
    """Tests de integracion para AgroVozPipeline.process()."""

    @pytest.fixture(autouse=True)
    def _state_machine_off_by_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Preserva el contrato stateless de todas las regresiones previas."""
        monkeypatch.setattr(settings, "use_conversation_state", False)
        monkeypatch.setattr(pipeline_module, "_conversation_registry", None)

    @pytest.fixture
    def wav_path(self, tmp_path: Path) -> Path:
        """Crea un archivo WAV falso para los tests."""
        p = tmp_path / "test_audio.wav"
        p.write_bytes(b"FAKE_WAV_16KHZ_MONO")
        return p

    @pytest.fixture
    def tts_ogg(self, tmp_path: Path) -> str:
        """Crea un OGG falso para simular salida de TTS."""
        p = tmp_path / "pipeline_output.ogg"
        p.write_bytes(b"FAKE_TTS_OGG")
        return str(p)

    @pytest.mark.parametrize("entrada", ["texto", "audio"])
    async def test_logs_no_exponen_contenido_ni_hash_en_entradas(
        self,
        entrada: str,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        wav_path: Path,
    ) -> None:
        """Texto directo y Whisper comparten el mismo contrato de minimización."""
        secreto = f"CONTENIDO-PRIVADO-{entrada.upper()}-NO-LOGUEAR"
        phone_hash = f"prefijohash-{entrada}-privado"

        async def no_alerta(
            _transcribed_text: str,
            _phone_hash: str,
            _wa_chat_id: str | None,
        ) -> tuple[None, None]:
            return None, None

        def fake_transcribe(
            _self: object,
            _audio_path: str,
        ) -> dict[str, object]:
            return {"text": secreto}

        monkeypatch.setattr(
            AgroVozPipeline,
            "_is_first_contact",
            staticmethod(lambda _phone_hash: False),
        )
        monkeypatch.setattr(
            AgroVozPipeline,
            "_handle_alert_commands",
            staticmethod(no_alerta),
        )
        monkeypatch.setattr(
            "app.services.pipeline_service.WhisperService.transcribe",
            fake_transcribe,
        )
        monkeypatch.setattr(pipeline_module, "retain_audio", lambda *_args: None)
        _mock_generated_response(monkeypatch)
        _mock_db_save(monkeypatch)
        caplog.set_level(logging.DEBUG, logger="app.services.pipeline_service")

        result = await AgroVozPipeline().process(
            wav_path=None if entrada == "texto" else wav_path,
            audio_duration_ms=0 if entrada == "texto" else 1000,
            message_id=f"msg-privacidad-{entrada}",
            chat_id_hash=phone_hash,
            request_id=f"req-privacidad-{entrada}",
            texto_directo=secreto if entrada == "texto" else None,
            generar_audio=False,
        )

        assert result.consultation_id == 42
        assert f"msg-privacidad-{entrada}" in caplog.text
        assert f"req-privacidad-{entrada}" in caplog.text
        assert f"chars={len(secreto)}" in caplog.text
        _assert_datos_sensibles_ausentes(
            caplog,
            secreto,
            phone_hash,
            phone_hash[:8],
        )

    # ── State machine (#192) ───────────────────────────────────

    async def test_state_flag_off_no_crea_registry_y_conserva_flujo(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """El gate apagado no construye estado ni cambia la respuesta."""
        registry_constructor = Mock(side_effect=AssertionError("No debe crear registry"))
        monkeypatch.setattr(
            pipeline_module,
            "ConversationRegistry",
            registry_constructor,
        )
        _mock_first_contact(monkeypatch, is_first=False)
        _mock_generated_response(monkeypatch)
        _mock_db_save(monkeypatch)

        result = await AgroVozPipeline().process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="state-off",
            chat_id_hash=_VALID_STATE_HASH,
            request_id="state-off-request",
            texto_directo="consulta compleja sobre el precio de la papa",
            generar_audio=False,
        )

        assert result.text_response == "La papa está a 500 pesos el kilo."
        assert pipeline_module._conversation_registry is None
        registry_constructor.assert_not_called()

    async def test_state_recorre_misma_secuencia_para_texto_y_audio(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
    ) -> None:
        """Ambas entradas recorren recibido, búsqueda, respuesta y espera."""
        _enable_conversation_state(monkeypatch)
        _mock_first_contact(monkeypatch, is_first=False)
        _mock_generated_response(monkeypatch)
        _mock_whisper_transcribe(monkeypatch)
        _mock_tts_synthesize(monkeypatch, tts_ogg)
        _mock_db_save(monkeypatch)
        monkeypatch.setattr(
            "app.services.pipeline_service.retain_audio",
            lambda *_args, **_kwargs: None,
        )

        async def no_alert(
            _text: str,
            _phone_hash: str,
            _chat_id: str | None,
        ) -> tuple[None, None]:
            return None, None

        monkeypatch.setattr(
            AgroVozPipeline,
            "_handle_alert_commands",
            staticmethod(no_alert),
        )

        sequence: list[ConversationState] = []
        original_claim = ConversationRegistry.claim
        original_transition = ConversationLease.transition

        def record_claim(
            registry: ConversationRegistry,
            phone_hash: str,
        ) -> ConversationClaim:
            claim = original_claim(registry, phone_hash)
            if claim.status == TransitionStatus.APLICADA:
                sequence.append(claim.conversation.state)
            return claim

        def record_transition(
            lease: ConversationLease,
            target: ConversationState,
        ) -> bool:
            sequence.append(target)
            return original_transition(lease, target)

        monkeypatch.setattr(ConversationRegistry, "claim", record_claim)
        monkeypatch.setattr(
            ConversationLease,
            "transition",
            record_transition,
        )

        pipeline = AgroVozPipeline()
        text_hash = "a" * 64
        audio_hash = "b" * 64
        text_result = await pipeline.process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="state-text",
            chat_id_hash=text_hash,
            request_id="state-text-request",
            texto_directo="consulta compleja sobre el precio de la papa",
            generar_audio=False,
        )
        audio_result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=1_000,
            message_id="state-audio",
            chat_id_hash=audio_hash,
            request_id="state-audio-request",
        )

        expected_turn = [
            ConversationState.CONSULTA_RECIBIDA,
            ConversationState.BUSCANDO_DATOS,
            ConversationState.RESPONDIENDO,
            ConversationState.ESPERANDO_CONSULTA,
        ]
        assert sequence == expected_turn * 2
        assert text_result.audio_path == ""
        assert audio_result.audio_path == tts_ogg
        registry = pipeline_module._conversation_registry
        assert isinstance(registry, ConversationRegistry)
        text_snapshot = registry.snapshot(text_hash)
        audio_snapshot = registry.snapshot(audio_hash)
        assert text_snapshot is not None
        assert audio_snapshot is not None
        assert text_snapshot.state == ConversationState.ESPERANDO_CONSULTA
        assert audio_snapshot.state == ConversationState.ESPERANDO_CONSULTA

    async def test_state_sin_respuesta_aclara_y_vuelve_a_espera(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Una entrada vacía usa ACLARANDO sin quedar bloqueada."""
        _enable_conversation_state(monkeypatch)
        _mock_first_contact(monkeypatch, is_first=False)
        _mock_db_save(monkeypatch)
        transitions: list[ConversationState] = []
        original_transition = ConversationLease.transition

        def record_transition(
            lease: ConversationLease,
            target: ConversationState,
        ) -> bool:
            transitions.append(target)
            return original_transition(lease, target)

        monkeypatch.setattr(
            ConversationLease,
            "transition",
            record_transition,
        )

        result = await AgroVozPipeline().process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="state-empty",
            chat_id_hash="c" * 64,
            request_id="state-empty-request",
            texto_directo="",
            generar_audio=False,
        )

        assert "más despacio" in result.text_response
        assert transitions == [
            ConversationState.BUSCANDO_DATOS,
            ConversationState.ACLARANDO,
            ConversationState.ESPERANDO_CONSULTA,
        ]
        registry = pipeline_module._conversation_registry
        assert isinstance(registry, ConversationRegistry)
        snapshot = registry.snapshot("c" * 64)
        assert snapshot is not None
        assert snapshot.comprehension_failures == 1

    @pytest.mark.parametrize("entrada", ["texto", "audio"])
    async def test_state_escala_tres_fallos_en_texto_y_audio(
        self,
        entrada: str,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
    ) -> None:
        """La misma escalada guía ambos canales sin prometer contacto humano."""
        _enable_conversation_state(monkeypatch)
        _mock_first_contact(monkeypatch, is_first=False)
        _mock_db_save(monkeypatch)
        synthesized: list[str] = []

        async def no_alerta(
            _text: str,
            _phone_hash: str,
            _chat_id: str | None,
        ) -> tuple[None, None]:
            return None, None

        async def incomprensible(
            _text: str,
            _phone_hash: str,
            _origen_ref: list[str] | None = None,
        ) -> tuple[str, Intent]:
            return "Respuesta original no comprendida.", "desconocido"

        def fake_transcribe(
            _self: object,
            _audio_path: str,
        ) -> dict[str, object]:
            return {"text": "frase ambigua para probar escalada"}

        def fake_synthesize(
            _self: object,
            text: str,
            _output_dir: str | Path | None = None,
        ) -> str:
            synthesized.append(text)
            return tts_ogg

        monkeypatch.setattr(
            AgroVozPipeline,
            "_handle_alert_commands",
            staticmethod(no_alerta),
        )
        monkeypatch.setattr(
            AgroVozPipeline,
            "_generate_response",
            staticmethod(incomprensible),
        )
        monkeypatch.setattr(
            "app.services.pipeline_service.WhisperService.transcribe",
            fake_transcribe,
        )
        monkeypatch.setattr(
            "app.services.pipeline_service.TTSService.synthesize",
            fake_synthesize,
        )
        monkeypatch.setattr(pipeline_module, "retain_audio", lambda *_args: None)

        phone_hash = ("1" if entrada == "texto" else "2") * 64
        responses: list[str] = []
        pipeline = AgroVozPipeline()
        for attempt in range(1, 4):
            result = await pipeline.process(
                wav_path=None if entrada == "texto" else wav_path,
                audio_duration_ms=0 if entrada == "texto" else 1_000,
                message_id=f"escalada-{entrada}-{attempt}",
                chat_id_hash=phone_hash,
                request_id=f"req-escalada-{entrada}-{attempt}",
                texto_directo=("frase ambigua para probar escalada" if entrada == "texto" else None),
                generar_audio=entrada == "audio",
            )
            responses.append(result.text_response)

        assert "más despacio" in responses[0]
        assert "escribe tu consulta por texto" in responses[1]
        assert "no cuenta con atención humana en este chat" in responses[2]
        assert "contactar directamente" in responses[2]
        assert "PRODESAL" in responses[2]
        assert "INDAP" in responses[2]
        assert "te llamará" not in responses[2].lower()
        assert "te contactará" not in responses[2].lower()
        assert synthesized == (responses if entrada == "audio" else [])
        registry = pipeline_module._conversation_registry
        assert isinstance(registry, ConversationRegistry)
        snapshot = registry.snapshot(phone_hash)
        assert snapshot is not None
        assert snapshot.comprehension_failures == 3

    async def test_state_exito_reinicia_escalada(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Tras una respuesta válida, el próximo fallo vuelve a pedir repetición."""
        _enable_conversation_state(monkeypatch)
        _mock_first_contact(monkeypatch, is_first=False)
        _mock_db_save(monkeypatch)
        generated = iter(
            [
                ("sin comprender uno", "desconocido"),
                ("sin comprender dos", "desconocido"),
                ("La papa está a 500 pesos.", "precio"),
                ("sin comprender otra vez", "desconocido"),
            ]
        )

        async def no_alerta(
            _text: str,
            _phone_hash: str,
            _chat_id: str | None,
        ) -> tuple[None, None]:
            return None, None

        async def scripted_response(
            _text: str,
            _phone_hash: str,
            _origen_ref: list[str] | None = None,
        ) -> tuple[str, Intent]:
            response_text, intent = next(generated)
            return response_text, cast(Intent, intent)

        monkeypatch.setattr(
            AgroVozPipeline,
            "_handle_alert_commands",
            staticmethod(no_alerta),
        )
        monkeypatch.setattr(
            AgroVozPipeline,
            "_generate_response",
            staticmethod(scripted_response),
        )

        responses: list[str] = []
        for attempt in range(4):
            result = await AgroVozPipeline().process(
                wav_path=None,
                audio_duration_ms=0,
                message_id=f"reset-escalada-{attempt}",
                chat_id_hash="3" * 64,
                request_id=f"req-reset-escalada-{attempt}",
                texto_directo="frase ambigua para probar reset",
                generar_audio=False,
            )
            responses.append(result.text_response)

        assert "más despacio" in responses[0]
        assert "escribe tu consulta por texto" in responses[1]
        assert responses[2] == "La papa está a 500 pesos."
        assert "más despacio" in responses[3]
        registry = pipeline_module._conversation_registry
        assert isinstance(registry, ConversationRegistry)
        snapshot = registry.snapshot("3" * 64)
        assert snapshot is not None
        assert snapshot.comprehension_failures == 1

    async def test_state_flag_off_no_aplica_escalada(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Con el feature apagado se conserva la respuesta stateless previa."""
        _mock_first_contact(monkeypatch, is_first=False)
        _mock_db_save(monkeypatch)

        async def no_alerta(
            _text: str,
            _phone_hash: str,
            _chat_id: str | None,
        ) -> tuple[None, None]:
            return None, None

        async def incomprensible(
            _text: str,
            _phone_hash: str,
            _origen_ref: list[str] | None = None,
        ) -> tuple[str, Intent]:
            return "Respuesta stateless original.", "desconocido"

        monkeypatch.setattr(
            AgroVozPipeline,
            "_handle_alert_commands",
            staticmethod(no_alerta),
        )
        monkeypatch.setattr(
            AgroVozPipeline,
            "_generate_response",
            staticmethod(incomprensible),
        )

        result = await AgroVozPipeline().process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="escalada-off",
            chat_id_hash="4" * 64,
            request_id="req-escalada-off",
            texto_directo="frase ambigua sin feature",
            generar_audio=False,
        )

        assert result.text_response == "Respuesta stateless original."
        assert pipeline_module._conversation_registry is None

    async def test_segunda_consulta_retorna_ocupada_sin_etapas_caras(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Solo el dueño entra a las etapas; la concurrente responde al instante."""
        _enable_conversation_state(monkeypatch)
        started = asyncio.Event()
        release = asyncio.Event()
        stage_calls = 0

        async def blocked_stages(
            _pipeline: AgroVozPipeline,
            **kwargs: object,
        ) -> AudioResponse:
            nonlocal stage_calls
            stage_calls += 1
            lease = kwargs.get("conversation_lease")
            assert isinstance(lease, ConversationLease)
            started.set()
            await release.wait()
            assert lease.transition(ConversationState.RESPONDIENDO)
            return AudioResponse(
                audio_path="",
                text_response="respuesta del dueño",
                latency_ms=1,
                intent="precio",
            )

        monkeypatch.setattr(
            AgroVozPipeline,
            "_process_stages",
            blocked_stages,
        )
        pipeline = AgroVozPipeline()
        owner_task = asyncio.create_task(
            pipeline.process(
                wav_path=None,
                audio_duration_ms=0,
                message_id="state-owner",
                chat_id_hash=_VALID_STATE_HASH,
                request_id="state-owner-request",
                texto_directo="precio de la papa",
                generar_audio=False,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)

        busy = await pipeline.process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="state-busy",
            chat_id_hash=_VALID_STATE_HASH,
            request_id="state-busy-request",
            texto_directo="clima de mañana",
            generar_audio=False,
        )

        assert busy.text_response == _CONVERSATION_BUSY_TEXT
        assert "consulta en proceso" in busy.text_response
        assert busy.audio_path == ""
        assert busy.consultation_id is None
        assert stage_calls == 1

        release.set()
        owner = await owner_task
        assert owner.text_response == "respuesta del dueño"

    async def test_timeout_retira_lease_y_permite_nuevo_turno(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """El timeout técnico no bloquea al productor por el timeout conversacional."""
        _enable_conversation_state(monkeypatch)

        async def never_finishes(
            _pipeline: AgroVozPipeline,
            **_kwargs: object,
        ) -> AudioResponse:
            await asyncio.sleep(999)
            raise AssertionError("inalcanzable")

        monkeypatch.setattr(
            AgroVozPipeline,
            "_process_stages",
            never_finishes,
        )
        result = await AgroVozPipeline(pipeline_timeout=0.01).process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="state-timeout",
            chat_id_hash=_VALID_STATE_HASH,
            request_id="state-timeout-request",
            texto_directo="precio de la papa",
            generar_audio=False,
        )

        assert result.intent == "desconocido"
        registry = pipeline_module._conversation_registry
        assert isinstance(registry, ConversationRegistry)
        assert registry.snapshot(_VALID_STATE_HASH) is None
        replacement = registry.claim(_VALID_STATE_HASH)
        assert replacement.status == TransitionStatus.APLICADA
        assert replacement.lease is not None
        assert replacement.lease.abort()

    async def test_timeout_conversacional_reinicia_sesion_vieja(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """El registry configurado reemplaza una lease vencida sin heredar turno."""
        _enable_conversation_state(monkeypatch, timeout_minutes=1)
        now = [0.0]

        def controlled_clock() -> float:
            return now[0]

        registry = ConversationRegistry(
            timeout_minutes=settings.conversation_timeout_minutes,
            clock=controlled_clock,
        )
        monkeypatch.setattr(
            pipeline_module,
            "_conversation_registry",
            registry,
        )
        stale = registry.claim(_VALID_STATE_HASH)
        assert stale.lease is not None
        now[0] = 60.0

        async def successful_stages(
            _pipeline: AgroVozPipeline,
            **kwargs: object,
        ) -> AudioResponse:
            lease = kwargs.get("conversation_lease")
            assert isinstance(lease, ConversationLease)
            assert lease.transition(ConversationState.RESPONDIENDO)
            return AudioResponse(
                audio_path="",
                text_response="respuesta nueva",
                latency_ms=1,
                intent="precio",
            )

        monkeypatch.setattr(
            AgroVozPipeline,
            "_process_stages",
            successful_stages,
        )
        result = await AgroVozPipeline().process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="state-expired",
            chat_id_hash=_VALID_STATE_HASH,
            request_id="state-expired-request",
            texto_directo="precio de la papa",
            generar_audio=False,
        )

        assert result.text_response == "respuesta nueva"
        assert not stale.lease.abort()
        snapshot = registry.snapshot(_VALID_STATE_HASH)
        assert snapshot is not None
        assert snapshot.state == ConversationState.ESPERANDO_CONSULTA
        assert snapshot.turn_count == 1

    async def test_cancelacion_retira_lease_duena(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cancelar el task libera el hash aunque no venza el timeout."""
        _enable_conversation_state(monkeypatch)
        started = asyncio.Event()

        async def blocked_stages(
            _pipeline: AgroVozPipeline,
            **_kwargs: object,
        ) -> AudioResponse:
            started.set()
            await asyncio.sleep(999)
            raise AssertionError("inalcanzable")

        monkeypatch.setattr(
            AgroVozPipeline,
            "_process_stages",
            blocked_stages,
        )
        task = asyncio.create_task(
            AgroVozPipeline().process(
                wav_path=None,
                audio_duration_ms=0,
                message_id="state-cancel",
                chat_id_hash=_VALID_STATE_HASH,
                request_id="state-cancel-request",
                texto_directo="precio de la papa",
                generar_audio=False,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        registry = pipeline_module._conversation_registry
        assert isinstance(registry, ConversationRegistry)
        assert registry.snapshot(_VALID_STATE_HASH) is None

    async def test_excepcion_retira_solo_sesion_duena(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Una excepción inesperada libera el hash mediante el finally."""
        _enable_conversation_state(monkeypatch)

        async def fail_stages(
            _pipeline: AgroVozPipeline,
            **_kwargs: object,
        ) -> AudioResponse:
            raise RuntimeError("fallo controlado")

        monkeypatch.setattr(
            AgroVozPipeline,
            "_process_stages",
            fail_stages,
        )

        with pytest.raises(RuntimeError, match="fallo controlado"):
            await AgroVozPipeline().process(
                wav_path=None,
                audio_duration_ms=0,
                message_id="state-error",
                chat_id_hash=_VALID_STATE_HASH,
                request_id="state-error-request",
                texto_directo="precio de la papa",
                generar_audio=False,
            )

        registry = pipeline_module._conversation_registry
        assert isinstance(registry, ConversationRegistry)
        assert registry.snapshot(_VALID_STATE_HASH) is None

    async def test_hash_invalido_no_crea_tracking(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Identidades no HMAC continúan stateless sin crear registro."""
        _enable_conversation_state(monkeypatch)
        leases: list[object] = []

        async def simple_stages(
            _pipeline: AgroVozPipeline,
            **kwargs: object,
        ) -> AudioResponse:
            leases.append(kwargs.get("conversation_lease"))
            return AudioResponse(
                audio_path="",
                text_response="respuesta stateless",
                latency_ms=1,
                intent="desconocido",
            )

        monkeypatch.setattr(
            AgroVozPipeline,
            "_process_stages",
            simple_stages,
        )

        result = await AgroVozPipeline().process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="state-invalid",
            chat_id_hash="sin_chat",
            request_id="state-invalid-request",
            texto_directo="hola",
            generar_audio=False,
        )

        assert result.text_response == "respuesta stateless"
        assert leases == [None]
        assert pipeline_module._conversation_registry is None

    # ── Happy path ──────────────────────────────────────────────

    async def test_happy_path_precio(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
    ) -> None:
        """Pipeline completo: Whisper → LLM → TTS con consulta de precio."""
        _mock_whisper_transcribe(monkeypatch)
        _mock_llm_answer(monkeypatch, "La papa esta a 450 pesos el kilo en Lo Valledor")
        _mock_tts_synthesize(monkeypatch, tts_ogg)
        save_calls = _mock_db_save(monkeypatch)

        pipeline = AgroVozPipeline()
        result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=3000,
            message_id="test-msg-001",
            chat_id_hash="abc123def456",
            request_id="req-happy-path",
        )

        assert isinstance(result, AudioResponse)
        assert result.audio_path == tts_ogg
        assert "450" in result.text_response
        assert result.intent == "precio"
        assert result.consultation_id == 42
        assert result.whisper_ms >= 0
        assert result.llm_ms >= 0
        assert result.tts_ms >= 0
        assert result.latency_ms >= 0

        # Verificar que se guardo la consulta
        assert len(save_calls) == 1
        assert save_calls[0]["intent"] == "precio"
        assert save_calls[0]["phone_hash"] == "abc123def456"

    async def test_producto_se_guarda_en_consulta(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
    ) -> None:
        """El campo producto se guarda correctamente en la consulta."""

        # Mock Whisper que transcribe texto con producto.
        def fake_transcribe_papa(_self: object, audio_path: str) -> dict[str, object]:
            return {
                "text": "precio de la papa en lo valledor",
                "language": "es",
                "segments": [],
                "duration_ms": 1200,
            }

        monkeypatch.setattr(
            "app.services.pipeline_service.WhisperService.transcribe",
            fake_transcribe_papa,
        )
        _mock_llm_answer(monkeypatch, "La papa esta a 450 pesos el kilo")
        _mock_tts_synthesize(monkeypatch, tts_ogg)
        save_calls = _mock_db_save(monkeypatch)

        pipeline = AgroVozPipeline()
        result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=3000,
            message_id="test-producto",
            chat_id_hash="hash-producto",
            request_id="req-producto",
        )

        assert result.intent == "precio"
        assert len(save_calls) == 1
        assert save_calls[0]["producto"] == "papa"

    async def test_producto_none_sin_producto_detectado(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
    ) -> None:
        """Si no se detecta producto, se guarda None."""

        def fake_transcribe_clima(_self: object, audio_path: str) -> dict[str, object]:
            return {
                "text": "clima en traiguen",
                "language": "es",
                "segments": [],
                "duration_ms": 1200,
            }

        monkeypatch.setattr(
            "app.services.pipeline_service.WhisperService.transcribe",
            fake_transcribe_clima,
        )
        _mock_llm_answer(monkeypatch, "En Traiguen hay 8 grados con lluvia")
        _mock_tts_synthesize(monkeypatch, tts_ogg)
        save_calls = _mock_db_save(monkeypatch)

        pipeline = AgroVozPipeline()
        await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=2500,
            message_id="test-no-producto",
            chat_id_hash="hash-no-producto",
            request_id="req-no-producto",
        )

        assert len(save_calls) == 1
        assert save_calls[0]["producto"] is None

    # ── Comando resumen ──────────────────────────────────────────

    async def test_resumen_keyword_llama_summary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
    ) -> None:
        """Keyword 'resumen' llama a summary_service y saltea el LLM."""

        # Mock Whisper que transcribe "resumen".
        def fake_transcribe_resumen(_self: object, audio_path: str) -> dict[str, object]:
            return {
                "text": "resumen",
                "language": "es",
                "segments": [],
                "duration_ms": 800,
            }

        monkeypatch.setattr(
            "app.services.pipeline_service.WhisperService.transcribe",
            fake_transcribe_resumen,
        )
        _mock_tts_synthesize(monkeypatch, tts_ogg)
        save_calls = _mock_db_save(monkeypatch)

        # Mock summary_service.get_consultation_summary (funcion sincrona).
        def fake_summary_text(session: object, phone_hash: str) -> str:
            return "Este mes consultaste 5 veces. Tu producto mas consultado fue papa."

        monkeypatch.setattr(
            "app.services.summary_service.get_consultation_summary",
            fake_summary_text,
        )

        # LLM NO deberia llamarse.
        llm_called = False

        async def fake_answer_check(_query: str, **kwargs: object) -> str:
            nonlocal llm_called
            llm_called = True
            return "no deberia llamarse"

        monkeypatch.setattr("app.services.llm_service.answer", fake_answer_check)

        pipeline = AgroVozPipeline()
        result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=1000,
            message_id="test-resumen",
            chat_id_hash="hash-resumen",
            request_id="req-resumen",
        )

        assert not llm_called  # LLM NO se llamo
        assert result.intent == "resumen"
        assert "5 veces" in result.text_response
        assert result.audio_path == tts_ogg

        # Verificar que se guardo la consulta con intent "resumen".
        assert len(save_calls) == 1
        assert save_calls[0]["intent"] == "resumen"

    async def test_reporte_pdf_entrega_path_temporal_desde_pipeline(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """El pedido explícito evita el LLM y deja el PDF para AudioService."""
        _mock_first_contact(monkeypatch, is_first=False)
        monkeypatch.setattr(settings, "pdf_reports_enabled", True)
        save_calls = _mock_db_save(monkeypatch)
        report_path = tmp_path / "agrovoz-test-reporte.pdf"
        report_path.write_bytes(b"%PDF-test")

        async def no_alerta(
            _text: str,
            _phone_hash: str,
            _chat_id: str | None,
        ) -> tuple[None, None]:
            return None, None

        async def fake_report(_phone_hash: str) -> tuple[str, str]:
            return "Listo, te envío el reporte semanal en PDF.", str(report_path)

        monkeypatch.setattr(
            AgroVozPipeline,
            "_handle_alert_commands",
            staticmethod(no_alerta),
        )
        monkeypatch.setattr(
            AgroVozPipeline,
            "_generate_report_response",
            staticmethod(fake_report),
        )

        result = await AgroVozPipeline().process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="test-reporte-pdf",
            chat_id_hash="f" * 64,
            request_id="req-reporte-pdf",
            texto_directo="mándame un resumen de la semana",
            generar_audio=False,
        )

        assert result.text_response == "Listo, te envío el reporte semanal en PDF."
        assert result.intent == "resumen"
        assert result.report_pdf_path == str(report_path)
        assert save_calls[0]["intent"] == "resumen"
        report_path.unlink()

    async def test_informe_con_gate_apagado_no_intercepta_consulta_normal(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Con PDF_REPORTS_ENABLED=false, 'informe' no debe secuestrar el flujo normal.

        _is_reporte_pdf_query matchea la palabra suelta 'informe', que aparece en
        preguntas comunes ('puedes informarme el precio de la papa'). Sin el gate
        activo, el pipeline debe caer al flujo normal (LLM/precio) en vez de
        responder 'Los reportes PDF todavía no están habilitados' — regresión de
        PR #257 encontrada en revisión de seguridad.
        """
        _mock_first_contact(monkeypatch, is_first=False)
        _mock_db_save(monkeypatch)
        monkeypatch.setattr(settings, "pdf_reports_enabled", False)

        async def no_alerta(
            _text: str,
            _phone_hash: str,
            _chat_id: str | None,
        ) -> tuple[None, None]:
            return None, None

        monkeypatch.setattr(
            AgroVozPipeline,
            "_handle_alert_commands",
            staticmethod(no_alerta),
        )
        _mock_generated_response(monkeypatch, response_text="La papa está a 500 pesos el kilo.")

        async def fail_if_called(_phone_hash: str) -> tuple[str, str | None]:
            raise AssertionError("no debe generar el reporte con el gate apagado")

        monkeypatch.setattr(
            AgroVozPipeline,
            "_generate_report_response",
            staticmethod(fail_if_called),
        )

        result = await AgroVozPipeline().process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="test-informe-gate-apagado",
            chat_id_hash="e" * 64,
            request_id="req-informe-gate-apagado",
            texto_directo="mándame un informe de precios del trigo",
            generar_audio=False,
        )

        assert result.text_response == "La papa está a 500 pesos el kilo."
        assert result.report_pdf_path is None

    async def test_timeout_despues_de_generar_reporte_elimina_pdf(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """El pipeline conserva ownership del PDF hasta devolver AudioResponse."""
        _mock_first_contact(monkeypatch, is_first=False)
        monkeypatch.setattr(settings, "pdf_reports_enabled", True)
        report_path = tmp_path / "reporte-huerfano.pdf"
        report_path.write_bytes(b"%PDF-test")

        async def no_alerta(
            _text: str,
            _phone_hash: str,
            _chat_id: str | None,
        ) -> tuple[None, None]:
            return None, None

        async def fake_report(_phone_hash: str) -> tuple[str, str]:
            return "Listo, te envío el reporte semanal en PDF.", str(report_path)

        class SlowTTS:
            def synthesize(self, _text: str) -> str:
                time.sleep(0.1)
                return str(tmp_path / "respuesta-tardia.ogg")

        monkeypatch.setattr(AgroVozPipeline, "_handle_alert_commands", staticmethod(no_alerta))
        monkeypatch.setattr(AgroVozPipeline, "_generate_report_response", staticmethod(fake_report))
        monkeypatch.setattr(pipeline_module, "_get_tts_service", lambda: SlowTTS())

        result = await AgroVozPipeline(pipeline_timeout=0.01).process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="test-reporte-timeout",
            chat_id_hash="f" * 64,
            request_id="req-reporte-timeout",
            texto_directo="mándame el reporte semanal",
        )

        assert result.report_pdf_path is None
        assert not report_path.exists()

    async def test_tool_llm_transfiere_intent_al_generador_de_pdf(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Una tool seleccionada por Qwen produce un adjunto, no una promesa."""
        from app.services.report_service import REPORT_TOOL_SIGNAL

        report_path = tmp_path / "reporte-tool.pdf"
        report_path.write_bytes(b"%PDF-test")
        _mock_first_contact(monkeypatch, is_first=False)
        _mock_db_save(monkeypatch)

        async def fake_answer(*_args: object, **_kwargs: object) -> str:
            return REPORT_TOOL_SIGNAL

        async def fake_report(_phone_hash: str) -> tuple[str, str]:
            return "Listo, te envío el reporte semanal en PDF.", str(report_path)

        monkeypatch.setattr("app.services.llm_service.answer", fake_answer)
        monkeypatch.setattr(AgroVozPipeline, "_load_user_cultivos", staticmethod(lambda _hash: None))
        monkeypatch.setattr(AgroVozPipeline, "_generate_report_response", staticmethod(fake_report))

        result = await AgroVozPipeline().process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="test-reporte-tool",
            chat_id_hash="f" * 64,
            request_id="req-reporte-tool",
            texto_directo="necesito el documento que ofreciste ayer",
            generar_audio=False,
        )

        assert result.text_response == "Listo, te envío el reporte semanal en PDF."
        assert result.intent == "resumen"
        assert result.report_pdf_path == str(report_path)
        report_path.unlink()

    async def test_historial_explicito_es_compartido_por_audio_y_texto(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
    ) -> None:
        """Ambas entradas convergen en el mismo lector y respuesta contextual."""
        history_request = "qué pregunté antes"
        reader_calls: list[str] = []

        def fake_transcribe_history(
            _self: object,
            _audio_path: str,
        ) -> dict[str, object]:
            return {
                "text": history_request,
                "language": "es",
                "segments": [],
                "duration_ms": 800,
            }

        def fake_reader(phone_hash: str) -> LatestConsultationContext:
            reader_calls.append(phone_hash)
            return LatestConsultationContext(
                query_text="precio de la papa",
                response_text="La papa estaba a 500 pesos el kilo.",
                intent="precio",
                producto="papa",
            )

        async def no_alert(
            _text: str,
            _phone_hash: str,
            _chat_id: str | None,
        ) -> tuple[None, None]:
            return None, None

        async def fail_llm(*_args: object, **_kwargs: object) -> str:
            raise AssertionError("Audio y texto deben evitar el LLM")

        monkeypatch.setattr(
            "app.services.pipeline_service.WhisperService.transcribe",
            fake_transcribe_history,
        )
        monkeypatch.setattr(
            "app.services.consultation_history_service.get_latest_consultation_context",
            fake_reader,
        )
        monkeypatch.setattr(
            AgroVozPipeline,
            "_handle_alert_commands",
            staticmethod(no_alert),
        )
        monkeypatch.setattr(
            "app.services.pipeline_service.retain_audio",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr("app.services.llm_service.answer", fail_llm)
        _mock_first_contact(monkeypatch, is_first=False)
        _mock_tts_synthesize(monkeypatch, tts_ogg)
        save_calls = _mock_db_save(monkeypatch)
        pipeline = AgroVozPipeline()

        audio_result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=1_000,
            message_id="history-audio",
            chat_id_hash="history-phone-hash",
            request_id="history-audio-request",
        )
        text_result = await pipeline.process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="history-text",
            chat_id_hash="history-phone-hash",
            request_id="history-text-request",
            texto_directo=history_request,
            generar_audio=False,
        )

        assert audio_result.text_response == text_result.text_response
        assert audio_result.intent == text_result.intent == "resumen"
        assert audio_result.audio_path == tts_ogg
        assert text_result.audio_path == ""
        assert reader_calls == ["history-phone-hash", "history-phone-hash"]
        assert [call["intent"] for call in save_calls] == ["resumen", "resumen"]

    async def test_happy_path_clima(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
    ) -> None:
        """Pipeline completo: Whisper → LLM → TTS con consulta de clima.

        Este test cubre la RUTA LLM. Una consulta de clima simple normalmente
        toma el fast-path determinista, asi que se anula devolviendo None desde
        _force_keyword_tool: es la condicion real en que el pipeline cae al LLM
        (keywords sin match). El fast-path tiene sus propios tests.
        """

        # Usar texto transcrito con keyword de clima
        def fake_transcribe_clima(_self: object, audio_path: str) -> dict[str, object]:
            return {
                "text": "como esta el clima en traiguen",
                "language": "es",
                "segments": [],
                "duration_ms": 1200,
            }

        monkeypatch.setattr(
            "app.services.pipeline_service.WhisperService.transcribe",
            fake_transcribe_clima,
        )

        async def sin_keyword_match(*_args: object, **_kwargs: object) -> None:
            return None

        monkeypatch.setattr("app.services.llm_keywords._force_keyword_tool", sin_keyword_match)
        _mock_llm_answer(monkeypatch, "En Traiguen hay 8 grados con lluvia ligera")
        _mock_tts_synthesize(monkeypatch, tts_ogg)
        _mock_db_save(monkeypatch)

        pipeline = AgroVozPipeline()
        result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=2500,
            message_id="test-msg-002",
            chat_id_hash="def456abc789",
            request_id="req-clima",
        )

        assert result.intent == "clima"
        assert "grados" in result.text_response.lower()
        assert result.audio_path == tts_ogg

    async def test_fast_path_responde_sin_invocar_al_llm(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
    ) -> None:
        """Consulta simple de precio: responde con la tool y NO llama al LLM.

        Es la razon de ser del fast-path — el LLM en CPU limitada se va casi
        todo el presupuesto de latencia leyendo el prompt de tools.
        """

        def fake_transcribe(_self: object, audio_path: str) -> dict[str, object]:
            return {
                "text": "a cuanto esta la papa",
                "language": "es",
                "segments": [],
                "duration_ms": 1200,
            }

        monkeypatch.setattr("app.services.pipeline_service.WhisperService.transcribe", fake_transcribe)

        async def keyword_tool(*_args: object, **_kwargs: object) -> str:
            return "La papa está a 520 pesos el kilo, según ODEPA."

        monkeypatch.setattr("app.services.llm_keywords._force_keyword_tool", keyword_tool)

        llamadas_llm: list[str] = []

        async def llm_no_deberia_correr(*args: object, **_kwargs: object) -> str:
            llamadas_llm.append(str(args[0]) if args else "")
            return "respuesta del LLM"

        monkeypatch.setattr("app.services.llm_service.answer", llm_no_deberia_correr)
        _mock_tts_synthesize(monkeypatch, tts_ogg)
        _mock_db_save(monkeypatch)

        pipeline = AgroVozPipeline()
        result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=2500,
            message_id="test-msg-fast",
            chat_id_hash="abc123def456",
            request_id="req-fast",
        )

        assert llamadas_llm == [], "el fast-path no debe invocar al LLM"
        assert result.intent == "precio"
        assert "520 pesos" in result.text_response

    async def test_fallback_desconocido(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
    ) -> None:
        """Pipeline con consulta fuera de scope → intent desconocido."""

        def fake_transcribe_out(_self: object, audio_path: str) -> dict[str, object]:
            return {
                "text": "hola como estas",
                "language": "es",
                "segments": [],
                "duration_ms": 800,
            }

        monkeypatch.setattr(
            "app.services.pipeline_service.WhisperService.transcribe",
            fake_transcribe_out,
        )
        _mock_llm_answer(
            monkeypatch,
            "Hola, soy AgroVoz. Soy un asistente de voz para agricultores.",
        )
        _mock_tts_synthesize(monkeypatch, tts_ogg)
        _mock_db_save(monkeypatch)

        pipeline = AgroVozPipeline()
        result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=1500,
            message_id="test-msg-003",
            chat_id_hash="ghi789jkl012",
            request_id="req-fallback",
        )

        assert result.intent == "desconocido"
        assert len(result.text_response) > 0
        assert result.audio_path == tts_ogg

    # ── Timeout ─────────────────────────────────────────────────

    async def test_pipeline_timeout(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
    ) -> None:
        """Pipeline que excede timeout de 20s retorna AudioResponse con error."""
        _mock_whisper_transcribe(monkeypatch)

        # LLM mock que se demora mas que el timeout
        async def fake_answer_slow(_query: str, phone_hash: str | None = None, **kwargs: object) -> str:
            await asyncio.sleep(999.0)  # Nunca termina
            return "muy tarde"

        monkeypatch.setattr("app.services.llm_service.answer", fake_answer_slow)
        async def fake_force_none(_query: str, phone_hash: str | None = None) -> None:
            return None

        # El timeout debe probar el proveedor LLM, no depender de que el
        # fast-path de precio sin datos encuentre o no registros en la DB.
        monkeypatch.setattr("app.services.llm_keywords._force_keyword_tool", fake_force_none)
        _mock_db_save(monkeypatch)

        # Usar un timeout MUY corto para el test (0.1s)
        pipeline = AgroVozPipeline(pipeline_timeout=0.1)
        result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=3000,
            message_id="test-timeout",
            chat_id_hash="hash-timeout",
            request_id="req-timeout",
        )

        assert isinstance(result, AudioResponse)
        assert result.intent == "desconocido"
        assert "tiempo" in result.text_response.lower() or "responder" in result.text_response.lower()
        assert result.audio_path == ""
        assert result.consultation_id is None

    # ── Audio demasiado largo ───────────────────────────────────

    async def test_audio_largo_omite_whisper(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
    ) -> None:
        """Audio > _MAX_WHISPER_AUDIO_MS omite transcripcion Whisper."""
        whisper_called = False

        def fake_transcribe_count(_self: object, audio_path: str) -> dict[str, object]:
            nonlocal whisper_called
            whisper_called = True
            return {"text": "no deberia llamarse", "language": "es", "segments": [], "duration_ms": 0}

        monkeypatch.setattr(
            "app.services.pipeline_service.WhisperService.transcribe",
            fake_transcribe_count,
        )
        _mock_llm_answer(monkeypatch, "respuesta dummy")
        _mock_tts_synthesize(monkeypatch, tts_ogg)
        _mock_db_save(monkeypatch)

        pipeline = AgroVozPipeline()
        result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=_MAX_WHISPER_AUDIO_MS + 1,
            message_id="test-largo",
            chat_id_hash="hash-largo",
            request_id="req-largo",
        )

        assert not whisper_called  # Whisper NO se llamo
        # Sin transcripcion, response_text queda vacio → TTS no sintetiza → audio_path vacio
        assert result.audio_path == ""
        assert result.intent == "desconocido"

    # ── Whisper falla ───────────────────────────────────────────

    async def test_whisper_runtime_error_continua(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
    ) -> None:
        """Si Whisper lanza RuntimeError, pipeline continua con respuesta generica.

        Fix #105: se guarda un stub consultation para que _is_first_contact()
        retorne False en el siguiente audio (evita bienvenida repetida).
        """

        def fake_transcribe_err(_self: object, audio_path: str) -> dict[str, object]:
            raise RuntimeError("Whisper OOM")

        monkeypatch.setattr(
            "app.services.pipeline_service.WhisperService.transcribe",
            fake_transcribe_err,
        )
        # LLM NO deberia llamarse porque no hay texto transcrito
        llm_called = False

        async def fake_answer_check(_query: str, phone_hash: str | None = None, **kwargs: object) -> str:
            nonlocal llm_called
            llm_called = True
            return "no deberia generarse"

        monkeypatch.setattr("app.services.llm_service.answer", fake_answer_check)
        _mock_tts_synthesize(monkeypatch, tts_ogg)
        save_calls = _mock_db_save(monkeypatch)

        pipeline = AgroVozPipeline()
        result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=3000,
            message_id="test-whisper-fail",
            chat_id_hash="hash-whisper-fail",
            request_id="req-whisper-fail",
        )

        assert not llm_called
        # Sin texto transcrito, response_text queda vacio → TTS no sintetiza → audio_path vacio
        assert result.audio_path == ""
        assert result.intent == "desconocido"
        # Fix #105: se guarda stub consultation incluso cuando Whisper falla.
        # Esto previene que _is_first_contact retorne True en el siguiente audio
        # (bienvenida repetida).
        assert len(save_calls) == 1
        assert save_calls[0]["query_text"] == ""
        assert save_calls[0]["intent"] == "desconocido"

    # ── TTS falla ───────────────────────────────────────────────

    async def test_tts_falla_retorna_audio_vacio(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
    ) -> None:
        """Si TTS lanza excepcion, pipeline retorna audio_path vacio (fallback a hello.ogg en audio_service)."""
        _mock_whisper_transcribe(monkeypatch)
        _mock_llm_answer(monkeypatch, "La papa cuesta 500 pesos el kilo")
        _mock_tts_fail(monkeypatch)
        _mock_db_save(monkeypatch)

        pipeline = AgroVozPipeline()
        result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=3000,
            message_id="test-tts-fail",
            chat_id_hash="hash-tts-fail",
            request_id="req-tts-fail",
        )

        assert result.text_response != ""  # LLM genero respuesta
        assert result.audio_path == ""  # TTS fallo
        assert result.intent == "precio"  # Intent detectado de la respuesta
        assert result.tts_ms == 0

    # ── Whisper cold-load timeout ──────────────────────────────

    async def test_whisper_timeout_no_rompe_pipeline(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
    ) -> None:
        """Si Whisper excede el timeout, pipeline continua sin transcripcion.

        Regression test: el timeout aumento de 30s a 60s para cubrir
        cold-load del modelo (~32s en CPU). Si el timeout se supera
        igual, el pipeline debe continuar elegantemente (no crashear).
        """
        # Reducir WHISPER_TIMEOUT a un valor tiny para el test
        monkeypatch.setattr(
            "app.services.pipeline_service._WHISPER_TIMEOUT",
            0.05,
        )

        # Mock Whisper que se demora mas que el timeout reducido
        def fake_transcribe_slow(_self: object, audio_path: str) -> dict[str, object]:
            import time

            time.sleep(0.1)  # > 0.05s timeout
            return _fake_whisper_output()

        monkeypatch.setattr(
            "app.services.pipeline_service.WhisperService.transcribe",
            fake_transcribe_slow,
        )

        # LLM NO deberia llamarse porque no hay texto transcrito
        llm_called = False

        async def fake_answer_check(_query: str, phone_hash: str | None = None, **kwargs: object) -> str:
            nonlocal llm_called
            llm_called = True
            return "no deberia generarse"

        monkeypatch.setattr("app.services.llm_service.answer", fake_answer_check)
        _mock_db_save(monkeypatch)

        pipeline = AgroVozPipeline()
        result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=3000,
            message_id="test-whisper-slow",
            chat_id_hash="hash-whisper-slow",
            request_id="req-whisper-slow",
        )

        assert not llm_called, "LLM no debe llamarse si Whisper fallo"
        assert result.audio_path == ""
        assert result.intent == "desconocido"

    # ── Benchmark ───────────────────────────────────────────────

    async def test_benchmark_tiene_metricas_por_etapa(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
    ) -> None:
        """AudioResponse incluye metricas de latencia por etapa."""
        _mock_whisper_transcribe(monkeypatch)
        _mock_llm_answer(monkeypatch, "respuesta de benchmark")
        _mock_tts_synthesize(monkeypatch, tts_ogg)
        _mock_db_save(monkeypatch)

        pipeline = AgroVozPipeline()
        result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=2000,
            message_id="test-benchmark",
            chat_id_hash="hash-bench",
            request_id="req-bench",
        )

        assert result.whisper_ms >= 0
        assert result.llm_ms >= 0
        assert result.tts_ms >= 0
        assert result.latency_ms >= result.whisper_ms
        assert result.latency_ms >= result.llm_ms
        assert result.latency_ms >= result.tts_ms


# ── _save_consultation ─────────────────────────────────────────────


class TestSaveConsultation:
    """Persistencia de consulta en SQLite."""

    def test_save_exitoso(self, caplog: pytest.LogCaptureFixture) -> None:
        """Sin feature de historial persiste métricas, pero no contenido."""
        caplog.set_level(logging.DEBUG, logger="app.services.pipeline_service")
        with (
            patch.object(settings, "consultation_history_enabled", False),
            patch("app.core.database.SessionLocal") as mock_factory,
        ):
            mock_session = mock_factory.return_value
            mock_session.commit.side_effect = lambda: setattr(mock_session.add.call_args.args[0], "id", 42)
            start = time.monotonic()
            consultation_id = AgroVozPipeline._save_consultation(
                phone_hash="test_hash_123",
                intent="precio",
                query_text="precio de la papa",
                response_text="450 pesos",
                audio_duration_ms=3500,
                start_time=start,
            )
            # Verifica que la consulta se persistio en DB.
            mock_session.add.assert_called_once()
            mock_session.commit.assert_called_once()
            assert consultation_id == 42
            consulta = mock_session.add.call_args.args[0]
            assert consulta.query_text == ""
            assert consulta.response_text == ""
            mock_session.scalar.assert_not_called()
            assert "consultation_id=42" in caplog.text

    def test_save_con_consentimiento_guarda_contenido_transitorio(self) -> None:
        """El opt-in específico habilita staging hasta confirmar la entrega."""
        with (
            patch.object(settings, "consultation_history_enabled", True),
            patch("app.core.database.SessionLocal") as mock_factory,
        ):
            mock_session = mock_factory.return_value
            mock_session.scalar.return_value = True

            AgroVozPipeline._save_consultation(
                phone_hash="a" * 64,
                intent="precio",
                query_text="precio de la papa",
                response_text="450 pesos",
                audio_duration_ms=3500,
                start_time=time.monotonic(),
            )

        consulta = mock_session.add.call_args.args[0]
        assert consulta.query_text == "precio de la papa"
        assert consulta.response_text == "450 pesos"
        mock_session.scalar.assert_called_once()

    def test_save_sin_consentimiento_redacta_contenido(self) -> None:
        """El feature global jamás reemplaza el consentimiento individual."""
        with (
            patch.object(settings, "consultation_history_enabled", True),
            patch("app.core.database.SessionLocal") as mock_factory,
        ):
            mock_session = mock_factory.return_value
            mock_session.scalar.return_value = False

            AgroVozPipeline._save_consultation(
                phone_hash="b" * 64,
                intent="clima",
                query_text="clima en Traiguén",
                response_text="8 grados",
                audio_duration_ms=2000,
                start_time=time.monotonic(),
            )

        consulta = mock_session.add.call_args.args[0]
        assert consulta.query_text == ""
        assert consulta.response_text == ""

    def test_intencion_no_elegible_nunca_guarda_contenido(self) -> None:
        """Una respuesta meta no requiere staging aunque exista opt-in."""
        with (
            patch.object(settings, "consultation_history_enabled", True),
            patch("app.core.database.SessionLocal") as mock_factory,
        ):
            mock_session = mock_factory.return_value

            AgroVozPipeline._save_consultation(
                phone_hash="c" * 64,
                intent="resumen",
                query_text="qué pregunté antes",
                response_text="Tu consulta anterior fue...",
                audio_duration_ms=0,
                start_time=time.monotonic(),
            )

        consulta = mock_session.add.call_args.args[0]
        assert consulta.query_text == ""
        assert consulta.response_text == ""
        mock_session.scalar.assert_not_called()

    def test_save_error_no_propaga_ni_filtra_datos(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """El error DB no expone contenido, excepción ni hash operacional."""
        secreto = "CONTENIDO-PRIVADO-DB-NO-LOGUEAR"
        phone_hash = "prefijohash-db-privado"
        caplog.set_level(logging.DEBUG, logger="app.services.pipeline_service")
        with patch("app.core.database.SessionLocal") as mock_session:
            mock_session.side_effect = SQLAlchemyError(f"{secreto} {phone_hash}")
            start = time.monotonic()
            # No debe lanzar excepcion
            consultation_id = AgroVozPipeline._save_consultation(
                phone_hash=phone_hash,
                intent="clima",
                query_text=secreto,
                response_text=f"respuesta {secreto}",
                audio_duration_ms=2000,
                start_time=start,
            )
            assert consultation_id is None
            assert "intent=clima" in caplog.text
            _assert_datos_sensibles_ausentes(
                caplog,
                secreto,
                phone_hash,
                phone_hash[:8],
            )

    def test_save_con_producto(self) -> None:
        """_save_consultation guarda el campo producto correctamente."""
        with patch("app.core.database.SessionLocal") as mock_factory:
            mock_session = mock_factory.return_value
            start = time.monotonic()
            AgroVozPipeline._save_consultation(
                phone_hash="test_hash_prod",
                intent="precio",
                query_text="precio de la papa",
                response_text="450 pesos",
                audio_duration_ms=3500,
                start_time=start,
                producto="papa",
            )
            # Verifica que la consulta se persistio con producto.
            mock_session.add.assert_called_once()
            consulta = mock_session.add.call_args[0][0]
            assert consulta.producto == "papa"
            mock_session.commit.assert_called_once()

    def test_save_sin_producto(self) -> None:
        """_save_consultation guarda producto=None si no se detecta."""
        with patch("app.core.database.SessionLocal") as mock_factory:
            mock_session = mock_factory.return_value
            start = time.monotonic()
            AgroVozPipeline._save_consultation(
                phone_hash="test_hash_no_prod",
                intent="clima",
                query_text="clima en traiguen",
                response_text="8 grados",
                audio_duration_ms=2000,
                start_time=start,
                producto=None,
            )
            mock_session.add.assert_called_once()
            consulta = mock_session.add.call_args[0][0]
            assert consulta.producto is None

    def test_save_consultation_retry_en_fallo_transitorio(self) -> None:
        """Si el primer commit falla con SQLAlchemyError, reintenta con conexion nueva.

        Regression B-11: NullPool + retry en _save_consultation.
        Verifica que el retry use una conexion fresca y persista en el segundo intento.
        """
        with patch("app.core.database.SessionLocal") as mock_factory:
            first_session = Mock()
            first_session.commit.side_effect = SQLAlchemyError("DB caida transitorio")
            second_session = Mock()
            second_session.commit.return_value = None

            mock_factory.side_effect = [first_session, second_session]

            start = time.monotonic()

            AgroVozPipeline._save_consultation(
                phone_hash="test_retry_hash",
                intent="precio",
                query_text="precio de la papa",
                response_text="450 pesos",
                audio_duration_ms=3500,
                start_time=start,
                producto="papa",
            )

        # 1. SessionLocal() se llama DOS veces (conexiones distintas via NullPool)
        assert mock_factory.call_count == 2, f"SessionLocal call_count={mock_factory.call_count}, esperado 2"

        # 2. Primer intento: add llamado, commit falla, rollback invocado
        first_session.add.assert_called_once()
        first_session.commit.assert_called_once()
        first_session.rollback.assert_called_once()

        # 3. Segundo intento: add llamado, commit exitoso
        second_session.add.assert_called_once()
        second_session.commit.assert_called_once()

        # 4. La consulta del segundo intento tiene los datos correctos
        consulta = second_session.add.call_args[0][0]
        assert consulta.producto == "papa"
        assert consulta.intent == "precio"


# ── Timeout constante ──────────────────────────────────────────────


class TestTimeout:
    """Timeout del pipeline."""

    def test_timeout_default(self) -> None:
        pipeline = AgroVozPipeline()
        assert pipeline._timeout == _PIPELINE_TIMEOUT
        assert pipeline._timeout == 120.0

    def test_timeout_customizable(self) -> None:
        pipeline = AgroVozPipeline(pipeline_timeout=5.0)
        assert pipeline._timeout == 5.0

    def test_max_whisper_audio_ms_es_12_segundos(self) -> None:
        assert _MAX_WHISPER_AUDIO_MS == 12_000

    def test_whisper_timeout_es_60_segundos(self) -> None:
        """Whisper timeout debe ser 60s para margen de cold-load (~32s en CPU)."""
        assert _WHISPER_TIMEOUT == 60.0


# ── Onboarding: primer contacto y bienvenida (#86) ──────────────────


def _mock_first_contact(monkeypatch: pytest.MonkeyPatch, is_first: bool) -> None:
    """Mockea AgroVozPipeline._is_first_contact para controlar el resultado.

    Usa staticmethod() para preservar el comportamiento de staticmethod:
    sin eso, self._is_first_contact pasaria self como primer argumento.

    Args:
        monkeypatch: Fixture de pytest.
        is_first: True simula primer contacto (0 consultas previas),
                   False simula contacto repetido.
    """

    def fake_is_first(_phone_hash: str) -> bool:
        return is_first

    monkeypatch.setattr(AgroVozPipeline, "_is_first_contact", staticmethod(fake_is_first))


@pytest.mark.asyncio
class TestOnboarding:
    """Deteccion de primer contacto y audio de bienvenida (#86)."""

    @pytest.fixture
    def wav_path(self, tmp_path: Path) -> Path:
        """Crea un archivo WAV falso para los tests."""
        p = tmp_path / "test_audio.wav"
        p.write_bytes(b"FAKE_WAV_16KHZ_MONO")
        return p

    @pytest.fixture
    def tts_ogg(self, tmp_path: Path) -> str:
        """Crea un OGG falso para simular salida de TTS."""
        p = tmp_path / "pipeline_output.ogg"
        p.write_bytes(b"FAKE_TTS_OGG")
        return str(p)

    @pytest.fixture
    def welcome_ogg(self, tmp_path: Path) -> str:
        """Crea un OGG falso para simular salida de TTS de bienvenida."""
        p = tmp_path / "welcome_output.ogg"
        p.write_bytes(b"FAKE_WELCOME_OGG")
        return str(p)

    async def test_primer_contacto_genera_bienvenida(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
        welcome_ogg: str,
    ) -> None:
        """Primer audio de un numero nuevo retorna welcome_audio_path no None."""
        _mock_whisper_transcribe(monkeypatch)
        _mock_llm_answer(monkeypatch, "La papa esta a 450 pesos el kilo")
        _mock_tts_synthesize(monkeypatch, tts_ogg)
        _mock_db_save(monkeypatch)
        _mock_first_contact(monkeypatch, is_first=True)

        # TTS de bienvenida: la primera llamada sintetiza _WELCOME_TEXT,
        # la segunda sintetiza la respuesta normal. Usamos un contador para
        # diferenciarlas.
        call_count = [0]

        def fake_synthesize(_self: object, text: str, output_dir: str | Path | None = None) -> str:
            call_count[0] += 1
            if call_count[0] == 1:
                # Primera llamada = bienvenida
                return welcome_ogg
            # Segunda llamada = respuesta normal
            return tts_ogg

        monkeypatch.setattr(
            "app.services.pipeline_service.TTSService.synthesize",
            fake_synthesize,
        )

        pipeline = AgroVozPipeline()
        result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=3000,
            message_id="test-onboard-1",
            chat_id_hash="abc123def456",
            request_id="req-onboard-1",
        )

        assert result.welcome_audio_path == welcome_ogg
        assert result.audio_path == tts_ogg
        assert "450" in result.text_response

    async def test_contacto_repetido_sin_bienvenida(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
    ) -> None:
        """Segundo audio en adelante: welcome_audio_path es None."""
        _mock_whisper_transcribe(monkeypatch)
        _mock_llm_answer(monkeypatch, "La papa esta a 500 pesos el kilo")
        _mock_tts_synthesize(monkeypatch, tts_ogg)
        _mock_db_save(monkeypatch)
        _mock_first_contact(monkeypatch, is_first=False)

        pipeline = AgroVozPipeline()
        result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=2500,
            message_id="test-onboard-2",
            chat_id_hash="def456abc789",
            request_id="req-onboard-2",
        )

        assert result.welcome_audio_path is None
        assert result.audio_path == tts_ogg

    async def test_bienvenida_falla_tts_no_bloquea_pipeline(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
    ) -> None:
        """Si el TTS de bienvenida falla, el pipeline continua sin bienvenida."""
        _mock_whisper_transcribe(monkeypatch)
        _mock_llm_answer(monkeypatch, "La papa esta a 450 pesos el kilo")
        _mock_db_save(monkeypatch)
        _mock_first_contact(monkeypatch, is_first=True)

        # TTS falla siempre (tanto bienvenida como respuesta).
        def fake_synthesize_err(_self: object, text: str, output_dir: str | Path | None = None) -> str:
            raise RuntimeError("TTS roto")

        monkeypatch.setattr(
            "app.services.pipeline_service.TTSService.synthesize",
            fake_synthesize_err,
        )

        pipeline = AgroVozPipeline()
        result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=3000,
            message_id="test-onboard-tts-fail",
            chat_id_hash="ghi789jkl012",
            request_id="req-onboard-tts-fail",
        )

        # Bienvenida fallo → welcome_audio_path es None (no cuelga el pipeline).
        assert result.welcome_audio_path is None
        # La respuesta normal tampoco tiene audio (TTS fallo), pero el texto existe.
        assert "450" in result.text_response
        assert result.audio_path == ""

    async def test_deteccion_falla_no_bloquea_pipeline(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
    ) -> None:
        """Si la query de deteccion de primer contacto falla, pipeline continua."""
        _mock_whisper_transcribe(monkeypatch)
        _mock_llm_answer(monkeypatch, "La papa esta a 450 pesos el kilo")
        _mock_tts_synthesize(monkeypatch, tts_ogg)
        _mock_db_save(monkeypatch)

        def fake_is_first_error(_phone_hash: str) -> bool:
            raise RuntimeError("DB caida")

        monkeypatch.setattr(AgroVozPipeline, "_is_first_contact", staticmethod(fake_is_first_error))

        pipeline = AgroVozPipeline()
        result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=3000,
            message_id="test-onboard-detect-fail",
            chat_id_hash="jkl012mno345",
            request_id="req-onboard-detect-fail",
        )

        # Deteccion fallo → safe default: no bienvenida.
        assert result.welcome_audio_path is None
        # Pipeline normal continua.
        assert result.audio_path == tts_ogg
        assert "450" in result.text_response

    async def test_chat_hash_vacio_no_detecta_bienvenida(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
    ) -> None:
        """Si chat_id_hash es 'sin_chat', no se intenta detectar primer contacto."""
        _mock_whisper_transcribe(monkeypatch)
        _mock_llm_answer(monkeypatch, "La papa esta a 450 pesos el kilo")
        _mock_tts_synthesize(monkeypatch, tts_ogg)
        _mock_db_save(monkeypatch)

        # Si se llamara _is_first_contact, el test fallaria.
        def fake_is_first_should_not_be_called(_phone_hash: str) -> bool:
            raise AssertionError("_is_first_contact no deberia llamarse con sin_chat")

        monkeypatch.setattr(AgroVozPipeline, "_is_first_contact", staticmethod(fake_is_first_should_not_be_called))

        pipeline = AgroVozPipeline()
        result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=3000,
            message_id="test-onboard-no-chat",
            chat_id_hash="sin_chat",
            request_id="req-onboard-no-chat",
        )

        assert result.welcome_audio_path is None

    async def test_bienvenida_texto_es_fijo_predefinido(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
        welcome_ogg: str,
    ) -> None:
        """El texto de bienvenida es _WELCOME_TEXT (constante), no generado por LLM."""
        _mock_whisper_transcribe(monkeypatch)
        _mock_llm_answer(monkeypatch, "no deberia usarse para bienvenida")
        _mock_db_save(monkeypatch)
        _mock_first_contact(monkeypatch, is_first=True)

        synthesized_texts: list[str] = []

        def fake_synthesize_capture(_self: object, text: str, output_dir: str | Path | None = None) -> str:
            synthesized_texts.append(text)
            if len(synthesized_texts) == 1:
                return welcome_ogg
            return tts_ogg

        monkeypatch.setattr(
            "app.services.pipeline_service.TTSService.synthesize",
            fake_synthesize_capture,
        )

        pipeline = AgroVozPipeline()
        result = await pipeline.process(
            wav_path=wav_path,
            audio_duration_ms=3000,
            message_id="test-onboard-text",
            chat_id_hash="mno345pqr678",
            request_id="req-onboard-text",
        )

        # La primera sintesis (bienvenida) usa _WELCOME_TEXT.
        from app.services.pipeline_service import _WELCOME_TEXT

        assert synthesized_texts[0] == _WELCOME_TEXT
        assert result.welcome_audio_path == welcome_ogg


# ── _is_first_contact (unit test) ───────────────────────────────────


class TestIsFirstContact:
    """Deteccion de primer contacto via SELECT COUNT en consultations."""

    def test_retorna_true_sin_consultas_previas(self, db) -> None:  # type: ignore[no-untyped-def]
        """Phone_hash sin consultas previas retorna True (primer contacto)."""
        import app.core.database as db_module

        # Mockear SessionLocal para usar la DB temporal del test.
        original = db_module.SessionLocal
        db_module.SessionLocal = lambda: db  # type: ignore[misc]
        try:
            result = AgroVozPipeline._is_first_contact("a" * 64)
        finally:
            db_module.SessionLocal = original  # type: ignore[misc]

        assert result is True

    def test_retorna_false_con_consultas_previas(self, db) -> None:  # type: ignore[no-untyped-def]
        """Phone_hash con consultas previas retorna False (contacto repetido)."""
        from app.models.consultation import Consultation

        # Insertar una consulta previa para este phone_hash.
        db.add(
            Consultation(
                phone_hash="b" * 64,
                intent="precio",
                query_text="precio de la papa",
                response_text="450 pesos",
                audio_duration_ms=2000,
                latency_ms=1000,
            )
        )
        db.commit()

        import app.core.database as db_module

        original = db_module.SessionLocal
        db_module.SessionLocal = lambda: db  # type: ignore[misc]
        try:
            result = AgroVozPipeline._is_first_contact("b" * 64)
        finally:
            db_module.SessionLocal = original  # type: ignore[misc]

        assert result is False

    def test_phone_hash_distinto_no_afecta(self, db) -> None:  # type: ignore[no-untyped-def]
        """Consultas de OTRO phone_hash no cuentan como previas para este."""
        from app.models.consultation import Consultation

        db.add(
            Consultation(
                phone_hash="c" * 64,
                intent="clima",
                query_text="clima en traiguen",
                response_text="8 grados",
                audio_duration_ms=1500,
                latency_ms=800,
            )
        )
        db.commit()

        import app.core.database as db_module

        original = db_module.SessionLocal
        db_module.SessionLocal = lambda: db  # type: ignore[misc]
        try:
            result = AgroVozPipeline._is_first_contact("d" * 64)
        finally:
            db_module.SessionLocal = original  # type: ignore[misc]

        assert result is True  # d*64 no tiene consultas, es primer contacto
