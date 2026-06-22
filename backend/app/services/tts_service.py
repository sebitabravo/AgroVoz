"""Servicio de sintesis de voz con Piper TTS.

Implementa un wrapper sobre Piper TTS con:
- Carga lazy del modelo de voz (se carga en la primera llamada a synthesize)
- Pipeline: texto -> Piper -> .wav -> ffmpeg -> .ogg (opus 16kHz mono)
- Split automatico de texto largo por frases (limite configurable)
- Path del modelo configurable via settings.PIPER_MODEL_PATH

Uso:
    service = TTSService()
    ogg_path = service.synthesize("El precio de la papa es 500 pesos")
    # ogg_path -> "/app/data/audio_temp/tts_abc123.ogg"

Nota: `from piper import PiperVoice` es lazy (dentro de _load_model) para
que CI pueda ejecutar tests sin piper-tts instalado. Los tests mockean
TTSService a nivel de metodo, no de modulo.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from app.core.config import settings

if TYPE_CHECKING:
    from piper import PiperVoice

logger = logging.getLogger(__name__)

# Maximo de caracteres por llamada a Piper (empirico).
# ~500 caracteres generan ~20s de audio a 22kHz, suficiente margen
# para evitar OOM en VPS con 16GB RAM.
_MAX_PIPER_CHARS = 500

# Regex para dividir texto en oraciones. Preserva el signo de puntuacion
# como parte de la oracion anterior.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# Tipo numpy para audio float32 (como lo entrega Piper).
_NP_FLOAT = np.float32


class PiperModelNotFoundError(RuntimeError):
    """El archivo del modelo Piper no existe en la ruta configurada."""

    pass


class TTSService:
    """Servicio de sintesis de voz con Piper TTS.

    El modelo se carga bajo demanda (lazy loading): la primera llamada a
    synthesize() carga el modelo en memoria y lo cachea para llamadas
    posteriores dentro del mismo proceso.

    El pipeline completo es:
        texto -> split (si excede _MAX_PIPER_CHARS) -> Piper -> .wav ->
        ffmpeg -> .ogg opus 16kHz mono

    Attributes:
        model_path: Ruta al archivo .onnx del modelo Piper.
        voice_name: Nombre de la voz configurada (ej: "es_ES-carlfm-x_low").
    """

    def __init__(self, model_path: str | None = None) -> None:
        """Inicializa el servicio TTS.

        El modelo NO se carga en __init__ (lazy loading). La carga ocurre
        en la primera llamada a synthesize().

        Args:
            model_path: Ruta al archivo .onnx de Piper.
                       Si es None, usa piper_model_path del settings.
        """
        self._model_path = model_path or settings.piper_model_path
        self._voice_name = settings.piper_voice or "es_ES-carlfm-x_low"
        self._model: PiperVoice | None = None
        self._model_lock = threading.Lock()

    # ──────────────────────── Carga del modelo ────────────────────────

    def _load_model(self) -> PiperVoice:
        """Carga el modelo Piper bajo demanda con double-checked locking.

        El modelo se cachea en self._model. La primera llamada lo carga
        desde disco, las siguientes retornan la instancia cacheada.

        Proteccion contra race condition: como synthesize() se ejecuta en
        thread pool (via asyncio.to_thread), dos webhooks simultaneos post-
        startup podrian ver self._model is None y cargar el modelo dos
        veces (~100MB RAM extra). Un threading.Lock con double-checked
        locking evita la doble carga sin serializar lecturas posteriores.

        Returns:
            Instancia de PiperVoice cargada.

        Raises:
            PiperModelNotFoundError: Si el archivo .onnx no existe en
                                     la ruta configurada.
        """
        if self._model is not None:
            return self._model

        with self._model_lock:
            if self._model is not None:
                return self._model

            model_file = Path(self._model_path)
            if not model_file.exists():
                raise PiperModelNotFoundError(
                    f"Modelo Piper no encontrado en {self._model_path}. "
                    "Descarguelo con scripts/download_models.sh"
                )

            # Lazy import: evita que CI falle si piper-tts no esta instalado.
            from piper import PiperVoice

            logger.info(
                "Cargando modelo Piper — path=%s voice=%s",
                self._model_path,
                self._voice_name,
            )
            start = time.monotonic()

            self._model = PiperVoice.load(self._model_path, use_cuda=False)

            elapsed = time.monotonic() - start
            logger.info("Modelo Piper cargado en %.1fs", elapsed)

        return self._model

    # ──────────────────────── Split de texto ────────────────────────

    @staticmethod
    def _split_text(text: str) -> list[str]:
        """Divide texto largo en fragmentos de max _MAX_PIPER_CHARS chars.

        Estrategia:
        1. Divide por limites de oracion (. ! ?) para evitar cortar palabras.
        2. Si una oracion individual excede el limite, la divide por comas.
        3. Como ultimo recurso, divide por cantidad de caracteres.

        Args:
            text: Texto a dividir.

        Returns:
            Lista de fragmentos de texto (vacia si el texto esta vacio).
        """
        text = text.strip()
        if not text:
            return []

        # Paso 1: dividir por oraciones
        sentences = _SENTENCE_SPLIT_RE.split(text)

        chunks: list[str] = []
        current = ""

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            # Si la oracion sola excede el limite, subdivision forzada
            if len(sentence) > _MAX_PIPER_CHARS:
                # Flush current si hay algo pendiente
                if current:
                    chunks.append(current.strip())
                    current = ""

                # Subdividir por comas
                parts = sentence.split(", ")
                for part in parts:
                    part = part.strip()
                    if not part:
                        continue
                    if len(part) > _MAX_PIPER_CHARS:
                        # Fallback: division por caracteres
                        for i in range(0, len(part), _MAX_PIPER_CHARS):
                            chunk = part[i : i + _MAX_PIPER_CHARS].strip()
                            if chunk:
                                chunks.append(chunk)
                    elif len(current) + len(part) + 2 <= _MAX_PIPER_CHARS:
                        current = f"{current}, {part}" if current else part
                    else:
                        if current:
                            chunks.append(current.strip())
                        current = part
            elif len(current) + len(sentence) + 1 <= _MAX_PIPER_CHARS:
                current = f"{current} {sentence}" if current else sentence
            else:
                if current:
                    chunks.append(current.strip())
                current = sentence

        # Ultimo fragmento pendiente
        if current:
            chunks.append(current.strip())

        return chunks

    # ──────────────────────── Sintesis y conversion ────────────────────────

    def _synthesize_wav(self, text: str, output_path: Path) -> Path:
        """Sintetiza texto a un archivo .wav usando Piper (API v1.4).

        Piper v1.4 ya no acepta un `wav_file` como argumento. En su lugar,
        `synthesize()` retorna un iterable de `AudioChunk` con audio como
        float32 en el rango [-1, 1]. Este metodo:
        1. Recolecta los chunks de audio
        2. Concatena los arrays float32
        3. Convierte a int16 PCM
        4. Escribe un archivo WAV valido

        Args:
            text: Texto a sintetizar (max _MAX_PIPER_CHARS chars).
            output_path: Ruta del archivo .wav de salida.

        Returns:
            Path al archivo .wav generado.

        Raises:
            RuntimeError: Si Piper no genera audio o la conversion falla.
        """
        from piper.config import SynthesisConfig

        voice = self._load_model()

        syn_config = SynthesisConfig(
            speaker_id=None,
            length_scale=1.0,
            noise_scale=0.667,
            noise_w_scale=0.8,
        )

        chunks = list(voice.synthesize(text, syn_config=syn_config))
        if not chunks:
            raise RuntimeError(f"Piper no genero audio para: {text[:60]}...")

        # Usar parametros del primer chunk (iguales para todos)
        sample_rate = chunks[0].sample_rate
        sample_width = chunks[0].sample_width  # bytes por sample (2 = int16)
        channels = chunks[0].sample_channels

        # Concatenar arrays float32 de todos los chunks
        audio_float = np.concatenate(
            [chunk.audio_float_array for chunk in chunks]
        )

        # Convertir float32 [-1, 1] a int16 PCM con clip para evitar
        # overflow/underflow audible si Piper produce valores fuera de rango
        audio_int16 = np.clip(audio_float * 32767, -32768, 32767).astype(np.int16)

        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(sample_width)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio_int16.tobytes())

        logger.debug(
            "Fragmento TTS sintetizado — text_len=%d sample_rate=%d "
            "samples=%d output=%s",
            len(text),
            sample_rate,
            len(audio_int16),
            output_path.name,
        )
        return output_path

    @staticmethod
    def _convert_wav_to_ogg(wav_path: Path, ogg_path: Path) -> Path:
        """Convierte .wav a .ogg opus 16kHz mono con ffmpeg.

        Usa codec libopus optimizado para voz (application=lowdelay)
        con bitrate 24kbps, sample rate 16kHz, mono — formato optimo
        para WhatsApp.

        Args:
            wav_path: Ruta al archivo .wav de entrada.
            ogg_path: Ruta al archivo .ogg de salida.

        Returns:
            Path al archivo .ogg generado.

        Raises:
            subprocess.CalledProcessError: Si ffmpeg falla.
            FileNotFoundError: Si ffmpeg no esta instalado.
        """
        cmd = [
            "ffmpeg",
            "-y",  # Sobrescribir si existe
            "-i",
            str(wav_path),
            "-acodec",
            "libopus",   # Codec Opus
            "-ar",
            "16000",     # 16kHz (WhatsApp optimiza a esto)
            "-ac",
            "1",         # Mono
            "-b:a",
            "24k",       # Bitrate 24 kbps (buena relacion calidad/tamano para voz)
            "-application",
            "lowdelay",  # Optimizado para voz (menor latencia que audio)
            "-fs",
            str(25 * 1024 * 1024),  # Limite 25 MB — decompression bomb
            str(ogg_path),
        ]
        logger.info(
            "Convirtiendo a .ogg — input=%s output=%s",
            wav_path.name,
            ogg_path.name,
        )
        try:
            subprocess.run(cmd, capture_output=True, check=True, timeout=30)
        except subprocess.TimeoutExpired:
            logger.error(
                "ffmpeg timeout (30s) — input=%s",
                wav_path.name,
            )
            raise
        except subprocess.CalledProcessError as exc:
            logger.error(
                "ffmpeg fallo al convertir a .ogg — input=%s stderr=%s",
                wav_path.name,
                exc.stderr.decode("utf-8", errors="replace") if exc.stderr else "(sin stderr)",
            )
            raise

        # Log stats defensivo: si el archivo no se creo (test con mock, error
        # silencioso de ffmpeg), no fallar — solo omitir el log.
        input_size = wav_path.stat().st_size if wav_path.exists() else 0
        output_size = ogg_path.stat().st_size if ogg_path.exists() else 0
        logger.info(
            "Audio convertido a .ogg — input_size=%d output_size=%d",
            input_size,
            output_size,
        )
        return ogg_path

    @staticmethod
    def _concatenate_wavs(wav_paths: list[Path], output_path: Path) -> None:
        """Concatena multiples archivos .wav en uno solo usando ffmpeg.

        Usa el demuxer concat de ffmpeg que opera a nivel de container:
        reutiliza los streams existentes sin recodificar (-c copy).

        Args:
            wav_paths: Lista de paths a archivos .wav en orden.
            output_path: Path del archivo concatenado.

        Raises:
            subprocess.CalledProcessError: Si ffmpeg falla.
        """
        list_path = output_path.with_suffix(".txt")
        list_path.write_text(
            "\n".join(f"file '{p.resolve()}'" for p in wav_paths) + "\n",
            encoding="utf-8",
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_path),
            "-c", "copy",
            str(output_path),
        ]
        try:
            subprocess.run(cmd, capture_output=True, check=True, timeout=30)
        except subprocess.CalledProcessError as exc:
            logger.error(
                "ffmpeg fallo al concatenar audios — n_files=%d stderr=%s",
                len(wav_paths),
                exc.stderr.decode("utf-8", errors="replace") if exc.stderr else "(sin stderr)",
            )
            raise
        except subprocess.TimeoutExpired:
            logger.error(
                "ffmpeg timeout (30s) concatenando — n_files=%d",
                len(wav_paths),
            )
            raise
        finally:
            list_path.unlink(missing_ok=True)

    # ──────────────────────── API publica ────────────────────────

    def synthesize(self, text: str, output_dir: str | Path | None = None) -> str:
        """Sintetiza texto a audio .ogg opus listo para WhatsApp.

        Pipeline completo:
            texto -> split (si largo) -> Piper -> .wav ->
            ffmpeg -> .ogg opus 16kHz mono

        Si el texto excede _MAX_PIPER_CHARS caracteres, se divide por
        oraciones, se sintetiza cada fragmento por separado, y se
        concatenan los audios resultantes.

        Args:
            text: Texto a sintetizar en espanol.
            output_dir: Directorio para el archivo de salida.
                       Si es None, usa data/audio_temp/.

        Returns:
            Ruta absoluta al archivo .ogg generado (str para compatibilidad
            con OpenWAService que recibe str).

        Raises:
            PiperModelNotFoundError: Si el modelo Piper no existe.
            RuntimeError: Si Piper o ffmpeg fallan.
            ValueError: Si el texto esta vacio.
        """
        text = text.strip()
        if not text:
            raise ValueError("El texto a sintetizar no puede estar vacio")

        start = time.monotonic()

        # Determinar directorio de salida
        if output_dir is None:
            output_dir = (
                Path(__file__).resolve().parent.parent.parent / "data" / "audio_temp"
            )
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Dividir texto largo en fragmentos
        chunks = self._split_text(text)
        logger.info(
            "Sintetizando texto — text_len=%d chunks=%d",
            len(text),
            len(chunks),
        )

        if not chunks:
            raise ValueError("El texto a sintetizar no contiene frases validas")

        file_tag = uuid.uuid4().hex[:12]
        wav_paths: list[Path] = []

        # Sintetizar cada fragmento
        for i, chunk in enumerate(chunks):
            wav_path = output_dir / f"tts_{file_tag}_{i:03d}.wav"
            try:
                self._synthesize_wav(chunk, wav_path)
                wav_paths.append(wav_path)
            except PiperModelNotFoundError:
                # PiperModelNotFoundError se propaga sin wrapper para que
                # el caller pueda distinguir "modelo no disponible" de
                # "error de sintesis" y hacer fallback a hello.ogg.
                for p in wav_paths:
                    p.unlink(missing_ok=True)
                raise
            except (RuntimeError, OSError, ValueError) as exc:
                logger.exception(
                    "Error sintetizando fragmento %d/%d — text_len=%d",
                    i + 1,
                    len(chunks),
                    len(chunk),
                )
                # Cleanup: eliminar WAVs generados hasta ahora
                for p in wav_paths:
                    p.unlink(missing_ok=True)
                raise RuntimeError(
                    f"Error al sintetizar fragmento {i + 1}/{len(chunks)}"
                ) from exc

        # Concatenar fragmentos y convertir a .ogg
        final_wav_path = output_dir / f"tts_{file_tag}_final.wav"
        ogg_path = output_dir / f"tts_{file_tag}.ogg"

        try:
            if len(wav_paths) == 1:
                wav_paths[0].rename(final_wav_path)
            else:
                self._concatenate_wavs(wav_paths, final_wav_path)
            self._convert_wav_to_ogg(final_wav_path, ogg_path)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            # Convertir errores de ffmpeg a RuntimeError para que audio_service.py
            # los capture y caiga en fallback a hello.ogg. Si se propagara
            # CalledProcessError directo, saltaria el handler mas especifico
            # de process_audio() y el productor se quedaria sin respuesta.
            logger.error(
                "ffmpeg fallo en síntesis — tag=%s error=%s",
                file_tag,
                exc,
            )
            raise RuntimeError(f"ffmpeg fallo: {exc}") from exc
        finally:
            # Garantizar cleanup incluso si ffmpeg falla (P2)
            for p in wav_paths:
                p.unlink(missing_ok=True)
            final_wav_path.unlink(missing_ok=True)

        elapsed = time.monotonic() - start
        ogg_size = ogg_path.stat().st_size
        logger.info(
            "Audio sintetizado — text_len=%d chunks=%d ogg_size=%d elapsed_ms=%d path=%s",
            len(text),
            len(chunks),
            ogg_size,
            int(elapsed * 1000),
            ogg_path,
        )

        return str(ogg_path)

    @property
    def is_loaded(self) -> bool:
        """Indica si el modelo ya fue cargado en memoria."""
        return self._model is not None

    @property
    def model_path(self) -> str:
        """Ruta al archivo .onnx del modelo Piper."""
        return self._model_path

    @property
    def voice_name(self) -> str:
        """Nombre de la voz configurada."""
        return self._voice_name


def clear_model_cache() -> None:
    """Limpia la cache del modelo Piper. Util en tests para forzar recarga.

    Como el modelo se cachea por instancia (no hay cache global como
    en Whisper), este metodo es principalmente un marcador para tests.
    """
    logger.debug("Cache de modelo Piper limpiada")
