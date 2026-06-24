"""Test E2E del pipeline de voz con modelos IA reales.

A diferencia de test_pipeline_service.py (que mockea Whisper, LLM y TTS),
este test carga los modelos reales y ejecuta el pipeline completo contra
un audio de muestra (tests/fixtures/sample_query.ogg).

Solo corre si los modelos IA están descargados Y el stack de import es
viable. En CI se omite automáticamente (skip) porque los modelos pesan
~3 GB y no están en el runner.

NOTA crítica sobre contaminación de sys.modules: el `import whisper` real
NO puede ocurrir si va a fallar (ej: NumPy 2.5 incompatible con numba).
Un import parcial de whisper deja módulos corruptos en sys.modules que
rompen los mocks de test_pipeline_service (latency_ms queda en 0). Por
eso `_puede_correr_e2e()` se evalúa en module scope verificando la versión
de NumPy (ya cargada en el proceso) y `find_spec("whisper")` (sin
importar), de modo que el cuerpo del test nunca se ejecuta si el import
de whisper fallaría.

Ejecutar manualmente en el VPS o tras `make setup-models`:
    uv run pytest tests/test_pipeline_e2e.py -v -s

Requisitos:
    - Whisper `small` (resuelto por openai-whisper, ~462 MB)
    - Qwen2.5-3B Q4_K_M en models/qwen2.5-3b-q4_k_m.gguf (~2 GB)
    - Piper voz es_MX-claude-high en models/es_MX-claude-high.onnx
    - tests/fixtures/sample_query.ogg (audio de consulta de muestra)
    - NumPy <= 2.4 (numba no soporta 2.5+)
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.core.config import settings

_FIXTURE_OGG = Path(__file__).parent / "fixtures" / "sample_query.ogg"


def _numpy_compatible_con_numba() -> bool:
    """True si NumPy instalado es <= 2.4 (numba no soporta 2.5+).

    NumPy ya está cargado en el proceso (dependencia transitoria), así que
    verificar su versión no contamina sys.modules. Esto evita el `import
    whisper` en el cuerpo del test cuando sabemos que va a fallar.
    """
    try:
        import numpy
    except ImportError:
        return False

    partes = numpy.__version__.split(".")
    try:
        major = int(partes[0])
        minor = int(partes[1])
    except (IndexError, ValueError):
        return False
    return (major, minor) <= (2, 4)


def _whisper_instalado() -> bool:
    """True si el paquete whisper está instalado, sin importarlo.

    find_spec localiza el módulo sin ejecutar su código, así no contamina
    sys.modules si el import real fuera a fallar (ej: NumPy 2.5 vs numba).
    """
    return importlib.util.find_spec("whisper") is not None


def _stack_ia_listo() -> bool:
    """True si el stack IA puede cargar: NumPy compatible + whisper instalado."""
    return _numpy_compatible_con_numba() and _whisper_instalado()


def _archivos_disponibles() -> bool:
    """True si los archivos de modelo y el fixture de audio existen en disco."""
    llm_ok = Path(settings.llm_model_path).exists()
    piper_ok = Path(settings.piper_model_path).exists()
    fixture_ok = _FIXTURE_OGG.exists()
    return bool(llm_ok and piper_ok and fixture_ok)


def _puede_correr_e2e() -> bool:
    """Gate de module scope: archivos + stack IA, sin contaminar sys.modules."""
    return _archivos_disponibles() and _stack_ia_listo()


skip_sin_stack = pytest.mark.skipif(
    not _puede_correr_e2e(),
    reason=(
        "Stack IA no disponible (modelos sin descargar, NumPy > 2.4 o "
        "whisper no instalado). Ejecutar scripts/download_models.sh y "
        "verificar NumPy <= 2.4 antes de correr este test."
    ),
)


@skip_sin_stack
@pytest.mark.asyncio
async def test_pipeline_e2e_transcribe_responde_sintetiza(tmp_path: Path) -> None:
    """E2E real: audio ogg → Whisper → Qwen → Piper → AudioResponse.

    Verifica que el pipeline completo funciona end-to-end con modelos reales:
    produce texto de respuesta no vacío y, si Piper está disponible, un
    archivo .ogg de respuesta válido.
    """
    from app.services.audio_service import convert_ogg_to_wav, get_audio_duration_ms
    from app.services.pipeline_service import AgroVozPipeline

    wav_path = tmp_path / "sample_query.wav"
    convert_ogg_to_wav(_FIXTURE_OGG, wav_path)
    duration_ms = get_audio_duration_ms(wav_path)

    pipeline = AgroVozPipeline(pipeline_timeout=180.0)
    response = await pipeline.process(
        wav_path=wav_path,
        audio_duration_ms=duration_ms,
        message_id="e2e-test",
        chat_id_hash="e2e-hash",
        request_id="e2e-req",
    )

    # El pipeline debe responder con texto (no error fatal).
    assert response.text_response, "Pipeline E2E no generó texto de respuesta"
    assert response.latency_ms < 180_000
    assert response.intent in ("precio", "clima", "desconocido")

    # Si Piper generó audio, el archivo debe existir en disco.
    if response.audio_path:
        assert Path(response.audio_path).exists()
        Path(response.audio_path).unlink(missing_ok=True)
