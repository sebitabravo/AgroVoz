"""Test E2E del pipeline de voz con modelos IA reales.

A diferencia de test_pipeline_service.py (que mockea Whisper, LLM y TTS),
este test carga los modelos reales y ejecuta el pipeline completo contra
un audio de muestra (tests/fixtures/sample_query.ogg).

Solo corre si los modelos IA están descargados Y el stack de import es
viable. En CI se omite automáticamente (skip) porque los modelos pesan
~3 GB y no están en el runner.

NOTA crítica sobre contaminación de sys.modules: el import real de
`openai-whisper` NO puede ocurrir si va a fallar (ej: NumPy 2.5 incompatible
con numba). Un import parcial de whisper deja módulos corruptos en sys.modules que
rompen los mocks de test_pipeline_service (latency_ms queda en 0). Por
eso `_puede_correr_e2e()` se evalúa en module scope verificando la versión
de NumPy (solo para openai-whisper) y `find_spec` (sin importar), de modo que
el cuerpo del test nunca se ejecuta si el backend efectivo no está instalado.

Ejecutar manualmente en el VPS o tras `make setup-models`:
    uv run pytest tests/test_pipeline_e2e.py -v -s

Requisitos:
    - Whisper `small` (faster-whisper/CTranslate2 por defecto, o
      openai-whisper si `WHISPER_BACKEND=openai`)
    - Qwen2.5-3B Q4_K_M en models/qwen2.5-3b-q4_k_m.gguf (~2 GB)
    - Piper voz es_MX-claude-high en models/es_MX-claude-high.onnx
    - tests/fixtures/sample_query.ogg (audio de consulta de muestra)
    - NumPy <= 2.4 solo con `WHISPER_BACKEND=openai` (numba no soporta 2.5+)
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


def _whisper_runtime_modules(backend: str | None = None) -> tuple[str, ...]:
    """Módulos requeridos por el backend Whisper efectivo.

    faster-whisper usa CTranslate2 en runtime; openai solo necesita su módulo
    legacy. Mantener la selección pura permite probarla sin cargar modelos.
    """
    backend_efectivo = backend or settings.whisper_backend
    if backend_efectivo == "faster":
        return ("faster_whisper", "ctranslate2")
    return ("whisper",)


def _whisper_runtime_faltantes(backend: str | None = None) -> list[str]:
    """Describe dependencias faltantes del backend Whisper efectivo.

    find_spec localiza cada módulo sin ejecutar su código, así no contamina
    sys.modules si el import legacy fuera a fallar (ej: NumPy 2.5 vs numba).
    """
    backend_efectivo = backend or settings.whisper_backend
    modulos_faltantes = [
        modulo for modulo in _whisper_runtime_modules(backend_efectivo) if importlib.util.find_spec(modulo) is None
    ]
    faltantes = [f"dependencia Whisper ausente: {modulo}" for modulo in modulos_faltantes]
    if backend_efectivo == "openai" and not _numpy_compatible_con_numba():
        faltantes.append("NumPy <= 2.4 requerido por openai-whisper/numba")
    return faltantes


def _stack_ia_listo() -> bool:
    """True si el backend Whisper configurado y sus requisitos están listos."""
    return not _whisper_runtime_faltantes()


def _archivos_faltantes() -> list[str]:
    """Describe modelos y fixtures externos que faltan para el E2E."""
    rutas = (
        (Path(settings.llm_model_path), "modelo LLM Qwen2.5-3B"),
        (Path(settings.piper_model_path), "modelo Piper TTS"),
        (_FIXTURE_OGG, "audio fixture sample_query.ogg"),
    )
    return [f"{descripcion}: {ruta}" for ruta, descripcion in rutas if not ruta.exists()]


def _puede_correr_e2e() -> bool:
    """Gate de module scope: archivos + stack IA, sin contaminar sys.modules."""
    return not _archivos_faltantes() and _stack_ia_listo()


def _requisitos_faltantes() -> list[str]:
    """Combina artefactos externos y dependencias del backend efectivo."""
    return _archivos_faltantes() + _whisper_runtime_faltantes()


skip_sin_stack = pytest.mark.skipif(
    not _puede_correr_e2e(),
    reason="Artefactos/requisitos E2E faltantes: " + ", ".join(_requisitos_faltantes()),
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


@pytest.mark.parametrize(
    ("backend", "modulos"),
    [
        ("faster", ("faster_whisper", "ctranslate2")),
        ("openai", ("whisper",)),
    ],
)
def test_gate_whisper_selecciona_dependencias_del_backend_efectivo(
    backend: str,
    modulos: tuple[str, ...],
) -> None:
    """El gate no debe exigir openai-whisper cuando producción usa faster."""
    assert _whisper_runtime_modules(backend) == modulos


def test_gate_faster_no_exige_restriccion_legacy_de_numpy(monkeypatch: pytest.MonkeyPatch) -> None:
    """faster-whisper/CTranslate2 funciona con NumPy moderno."""
    monkeypatch.setattr(importlib.util, "find_spec", lambda _modulo: object())
    monkeypatch.setattr(
        "tests.test_pipeline_e2e._numpy_compatible_con_numba",
        lambda: False,
    )

    assert _whisper_runtime_faltantes("faster") == []


def test_gate_openai_anuncia_dependencia_legacy_y_numpy(monkeypatch: pytest.MonkeyPatch) -> None:
    """El fallback openai conserva explícitamente sus requisitos antiguos."""
    monkeypatch.setattr(importlib.util, "find_spec", lambda _modulo: object())
    monkeypatch.setattr(
        "tests.test_pipeline_e2e._numpy_compatible_con_numba",
        lambda: False,
    )

    assert _whisper_runtime_faltantes("openai") == ["NumPy <= 2.4 requerido por openai-whisper/numba"]
