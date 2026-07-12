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
from unittest.mock import patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.schemas.pipeline import AudioResponse
from app.services.pipeline_service import (
    _MAX_WHISPER_AUDIO_MS,
    _PIPELINE_TIMEOUT,
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

    async def fake_answer(query: str) -> str:
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


def _mock_tts_fail(
    monkeypatch: pytest.MonkeyPatch, exc: type[Exception] = RuntimeError, msg: str = "TTS roto"
) -> None:
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
    ) -> None:
        calls.append({
            "phone_hash": phone_hash,
            "intent": intent,
            "query_text": query_text,
            "response_text": response_text,
            "audio_duration_ms": audio_duration_ms,
            "whisper_ms": whisper_ms,
            "llm_ms": llm_ms,
            "tts_ms": tts_ms,
            "producto": producto,
        })

    monkeypatch.setattr(AgroVozPipeline, "_save_consultation", fake_save)
    return calls


# ── _detect_intent ─────────────────────────────────────────────────


class TestDetectIntent:
    """Keyword matching para metrica de intencion."""

    def test_precio_por_keyword_papa(self) -> None:
        intent = AgroVozPipeline._detect_intent(
            "precio de la papa en lo valledor", ""
        )
        assert intent == "precio"

    def test_precio_por_keyword_luca(self) -> None:
        intent = AgroVozPipeline._detect_intent(
            "a cuanto estan las papas", "en la feria estan a 200 pesos el kilo"
        )
        assert intent == "precio"

    def test_precio_por_keyword_cuesta_en_respuesta(self) -> None:
        intent = AgroVozPipeline._detect_intent(
            "como esta la papa",
            "la papa cuesta 450 pesos el kilo en lo valledor",
        )
        assert intent == "precio"

    def test_clima_por_keyword_temperatura(self) -> None:
        intent = AgroVozPipeline._detect_intent(
            "cual es la temperatura en traiguen", ""
        )
        assert intent == "clima"

    def test_clima_por_keyword_lluvia(self) -> None:
        # "llover" NO esta en keywords de clima, "lluvia" y "lloviendo" si
        intent_sin_match = AgroVozPipeline._detect_intent(
            "va a llover manana", ""
        )
        assert intent_sin_match != "clima"  # "llover" no es keyword
        # Con keyword correcta
        intent = AgroVozPipeline._detect_intent(
            "habra lluvia manana", ""
        )
        assert intent == "clima"

    def test_clima_por_keyword_frio_en_respuesta(self) -> None:
        intent = AgroVozPipeline._detect_intent(
            "como esta el dia",
            "hace frio en traiguen con 5 grados",
        )
        assert intent == "clima"

    def test_desconocido_sin_keywords(self) -> None:
        intent = AgroVozPipeline._detect_intent(
            "hola buenos dias", "en que puedo ayudarte"
        )
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
        intent = AgroVozPipeline._detect_intent(
            "PRECIO DE LA PAPA EN LO VALLEDOR", ""
        )
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
        assert AgroVozPipeline._extract_producto(
            "cuanto esta el kilo de papa en la vega"
        ) == "papa"

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
        text, intent = await AgroVozPipeline._generate_response(
            "precio de la papa en lo valledor"
        )
        assert "450" in text
        assert intent == "precio"

    async def test_query_vacia(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Si el texto transcrito esta vacio, retorna mensaje de error y desconocido."""
        _mock_llm_answer(monkeypatch, "no deberia llamarse")
        text, intent = await AgroVozPipeline._generate_response("")
        assert "entendi" in text.lower() or "entendí" in text.lower()
        assert intent == "desconocido"

    async def test_query_solo_espacios(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Si el texto transcrito son solo espacios, retorna mensaje de error."""
        _mock_llm_answer(monkeypatch, "no deberia llamarse")
        text, intent = await AgroVozPipeline._generate_response("   ")
        assert "entendi" in text.lower() or "entendí" in text.lower()
        assert intent == "desconocido"

    async def test_answer_lanza_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Si answer() lanza excepcion, retorna fallback pero no propaga."""

        async def fake_answer_error(_query: str) -> str:
            raise RuntimeError("LLM colapso")

        monkeypatch.setattr("app.services.llm_service.answer", fake_answer_error)
        # Query SIN keywords de precio ni clima → intent debe ser "desconocido"
        text, intent = await AgroVozPipeline._generate_response("hola como estas")
        assert "problema" in text.lower() or "intentar" in text.lower()
        assert intent == "desconocido"


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
        async def fake_answer_slow(_query: str) -> str:
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
        """Si Whisper lanza RuntimeError, pipeline continua con respuesta generica."""

        def fake_transcribe_err(_self: object, audio_path: str) -> dict[str, object]:
            raise RuntimeError("Whisper OOM")

        monkeypatch.setattr(
            "app.services.pipeline_service.WhisperService.transcribe",
            fake_transcribe_err,
        )
        # LLM NO deberia llamarse porque no hay texto transcrito
        llm_called = False

        async def fake_answer_check(_query: str) -> str:
            nonlocal llm_called
            llm_called = True
            return "no deberia generarse"

        monkeypatch.setattr("app.services.llm_service.answer", fake_answer_check)
        _mock_tts_synthesize(monkeypatch, tts_ogg)
        _mock_db_save(monkeypatch)

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
