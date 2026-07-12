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


# ── _generate_response ─────────────────────────────────────────────


@pytest.mark.asyncio
class TestGenerateResponse:
    """Generacion de respuesta LLM con mock de answer()."""

    async def test_respuesta_normal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _mock_llm_answer(
            monkeypatch,
            "La papa cuesta 450 pesos el kilo en Lo Valledor",
        )
        text, intent = await AgroVozPipeline._generate_response("precio de la papa en lo valledor")
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

        async def fake_answer_check(_query: str) -> str:
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
