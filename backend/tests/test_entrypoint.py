"""Regresiones deterministas del bootstrap Docker de Whisper."""

import subprocess
import sys
from pathlib import Path
from types import ModuleType
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


def test_preload_module_se_resuelve_sin_descargar_modelos() -> None:
    """El comando de producción importa el módulo antes de intentar una descarga."""
    backend_dir = Path(__file__).parents[1]

    result = subprocess.run(
        [sys.executable, "-m", "scripts.preload_whisper", "--help"],
        cwd=backend_dir,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--check-cache" in result.stdout


def test_cache_faster_completo_evitas_precarga(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Un snapshot completo se reconoce offline, sin descargar el modelo."""
    service = Mock(backend="faster", model_name="small")
    monkeypatch.setattr(preload_whisper, "WhisperService", Mock(return_value=service))
    for filename in preload_whisper._FASTER_WHISPER_FILES:
        (tmp_path / filename).touch()

    download_model = Mock(return_value=str(tmp_path))
    utils_module = ModuleType("faster_whisper.utils")
    utils_module.download_model = download_model  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", utils_module)

    assert preload_whisper.is_cached()
    download_model.assert_called_once_with("small", cache_dir=None, local_files_only=True)


def test_cache_faster_incompleto_no_omite_la_precarga(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Un snapshot parcial debe volver a la precarga para no fallar en runtime."""
    service = Mock(backend="faster", model_name="small")
    monkeypatch.setattr(preload_whisper, "WhisperService", Mock(return_value=service))
    (tmp_path / "model.bin").touch()

    download_model = Mock(return_value=str(tmp_path))
    utils_module = ModuleType("faster_whisper.utils")
    utils_module.download_model = download_model  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", utils_module)

    assert not preload_whisper.is_cached()


def test_entrypoint_delega_cache_y_precarga_al_modulo_configurado() -> None:
    """El entrypoint respeta Settings y evita precargar si el cache está completo."""
    entrypoint = Path(__file__).parents[1] / "scripts" / "entrypoint.sh"
    source = entrypoint.read_text(encoding="utf-8")

    assert "python -m scripts.preload_whisper --check-cache" in source
    assert "python -m scripts.preload_whisper;" in source
    assert 'WHISPER_BACKEND="${WHISPER_BACKEND:-faster}"' not in source
    assert 'WHISPER_MODEL="${WHISPER_MODEL:-small}"' not in source
    assert "whisper.load_model" not in source
    assert "~/.cache/whisper" not in source
