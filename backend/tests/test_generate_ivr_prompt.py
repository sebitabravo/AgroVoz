"""Pruebas del generador de audio para el spike IVR local."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import generate_ivr_prompt as ivr_prompt


def test_genera_wav_telefonico_y_publica_atomicamente(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """El prompt usa ODEPA/Piper y ffmpeg 8 kHz mono."""
    session = Mock()
    source_ogg = tmp_path / "tts-source.ogg"
    source_ogg.write_bytes(b"ogg")
    output = tmp_path / "sounds" / "precio-papa.wav"
    tts = Mock()
    tts.synthesize.return_value = str(source_ogg)
    run_calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        run_calls.append(command)
        Path(command[-1]).write_bytes(b"wav-8khz")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(ivr_prompt, "SessionLocal", Mock(return_value=session))
    monkeypatch.setattr(
        ivr_prompt,
        "get_price_for_llm",
        Mock(return_value="Papa está a 500 pesos el kilo, según ODEPA."),
    )
    monkeypatch.setattr(ivr_prompt, "TTSService", Mock(return_value=tts))
    monkeypatch.setattr(ivr_prompt.subprocess, "run", fake_run)

    result = ivr_prompt.generate_ivr_prompt(output_path=output)

    assert result == output
    assert output.read_bytes() == b"wav-8khz"
    assert not source_ogg.exists()
    session.close.assert_called_once()
    tts.synthesize.assert_called_once()
    assert run_calls[0][run_calls[0].index("-ar") + 1] == "8000"
    assert run_calls[0][run_calls[0].index("-ac") + 1] == "1"


def test_sin_precio_falla_cerrado_y_no_invoca_tts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Nunca publica un audio engañoso cuando ODEPA no tiene dato."""
    session = Mock()
    tts_factory = Mock()
    monkeypatch.setattr(ivr_prompt, "SessionLocal", Mock(return_value=session))
    monkeypatch.setattr(
        ivr_prompt,
        "get_price_for_llm",
        Mock(return_value="No tengo datos de precio para papa."),
    )
    monkeypatch.setattr(ivr_prompt, "TTSService", tts_factory)

    with pytest.raises(RuntimeError, match="precio utilizable"):
        ivr_prompt.generate_ivr_prompt(output_path=tmp_path / "precio.wav")

    session.close.assert_called_once()
    tts_factory.assert_not_called()
