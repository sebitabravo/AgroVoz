"""Benchmark de latencia para TTSService con modelo Piper real.

Mide el tiempo de sintesis para 3 largos de texto distintos.
Incluye warm-up para descartar overhead de carga del modelo ONNX.
Se salta si el modelo no esta descargado.

Uso:
    uv run pytest tests/test_tts_benchmark.py -v

Targets MVP (medidos con warm-up):
    - <20 palabras:  < 4s
    - 30-40 palabras: < 6s
    - 70-80 palabras: < 10s

Nota: las latencias dependen del hardware del runner (CPU, RAM).
Los targets estan calibrados para Hetzner CX43 (8 vCPU, 16 GB RAM).
"""

import logging
import time
from pathlib import Path

import pytest

from app.services.tts_service import TTSService

logger = logging.getLogger(__name__)

MODEL_PATH = Path("models/es_MX-claude-high.onnx")
MODEL_AVAILABLE = MODEL_PATH.exists()

skip_msg = (
    f"Modelo Piper no encontrado en {MODEL_PATH}. "
    "Descarguelo con: bash scripts/download_models.sh"
)


# Instancia compartida de TTSService (carga el modelo una sola vez)
_TTS_SERVICE: TTSService | None = None


def _get_tts_service() -> TTSService:
    global _TTS_SERVICE
    if _TTS_SERVICE is None:
        _TTS_SERVICE = TTSService(model_path=str(MODEL_PATH))
    return _TTS_SERVICE


def _warmup(tts_service: TTSService, benchmark_dir: Path) -> None:
    """Sintetiza un texto corto para descartar cold start."""
    tts_service.synthesize("Warm up.", output_dir=benchmark_dir)


@pytest.mark.skipif(not MODEL_AVAILABLE, reason=skip_msg)
class TestPiperLatencyBenchmark:
    """Benchmark de latencia de sintesis con modelo Piper real.

    Usa un solo TTSService para toda la clase (warm-up incluido).
    Las mediciones descartan el overhead de carga del modelo ONNX
    para reflejar latencia real de sintesis.
    """

    # ─── Textos de prueba ────────────────────────────────────────────

    TEXTO_CORTO = "El precio de la papa es 500 pesos por kilo."
    TEXTO_MEDIO = (
        "El precio de la papa en la feria de Temuco es de 500 pesos por kilo. "
        "El clima en Traiguen esta nublado con maxima de 22 grados. "
        "En Concepcion el precio de la cebolla es de 300 pesos."
    )
    TEXTO_LARGO = (
        "El precio de la papa en la feria de Santiago es de 500 pesos por kilo. "
        "En Temuco el precio de la papa es de 450 pesos por kilo. "
        "En la feria de Concepcion el precio es de 480 pesos por kilo. "
        "El clima en Traiguen esta nublado con una maxima de 22 grados "
        "y una minima de 12 grados. "
        "Se esperan lluvias para el fin de semana con probabilidad de 80 por ciento."
    )

    # ─── Fixtures ─────────────────────────────────────────────────────

    @pytest.fixture
    def benchmark_dir(self, tmp_path: Path) -> Path:
        """Directorio temporal para los audios del benchmark."""
        return tmp_path / "benchmark_audio"

    # ─── Tests ────────────────────────────────────────────────────────

    def _ejecutar_benchmark(
        self,
        texto: str,
        target_segundos: float,
        benchmark_dir: Path,
    ) -> None:
        """Ejecuta una medicion de latencia para un texto dado.

        El servicio se obtiene de _get_tts_service() con warm-up previo.

        Args:
            texto: Texto a sintetizar.
            target_segundos: Limite maximo aceptable en segundos.
            benchmark_dir: Directorio de salida.

        Raises:
            AssertionError: Si la latencia supera el target o el archivo
                          de salida no es valido.
        """
        service = _get_tts_service()

        # Medicion
        inicio = time.monotonic()
        result = service.synthesize(texto, output_dir=benchmark_dir)
        elapsed = time.monotonic() - inicio

        n_palabras = len(texto.split())

        # Validaciones
        ruta = Path(result)
        assert ruta.exists(), f"Archivo de salida no existe: {result}"
        assert ruta.stat().st_size > 0, f"Archivo de salida vacio: {result}"
        assert result.endswith(".ogg"), f"Formato incorrecto: {result}"

        # Log de la medicion
        logger.info(
            "Benchmark TTS — palabras=%d elapsed=%.2fs target=%.1fs archivo=%s tamano=%d",
            n_palabras,
            elapsed,
            target_segundos,
            result,
            ruta.stat().st_size,
        )

        # Asercion de performance
        assert elapsed < target_segundos, (
            f"Texto de {n_palabras} palabras tardo {elapsed:.2f}s, "
            f"superando el target de {target_segundos:.1f}s"
        )

    def test_latencia_texto_corto(self, benchmark_dir: Path) -> None:
        """Texto corto (<20 palabras) debe sintetizar en <4s."""
        _warmup(_get_tts_service(), benchmark_dir)
        self._ejecutar_benchmark(self.TEXTO_CORTO, 4.0, benchmark_dir)

    def test_latencia_texto_medio(self, benchmark_dir: Path) -> None:
        """Texto medio (30-40 palabras) debe sintetizar en <6s."""
        _warmup(_get_tts_service(), benchmark_dir)
        self._ejecutar_benchmark(self.TEXTO_MEDIO, 6.0, benchmark_dir)

    def test_latencia_texto_largo(self, benchmark_dir: Path) -> None:
        """Texto largo (70-80 palabras) debe sintetizar en <10s."""
        _warmup(_get_tts_service(), benchmark_dir)
        self._ejecutar_benchmark(self.TEXTO_LARGO, 10.0, benchmark_dir)
