"""Tests para app.services.pipeline_service: AgroVozPipeline.

Cubre: detect_intent (precio, clima, desconocido), _generate_response con
mock_answer, _save_consultation (DB y error), process (happy path, timeout,
Whisper falla, TTS falla, audio largo), y benchmark por etapa.

Sin modelo real: todos los tests corren en CI sin Whisper, llama-cpp-python,
ni Piper TTS.
"""

import asyncio
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.schemas.pipeline import AudioResponse
from app.services.pipeline_service import (
    _MAX_WHISPER_AUDIO_MS,
    _PIPELINE_TIMEOUT,
    _WHISPER_TIMEOUT,
    AgroVozPipeline,
)

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
    """Mockea llm_service.answer para retornar texto fijo."""

    async def fake_answer(query: str, phone_hash: str | None = None) -> str:
        return answer_text

    monkeypatch.setattr("app.services.llm_service.answer", fake_answer)


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
    ) -> None:
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

    monkeypatch.setattr(AgroVozPipeline, "_save_consultation", fake_save)
    return calls


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

    def test_vacio(self) -> None:
        assert AgroVozPipeline._extract_producto("") is None

    def test_producto_en_oracion_larga(self) -> None:
        assert AgroVozPipeline._extract_producto("cuanto esta el kilo de papa en la vega") == "papa"

    def test_producto_con_acento(self) -> None:
        """Productos con acento matchean (limón, brócoli)."""
        assert AgroVozPipeline._extract_producto("precio del limón") == "limón"


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

    async def test_answer_lanza_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Si answer() lanza excepcion y no hay keywords, retorna disculpa generica.

        Path (d) del fallback determinista (Issue #121): sin producto, clima
        ni venta en la query → mensaje generico + intent "desconocido".
        """

        async def fake_answer_error(_query: str, phone_hash: str | None = None) -> str:
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

    async def test_answer_lanza_exception_fallback_precio(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Path (a): LLM cae, keyword de producto → fallback get_price real.

        Issue #121: si el LLM falla pero la query contiene un producto
        agricola, se llama get_price_for_llm via _force_keyword_tool
        y se responde con datos reales de ODEPA.
        """

        async def fake_answer_error(_query: str, phone_hash: str | None = None) -> str:
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

    async def test_answer_lanza_exception_fallback_clima(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Path (b): LLM cae, keyword de clima → fallback get_weather real.

        Issue #121: si el LLM falla pero la query contiene keywords
        climaticos, se llama get_weather(lat=-38.23, lon=-72.68) via
        _force_keyword_tool y se responde con datos de OpenMeteo.
        """

        async def fake_answer_error(_query: str, phone_hash: str | None = None) -> str:
            raise RuntimeError("LLM colapso")

        async def fake_force_clima(query: str, phone_hash: str | None = None) -> str:
            return (
                "En Traiguen ahora: 8 grados, nublado, humedad 80%, "
                "viento 3.5 m/s, lluvia 0.8 mm, segun OpenMeteo."
            )

        monkeypatch.setattr("app.services.llm_service.answer", fake_answer_error)
        monkeypatch.setattr(
            "app.services.llm_keywords._force_keyword_tool",
            fake_force_clima,
        )

        text, intent = await AgroVozPipeline._generate_response(
            "como esta el clima en traiguen", "test-chat-hash"
        )
        assert "grados" in text.lower()
        assert "traiguen" in text.lower()
        assert intent == "clima"

    async def test_answer_lanza_exception_fallback_venta(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Path (c): LLM cae, patron "N kilos de producto" → fallback venta.

        Issue #121: si el LLM falla pero la query contiene un patron
        de venta ("N kilos de producto"), se llama calculate_sale_value
        via _force_keyword_tool.
        """

        async def fake_answer_error(_query: str, phone_hash: str | None = None) -> str:
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

        text, intent = await AgroVozPipeline._generate_response(
            "voy a vender 30 kilos de papa", "test-chat-hash"
        )
        assert "kilos" in text.lower()
        assert "papa" in text.lower()
        assert "recibiras" in text.lower()
        assert intent == "precio"


# ── process() pipeline completo ────────────────────────────────────


@pytest.mark.asyncio
class TestProcess:
    """Tests de integracion para AgroVozPipeline.process()."""

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

        async def fake_answer_check(_query: str) -> str:
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

    async def test_happy_path_clima(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wav_path: Path,
        tts_ogg: str,
    ) -> None:
        """Pipeline completo: Whisper → LLM → TTS con consulta de clima."""

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
        async def fake_answer_slow(_query: str, phone_hash: str | None = None) -> str:
            await asyncio.sleep(999.0)  # Nunca termina
            return "muy tarde"

        monkeypatch.setattr("app.services.llm_service.answer", fake_answer_slow)
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

        async def fake_answer_check(_query: str, phone_hash: str | None = None) -> str:
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

        async def fake_answer_check(_query: str, phone_hash: str | None = None) -> str:
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

    def test_save_exitoso(self) -> None:
        """_save_consultation guarda en DB sin lanzar excepcion."""
        with patch("app.core.database.SessionLocal") as mock_factory:
            mock_session = mock_factory.return_value
            start = time.monotonic()
            AgroVozPipeline._save_consultation(
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

    def test_save_error_no_propaga(self) -> None:
        """Si la DB falla, _save_consultation no lanza excepcion."""
        with patch("app.core.database.SessionLocal") as mock_session:
            mock_session.side_effect = SQLAlchemyError("DB caida")
            start = time.monotonic()
            # No debe lanzar excepcion
            AgroVozPipeline._save_consultation(
                phone_hash="test_hash_err",
                intent="clima",
                query_text="clima en traiguen",
                response_text="8 grados",
                audio_duration_ms=2000,
                start_time=start,
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
        assert mock_factory.call_count == 2, (
            f"SessionLocal call_count={mock_factory.call_count}, esperado 2"
        )

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
