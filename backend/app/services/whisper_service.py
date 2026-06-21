"""Servicio de transcripcion de audio con Whisper.

Implementa un wrapper thread-safe sobre openai-whisper con:
- Carga lazy del modelo (primera transcripcion descarga el modelo)
- Singleton por nombre de modelo (evita tener multiples copias en memoria)
- Soporte para CPU, CUDA y MPS (Apple Silicon)
- Manejo de errores: archivo corrupto, audio vacio, timeout

Nota: `import whisper` es lazy (dentro de _load_model) para que CI
pueda ejecutar tests sin tener openai-whisper instalado. Los tests
mockean WhisperService a nivel de clase, no de modulo.
"""

import logging
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


class WhisperModel(Protocol):
    """Protocolo para el modelo Whisper.

    Define solo los metodos que usamos, permitiendo type checking
    sin importar openai-whisper directamente (lazy import).
    """

    def transcribe(
        self,
        audio: str,
        *,
        language: str,
        fp16: bool,
        task: str,
        verbose: bool,
    ) -> dict[str, object]: ...


# Cache de modelos cargados: {model_name: WhisperModel}
_model_cache: dict[str, WhisperModel] = {}
_model_cache_lock = threading.Lock()


def _get_device() -> str:
    """Detecta el mejor dispositivo disponible para Whisper.

    Orden de preferencia:
    1. CUDA (GPU NVIDIA)
    2. MPS (Apple Silicon)
    3. CPU (fallback universal)

    Si torch no esta instalado (CI, tests), fallback silencioso a CPU.

    Returns:
        Nombre del dispositivo: "cuda", "mps" o "cpu".
    """
    try:
        import torch
    except ImportError:
        logger.debug("torch no disponible — dispositivo: CPU")
        return "cpu"

    if torch.cuda.is_available():
        logger.info("Dispositivo detectado: CUDA")
        return "cuda"
    # MPS tiene limitaciones con fp16 en algunos modelos
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        logger.info("Dispositivo detectado: MPS (Apple Silicon)")
        return "mps"
    logger.info("Dispositivo detectado: CPU")
    return "cpu"


class WhisperService:
    """Servicio de transcripcion de audio con Whisper.

    Uso:
        service = WhisperService()
        result = service.transcribe("/tmp/audio.wav")
        print(result["text"])

    El modelo se carga bajo demanda (lazy loading). Si el modelo no existe
    en cache, se descarga automaticamente desde los servers de OpenAI.

    Es thread-safe para uso con asyncio.to_thread().
    """

    def __init__(self, model_name: str | None = None) -> None:
        """Inicializa el servicio de transcripcion.

        Args:
            model_name: Nombre del modelo Whisper ("tiny", "base", "small", etc.).
                       Si es None, usa WHISPER_MODEL del settings.
        """
        self._model_name = model_name or settings.whisper_model or "small"
        self._device = _get_device()
        self._language = "es"  # Fijo: espanol chileno

    @staticmethod
    def _import_whisper() -> ModuleType:
        """Importa el modulo whisper bajo demanda.

        Separado como metodo estatico para que los tests puedan mockearlo
        via @patch.object(WhisperService, '_import_whisper') sin necesitar
        openai-whisper instalado.

        Returns:
            El modulo whisper (lazy import) como ModuleType.
        """
        import whisper

        return whisper  # type: ignore[no-any-return]

    def _load_model(self) -> WhisperModel:
        """Carga el modelo Whisper en cache (singleton por nombre de modelo).

        Thread-safe: usa _model_cache_lock para evitar que dos threads carguen
        el modelo simultaneamente durante cold start (P1 del code review).

        El `import whisper` es lazy (dentro de _import_whisper) para que CI
        pueda ejecutar tests sin openai-whisper instalado.

        Returns:
            Instancia de Whisper cargada (tipo Any por import lazy).
        """
        with _model_cache_lock:
            if self._model_name not in _model_cache:
                whisper = self._import_whisper()

                logger.info(
                    "Cargando modelo Whisper '%s' en %s (primer uso)...",
                    self._model_name,
                    self._device,
                )
                start = time.monotonic()

                # Whisper se carga con fp16=False en CPU/MPS para evitar errores
                # de precision. CUDA puede usar fp16=True para mayor velocidad.
                fp16 = self._device == "cuda"
                model = whisper.load_model(
                    self._model_name,
                    device=self._device,
                    download_root=settings.whisper_model_path or None,
                )

                elapsed = time.monotonic() - start
                logger.info(
                    "Modelo Whisper '%s' cargado en %.1fs — device=%s fp16=%s",
                    self._model_name,
                    elapsed,
                    self._device,
                    fp16,
                )
                _model_cache[self._model_name] = model
            return _model_cache[self._model_name]

    def transcribe(self, audio_path: str | Path) -> dict[str, object]:
        """Transcribe un archivo de audio a texto.

        Args:
            audio_path: Ruta al archivo .wav 16kHz mono.

        Returns:
            Dict con:
                "text": str — texto transcrito
                "language": str — idioma detectado
                "segments": list — segmentos con timestamps
                "duration_ms": int — duracion procesada

        Raises:
            FileNotFoundError: Si el archivo de audio no existe.
            RuntimeError: Si Whisper falla al transcribir.
            ValueError: Si el archivo esta vacio o es invalido.
        """
        path = Path(audio_path)

        # Validaciones defensivas
        if not path.exists():
            raise FileNotFoundError(f"Archivo de audio no encontrado: {path}")
        if path.stat().st_size == 0:
            raise ValueError(f"Archivo de audio vacio: {path}")

        start = time.monotonic()

        logger.info(
            "Transcribiendo audio — path=%s size_bytes=%d model=%s",
            path.name,
            path.stat().st_size,
            self._model_name,
        )

        try:
            model = self._load_model()
            result = model.transcribe(
                str(path),
                language=self._language,
                fp16=self._device == "cuda",
                task="transcribe",
                verbose=False,
            )
        except (RuntimeError, OSError, ValueError) as exc:
            elapsed = time.monotonic() - start
            logger.exception(
                "Whisper fallo al transcribir — path=%s elapsed_ms=%d error=%s",
                path.name,
                int(elapsed * 1000),
                exc,
            )
            raise RuntimeError(f"Error de transcripcion Whisper: {exc}") from exc

        elapsed_ms = int((time.monotonic() - start) * 1000)
        text = str(result.get("text") or "").strip()

        logger.info(
            "Audio transcrito — path=%s text_len=%d words=%d language=%s elapsed_ms=%d",
            path.name,
            len(text),
            len(text.split()),
            result.get("language", "es"),
            elapsed_ms,
        )

        return {
            "text": text,
            "language": result.get("language", "es"),
            "segments": result.get("segments", []),
            "duration_ms": elapsed_ms,
        }

    @property
    def model_name(self) -> str:
        """Nombre del modelo Whisper configurado."""
        return self._model_name

    @property
    def is_loaded(self) -> bool:
        """Indica si el modelo ya fue cargado en memoria."""
        return self._model_name in _model_cache


def clear_model_cache() -> None:
    """Limpia la cache de modelos. Util en tests para forzar recarga."""
    _model_cache.clear()
    logger.debug("Cache de modelos Whisper limpiada")
