"""Tests de consultas escritas (mensaje de texto de WhatsApp).

El productor no siempre puede mandar un audio: lugar ruidoso, una reunion, mala
senal. El texto es una via de entrada de primera clase, no un fallback. Estos
tests fijan que la consulta escrita recorre el MISMO pipeline que la de voz
(alertas, resumen, fast-path, tool calling, persistencia) pero sin Whisper ni
Piper, que son las dos etapas mas caras en CPU limitada.
"""

from pathlib import Path

import pytest

from app.api.webhooks import _MAX_TEXTO_CHARS, _is_text_message
from app.schemas.webhook import WebhookPayload
from app.services.pipeline_service import AgroVozPipeline


def _payload(tipo: str, body: str) -> WebhookPayload:
    """Payload minimo de Open-WA con tipo y cuerpo dados."""
    return WebhookPayload.model_validate(
        {"data": {"type": tipo, "body": body, "chatId": "56900000000@c.us"}}
    )


class TestDeteccionDeTexto:
    """Que cuenta como consulta escrita."""

    @pytest.mark.parametrize("tipo", ["chat", "text", ""])
    def test_tipos_de_mensaje_escrito(self, tipo: str) -> None:
        """Open-WA usa "chat"; se toleran "text" y sin tipo por robustez."""
        assert _is_text_message(_payload(tipo, "¿a cuánto está la papa?"))

    def test_voice_no_es_texto(self) -> None:
        """Un audio va por Whisper, no por esta via."""
        assert not _is_text_message(_payload("voice", ""))

    def test_imagen_no_es_texto(self) -> None:
        assert not _is_text_message(_payload("image", "pie de foto"))

    def test_cuerpo_vacio_no_es_consulta(self) -> None:
        assert not _is_text_message(_payload("chat", "   "))

    def test_texto_desmedido_se_descarta(self) -> None:
        """Un pegado accidental no es una consulta y dispara el prompt del LLM."""
        assert not _is_text_message(_payload("chat", "a" * (_MAX_TEXTO_CHARS + 1)))

    def test_texto_en_el_limite_se_acepta(self) -> None:
        assert _is_text_message(_payload("chat", "a" * _MAX_TEXTO_CHARS))


class TestPipelineDeTexto:
    """El pipeline con texto salta Whisper y TTS pero responde igual."""

    async def test_responde_sin_whisper_ni_tts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        whisper_llamado = []
        tts_llamado = []

        def whisper_espia(*_a: object, **_k: object) -> dict[str, object]:
            whisper_llamado.append(1)
            return {"text": "no deberia usarse"}

        def tts_espia(*_a: object, **_k: object) -> str:
            tts_llamado.append(1)
            return "/tmp/no.ogg"

        monkeypatch.setattr(
            "app.services.pipeline_service.WhisperService.transcribe", whisper_espia
        )
        monkeypatch.setattr(
            "app.services.tts_service.TTSService.synthesize", tts_espia
        )

        async def responder(*_a: object, **_k: object) -> str:
            return "La papa está a 520 pesos el kilo."

        monkeypatch.setattr("app.services.llm_keywords._force_keyword_tool", responder)

        resultado = await AgroVozPipeline().process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="txt-1",
            chat_id_hash="a" * 64,
            request_id="req-txt-1",
            texto_directo="¿a cuánto está la papa?",
            generar_audio=False,
        )

        assert whisper_llamado == [], "no debe transcribir: la consulta ya es texto"
        assert tts_llamado == [], "no debe sintetizar: la respuesta va escrita"
        assert "520 pesos" in resultado.text_response
        assert resultado.audio_path == ""
        assert resultado.whisper_ms == 0
        assert resultado.tts_ms == 0

    async def test_texto_usa_el_mismo_intent_que_la_voz(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Misma consulta escrita o hablada: mismo intent clasificado."""

        async def responder(*_a: object, **_k: object) -> str:
            return "En Traiguén ahora: 12°C, nublado."

        monkeypatch.setattr("app.services.llm_keywords._force_keyword_tool", responder)

        resultado = await AgroVozPipeline().process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="txt-2",
            chat_id_hash="b" * 64,
            request_id="req-txt-2",
            texto_directo="¿va a llover mañana?",
            generar_audio=False,
        )
        assert resultado.intent == "clima"

    async def test_sin_audio_ni_texto_no_revienta(self) -> None:
        """Llamada degenerada: responde algo, no lanza excepcion."""
        resultado = await AgroVozPipeline().process(
            wav_path=None,
            audio_duration_ms=0,
            message_id="txt-3",
            chat_id_hash="c" * 64,
            request_id="req-txt-3",
        )
        assert resultado.audio_path == ""
        assert resultado.whisper_ms == 0


class TestPipelineDeVozNoCambia:
    """Regresion: el camino de audio sigue generando TTS."""

    async def test_voz_sigue_sintetizando_audio(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        ogg = tmp_path / "salida.ogg"
        ogg.write_bytes(b"OGG")

        monkeypatch.setattr(
            "app.services.pipeline_service.WhisperService.transcribe",
            lambda *_a, **_k: {"text": "a cuanto esta la papa"},
        )
        monkeypatch.setattr(
            "app.services.tts_service.TTSService.synthesize", lambda *_a, **_k: str(ogg)
        )

        async def responder(*_a: object, **_k: object) -> str:
            return "La papa está a 520 pesos el kilo."

        monkeypatch.setattr("app.services.llm_keywords._force_keyword_tool", responder)

        wav = tmp_path / "entrada.wav"
        wav.write_bytes(b"RIFF")

        resultado = await AgroVozPipeline().process(
            wav_path=wav,
            audio_duration_ms=3000,
            message_id="voz-1",
            chat_id_hash="d" * 64,
            request_id="req-voz-1",
        )
        assert resultado.audio_path == str(ogg)
