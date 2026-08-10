"""Regresiones deterministas del bootstrap Docker de Whisper."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import preload_whisper


def test_preload_delega_al_servicio_que_resuelve_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """El script no duplica la selección faster/openai ni descarga por su cuenta."""
    service = Mock()
    factory = Mock(return_value=service)
    monkeypatch.setattr(preload_whisper, "WhisperService", factory)

    preload_whisper.preload()

    factory.assert_called_once_with()
    service.preload.assert_called_once_with()


def test_entrypoint_no_hardcodea_el_backend_openai() -> None:
    """El entrypoint debe delegar el preload y respetar WHISPER_BACKEND."""
    entrypoint = Path(__file__).parents[1] / "scripts" / "entrypoint.sh"
    source = entrypoint.read_text(encoding="utf-8")

    assert "scripts/preload_whisper.py" in source
    assert "whisper.load_model" not in source
    assert "~/.cache/whisper" not in source
