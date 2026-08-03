"""Integración determinista de programas INDAP en el pipeline de voz."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.services import indap_credit_service
from app.services import pipeline_service as pipeline_module
from app.services.pipeline_service import AgroVozPipeline

_CATALOG_DATE = date(2026, 8, 3)


@pytest.mark.asyncio
async def test_audio_indap_transcribe_deriva_a_programas_y_sintetiza(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Whisper → derivación INDAP → TTS conserva fuente y oficina local."""
    wav_path = tmp_path / "consulta.wav"
    wav_path.write_bytes(b"wav")
    tts_path = tmp_path / "respuesta.ogg"

    def fake_transcribe(_self: object, _audio_path: str) -> dict[str, object]:
        return {
            "text": "qué programa hay para comprar un motocultivador",
            "language": "es",
            "segments": [],
            "duration_ms": 1_200,
        }

    class FakeTTS:
        def synthesize(self, _text: str) -> str:
            return str(tts_path)

    async def no_alerta(
        _text: str,
        _phone_hash: str,
        _chat_id: str | None,
    ) -> tuple[None, None]:
        return None, None

    def fake_save(**_kwargs: object) -> int:
        return 245

    monkeypatch.setattr(pipeline_module.WhisperService, "transcribe", fake_transcribe)
    monkeypatch.setattr(pipeline_module, "_get_tts_service", lambda: FakeTTS())
    real_get_programas = indap_credit_service.get_programas_indap

    def deterministic_get_programas(consulta: str) -> str:
        """Fija la fecha de vigencia para que la prueba no dependa del reloj."""
        return real_get_programas(consulta, today=_CATALOG_DATE)

    monkeypatch.setattr(indap_credit_service, "get_programas_indap", deterministic_get_programas)
    monkeypatch.setattr(pipeline_module, "retain_audio", lambda *_args: None)
    monkeypatch.setattr(AgroVozPipeline, "_is_first_contact", staticmethod(lambda _phone: False))
    monkeypatch.setattr(AgroVozPipeline, "_save_consultation", staticmethod(fake_save))
    monkeypatch.setattr(AgroVozPipeline, "_handle_alert_commands", staticmethod(no_alerta))

    result = await AgroVozPipeline().process(
        wav_path=wav_path,
        audio_duration_ms=1_200,
        message_id="indap-voice-245",
        chat_id_hash="phone-hash",
        request_id="request-indap-245",
    )

    assert result.intent == "credito"
    assert result.consultation_id == 245
    assert result.audio_path == str(tts_path)
    assert "Programa de Desarrollo de Inversiones" in result.text_response
    assert "Riveros #1059" in result.text_response
    assert "Fuente oficial INDAP:" in result.text_response
