"""Servicio de transcripcion de audio con Whisper.

Implementa un wrapper thread-safe con dos backends intercambiables:
- `faster` (default): faster-whisper sobre CTranslate2, cuantizado INT8. Corre
  el mismo modelo `small` varias veces mas rapido en CPU y no necesita torch.
- `openai`: openai-whisper, el camino original, como salida de emergencia.

Ambos comparten carga lazy, singleton por modelo, deteccion de dispositivo y
manejo de errores. Whisper es el tramo mas caro del camino de voz (~9 s de los
~11 s en 1 vCPU), asi que el backend se elige por configuracion y no por codigo:
si INT8 degrada la transcripcion rural, se vuelve atras sin desplegar codigo.

Nota: los imports de ambas librerias son lazy para que CI pueda ejecutar tests
sin tenerlas instaladas. Los tests mockean WhisperService a nivel de clase.
"""

import logging
import os
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)

# Tope de hilos de CTranslate2. Sobresuscribir degrada la inferencia en CPU en
# vez de acelerarla, y el piso soportado es 1 vCPU.
_MAX_CPU_THREADS = 4


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
        initial_prompt: str,
        temperature: tuple[float, ...],
    ) -> dict[str, object]: ...


# Contexto que se le pasa a Whisper antes de transcribir. El decoder lo toma
# como texto previo de la conversacion y sesga la busqueda hacia ese vocabulario.
#
# Sin esto, Whisper small confunde terminos del dominio con palabras comunes:
# "va a llover manana" salia como "vaya chubes manana", y la consulta quedaba
# sin clasificar. El productor no lee, asi que una transcripcion mala no se
# puede corregir: es una consulta perdida.
#
# Se nombran los productos y mercados reales de ODEPA, las comunas del piloto y
# los verbos tipicos de una consulta de precio o clima.
_INITIAL_PROMPT = (
    "Consulta de un agricultor chileno por WhatsApp. "
    "Pregunta por precios de ODEPA o por el clima. "
    "Productos: papa, tomate, cebolla, zanahoria, lechuga, choclo, zapallo, "
    "ajo, poroto, arveja, betarraga, brócoli, coliflor, espinaca, manzana, trigo, avena. "
    "Mercados: Lo Valledor, Vega Central, Mapocho, Macroferia de Talca, "
    "Terminal La Palmera, Agro Chillán. "
    "Lugares: Traiguén, Temuco, Araucanía, Victoria, Lautaro, Angol. "
    "Frases: a cuánto está, cuánto vale, qué precio tiene, va a llover, "
    "cómo está el clima, va a helar, cuántos grados, vendí, me pagaron, "
    "avísame cuando, el kilo, el saco, la malla, la bandeja."
)

# Temperaturas del fallback de decodificacion. Whisper reintenta con
# temperatura mas alta cuando la salida es de baja confianza (repeticiones o
# logprob bajo). El default de openai-whisper ya es este; se explicita para que
# quede claro que el fallback esta activo y no se pierda en un refactor.
_TEMPERATURE_FALLBACK = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


# Cache de modelos cargados: {"backend:model_name": modelo}
# La clave incluye el backend porque faster-whisper y openai-whisper cargan el
# mismo `small` en objetos incompatibles.
_model_cache: dict[str, object] = {}
_model_cache_lock = threading.Lock()


def _cpu_threads() -> int:
    """Hilos para CTranslate2 sin sobresuscribir el host."""
    return max(1, min(os.cpu_count() or 1, _MAX_CPU_THREADS))


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
        self._backend = self._resolve_backend()

    def _resolve_backend(self) -> str:
        """Elige backend segun configuracion, degradando si falta la libreria.

        Un despliegue sin `faster-whisper` instalado no puede quedarse mudo: si
        el import falla se vuelve a openai-whisper y se deja constancia.
        """
        requested = settings.whisper_backend
        if requested != "faster":
            return requested
        try:
            self._import_faster_whisper()
        except ImportError:
            logger.warning("faster-whisper no disponible — se usa openai-whisper como respaldo")
            return "openai"
        return "faster"

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

    @staticmethod
    def _import_faster_whisper() -> ModuleType:
        """Importa faster_whisper bajo demanda, mockeable igual que el anterior."""
        import faster_whisper

        return faster_whisper  # type: ignore[no-any-return]

    @property
    def _cache_key(self) -> str:
        """Clave de cache que separa los modelos por backend."""
        return f"{self._backend}:{self._model_name}"

    def _compute_type(self) -> str:
        """Cuantizacion efectiva de CTranslate2 para el dispositivo detectado.

        INT8 es la que hace rendir el piso de 1 vCPU. En CUDA, pedir int8 puro
        desaprovecha la GPU, asi que se sube a la variante mixta.
        """
        configured = settings.whisper_compute_type
        if self._device == "cuda" and configured == "int8":
            return "int8_float16"
        return configured

    def _build_model(self) -> object:
        """Instancia el modelo del backend activo."""
        if self._backend == "faster":
            faster_whisper = self._import_faster_whisper()
            return faster_whisper.WhisperModel(
                self._model_name,
                # CTranslate2 no soporta MPS: Apple Silicon corre en CPU.
                device="cuda" if self._device == "cuda" else "cpu",
                compute_type=self._compute_type(),
                cpu_threads=_cpu_threads(),
                download_root=settings.whisper_model_path or None,
            )

        whisper = self._import_whisper()
        return whisper.load_model(
            self._model_name,
            device=self._device,
            download_root=settings.whisper_model_path or None,
        )

    def _load_model(self) -> object:
        """Carga el modelo del backend activo en cache (singleton por clave).

        Thread-safe: usa _model_cache_lock para evitar que dos threads carguen
        el modelo simultaneamente durante cold start (P1 del code review).

        Los imports son lazy para que CI pueda ejecutar tests sin ninguna de las
        dos librerias instaladas.

        Returns:
            Instancia del modelo cargado (tipo object por import lazy).
        """
        with _model_cache_lock:
            if self._cache_key not in _model_cache:
                logger.info(
                    "Cargando modelo Whisper '%s' en %s (primer uso) — backend=%s",
                    self._model_name,
                    self._device,
                    self._backend,
                )
                start = time.monotonic()
                model = self._build_model()
                elapsed = time.monotonic() - start
                logger.info(
                    "Modelo Whisper '%s' cargado en %.1fs — device=%s backend=%s compute=%s",
                    self._model_name,
                    elapsed,
                    self._device,
                    self._backend,
                    self._compute_type() if self._backend == "faster" else "fp32",
                )
                _model_cache[self._cache_key] = model
            return _model_cache[self._cache_key]

    def _transcribe_openai(self, model: object, path: Path) -> dict[str, object]:
        """Ejecuta openai-whisper y devuelve su dict nativo."""
        return model.transcribe(  # type: ignore[attr-defined,no-any-return]
            str(path),
            language=self._language,
            fp16=self._device == "cuda",
            task="transcribe",
            verbose=False,
            initial_prompt=_INITIAL_PROMPT,
            temperature=_TEMPERATURE_FALLBACK,
        )

    def _transcribe_faster(self, model: object, path: Path) -> dict[str, object]:
        """Ejecuta faster-whisper y normaliza su salida al dict de openai-whisper.

        `segments` llega como generador perezoso: la transcripcion real ocurre
        al consumirlo, no al llamar a `transcribe`. Por eso se materializa aca
        dentro, donde el bloque que llama sigue capturando los errores.
        """
        segments, info = model.transcribe(  # type: ignore[attr-defined]
            str(path),
            language=self._language,
            task="transcribe",
            initial_prompt=_INITIAL_PROMPT,
            temperature=_TEMPERATURE_FALLBACK,
        )
        materialized = [
            {
                "start": getattr(segment, "start", 0.0),
                "end": getattr(segment, "end", 0.0),
                "text": getattr(segment, "text", ""),
            }
            for segment in segments
        ]
        return {
            "text": "".join(str(segment["text"]) for segment in materialized),
            "language": getattr(info, "language", self._language),
            "segments": materialized,
        }

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
            raise ValueError("Archivo de audio vacío")

        start = time.monotonic()

        logger.info(
            "Transcribiendo audio — size_bytes=%d model=%s",
            path.stat().st_size,
            self._model_name,
        )

        try:
            model = self._load_model()
            result = (
                self._transcribe_faster(model, path)
                if self._backend == "faster"
                else self._transcribe_openai(model, path)
            )
        except (RuntimeError, OSError, ValueError) as exc:
            elapsed = time.monotonic() - start
            logger.error(
                "Whisper falló al transcribir — elapsed_ms=%d error=%s",
                int(elapsed * 1000),
                type(exc).__name__,
            )
            raise RuntimeError("Error de transcripción Whisper") from exc

        elapsed_ms = int((time.monotonic() - start) * 1000)
        text = str(result.get("text") or "").strip()

        logger.info(
            "Audio transcrito — text_len=%d words=%d language=%s elapsed_ms=%d",
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
    def device(self) -> str:
        """Dispositivo de inferencia detectado (cpu, cuda, mps)."""
        return self._device

    @property
    def backend(self) -> str:
        """Backend de transcripcion efectivo ("faster" u "openai")."""
        return self._backend

    @property
    def is_loaded(self) -> bool:
        """Indica si el modelo ya fue cargado en memoria."""
        return self._cache_key in _model_cache


def clear_model_cache() -> None:
    """Limpia la cache de modelos. Util en tests para forzar recarga."""
    _model_cache.clear()
    logger.debug("Cache de modelos Whisper limpiada")
