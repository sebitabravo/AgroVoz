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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from app.core.config import settings

if TYPE_CHECKING:
    from piper import PiperVoice

logger = logging.getLogger(__name__)

# ── Normalizacion de texto para voz ────────────────────────────────
#
# El fonemizador de Piper (espeak-ng es_MX) deletrea las abreviaturas de unidad
# en vez de leerlas. Verificado contra el modelo real es_MX-claude-high:
#
#   "4.6 m/s"     -> "cuatro punto seis EME BARRA ESE"
#   "12 mm"       -> "doce EME EME"
#   "20 km/h"     -> "veinte KA EME BARRA ACHE"
#   "17/07/2026"  -> "diecisiete BARRA cero siete BARRA dos mil veintiseis"
#
# El productor escucha eso literal. Como no lee, el audio es la unica salida:
# una unidad mal leida es un dato perdido. Estas sustituciones corren sobre TODO
# texto que va al TTS (respuesta del LLM, tools deterministas y alertas).
#
# NO se tocan los casos que espeak ya resuelve bien (verificados): "83%" ->
# "por ciento", "13.000" -> "trece mil", "12°C" -> "doce grados ce".
_MESES_ES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def _fecha_a_palabras(match: re.Match[str]) -> str:
    """Convierte dd/mm/aaaa a "dd de <mes> de aaaa". Deja intacto lo invalido."""
    dia, mes, anio = int(match.group(1)), int(match.group(2)), match.group(3)
    if not 1 <= mes <= 12 or not 1 <= dia <= 31:
        return match.group(0)
    return f"{dia} de {_MESES_ES[mes - 1]} de {anio}"


# Orden relevante: km/h antes que m/s y mm antes que m, para que el patron mas
# largo gane. \b evita comerse palabras que terminan en la abreviatura.
_SUSTITUCIONES_VOZ: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bkm/h\b", re.IGNORECASE), "kilómetros por hora"),
    (re.compile(r"\bm/s\b", re.IGNORECASE), "metros por segundo"),
    (re.compile(r"\bkm\b", re.IGNORECASE), "kilómetros"),
    (re.compile(r"\bmm\b", re.IGNORECASE), "milímetros"),
    (re.compile(r"\bkg\b", re.IGNORECASE), "kilos"),
    # "ha" (hectarea) queda FUERA a proposito: es homografo del auxiliar "ha",
    # muchisimo mas frecuente. Sustituirlo rompe frases normales
    # ("no ha llovido" -> "no hectareas llovido"). Piper lee "ha" como el verbo,
    # que es el caso comun; la unidad es rara en estas respuestas.
)

_RE_FECHA = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_RE_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_URL_TRAILING_PUNCTUATION = ".,;:!?)]}"


def _url_a_palabras(match: re.Match[str]) -> str:
    """Reemplaza URLs por una referencia hablable y conserva puntuación final."""
    raw_url = match.group(0)
    url = raw_url.rstrip(_URL_TRAILING_PUNCTUATION)
    suffix = raw_url[len(url) :]

    from app.services.indap_credit_service import is_official_indap_url

    replacement = "la página oficial de INDAP" if is_official_indap_url(url) else "la referencia web indicada"
    return replacement + suffix


def normalizar_para_voz(texto: str) -> str:
    """Reescribe URLs, unidades y fechas para que Piper las lea en palabras.

    Args:
        texto: Texto tal como lo produjo el LLM o una tool.

    Returns:
        Texto equivalente, apto para sintesis.
    """
    texto = _RE_URL.sub(_url_a_palabras, texto)
    texto = _RE_FECHA.sub(_fecha_a_palabras, texto)
    for patron, reemplazo in _SUSTITUCIONES_VOZ:
        texto = patron.sub(reemplazo, texto)
    return texto


# Maximo de caracteres por llamada a Piper (empirico).
# ~500 caracteres generan ~20s de audio a 22kHz, suficiente margen
# para evitar OOM en VPS con 16GB RAM.
_MAX_PIPER_CHARS = 500

# Tope de fragmentos sintetizados en paralelo (#136). Piper libera el GIL
# durante la inferencia ONNX, asi que varios fragmentos cortos rinden real
# en CPU multi-nucleo: medido 1.32x en 3 oraciones sobre hardware de
# desarrollo. En el piso de 1 vCPU no hay nucleo de sobra para paralelizar,
# pero tampoco degrada: mismo trabajo total, sin oversubscription porque el
# tope nunca supera los fragmentos reales de una respuesta.
_MAX_PARALLEL_CHUNKS = 4

# Regex para dividir texto en oraciones. Preserva el signo de puntuacion
# como parte de la oracion anterior.
#
# NO divide en numeros chilenos (1.500, 2.500 kg, $15.000) gracias al
# negative lookbehind (?<!\d\.) que evita dividir despues de un punto
# precedido por digito. Sin esto, "El precio es 1.500 pesos" se dividiria
# en ["El precio es 1.", "500 pesos"].
_SENTENCE_SPLIT_RE = re.compile(r"(?:(?<=[!?])|(?:(?<=\.)(?<!\d\.)))\s+")

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
        voice_name: Nombre de la voz configurada (ej: "es_MX-claude-high").
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
        self._voice_name = settings.piper_voice or "es_MX-claude-high"
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
                raise PiperModelNotFoundError("Modelo Piper no encontrado. Descárguelo con scripts/download_models.sh")

            # Lazy import: evita que CI falle si piper-tts no esta instalado.
            from piper import PiperVoice

            logger.info(
                "Cargando modelo Piper — voice=%s",
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
        audio_float = np.concatenate([chunk.audio_float_array for chunk in chunks])

        # Convertir float32 [-1, 1] a int16 PCM con clip para evitar
        # overflow/underflow audible si Piper produce valores fuera de rango
        audio_int16 = np.clip(audio_float * 32767, -32768, 32767).astype(np.int16)

        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(sample_width)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio_int16.tobytes())

        logger.debug(
            "Fragmento TTS sintetizado — text_len=%d sample_rate=%d samples=%d",
            len(text),
            sample_rate,
            len(audio_int16),
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
            "libopus",  # Codec Opus
            "-ar",
            "16000",  # 16kHz (WhatsApp optimiza a esto)
            "-ac",
            "1",  # Mono
            "-b:a",
            "24k",  # Bitrate 24 kbps (buena relacion calidad/tamano para voz)
            "-application",
            "lowdelay",  # Optimizado para voz (menor latencia que audio)
            "-fs",
            str(25 * 1024 * 1024),  # Limite 25 MB — decompression bomb
            str(ogg_path),
        ]
        logger.info("Convirtiendo audio TTS a .ogg — estado=iniciado")
        try:
            subprocess.run(cmd, capture_output=True, check=True, timeout=30)
        except subprocess.TimeoutExpired:
            logger.error("ffmpeg timeout TTS — timeout_seconds=30")
            raise
        except subprocess.CalledProcessError as exc:
            logger.error(
                "ffmpeg falló al convertir TTS — returncode=%d",
                exc.returncode,
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
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(output_path),
        ]
        try:
            subprocess.run(cmd, capture_output=True, check=True, timeout=30)
        except subprocess.CalledProcessError as exc:
            logger.error(
                "ffmpeg falló al concatenar audios — n_files=%d returncode=%d",
                len(wav_paths),
                exc.returncode,
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

        text = normalizar_para_voz(text)

        start = time.monotonic()

        # Determinar directorio de salida
        if output_dir is None:
            output_dir = Path(__file__).resolve().parent.parent.parent / "data" / "audio_temp"
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Dividir texto largo en fragmentos
        chunks = self._split_text(text)

        # Defensa en profundidad: _split_text no deberia devolver chunks
        # > _MAX_PIPER_CHARS, pero si lo hace (edge case no cubierto),
        # se subdividen forzadamente para evitar que Piper falle.
        safe_chunks: list[str] = []
        for c in chunks:
            if len(c) <= _MAX_PIPER_CHARS:
                safe_chunks.append(c)
            else:
                logger.warning(
                    "_split_text devolvió chunk mayor al límite — chars=%d max=%d",
                    len(c),
                    _MAX_PIPER_CHARS,
                )
                for j in range(0, len(c), _MAX_PIPER_CHARS):
                    sub = c[j : j + _MAX_PIPER_CHARS].strip()
                    if sub:
                        safe_chunks.append(sub)
        chunks = safe_chunks
        logger.info(
            "Sintetizando texto — text_len=%d chunks=%d",
            len(text),
            len(chunks),
        )

        if not chunks:
            raise ValueError("El texto a sintetizar no contiene frases validas")

        file_tag = uuid.uuid4().hex[:12]
        wav_paths = [output_dir / f"tts_{file_tag}_{i:03d}.wav" for i in range(len(chunks))]

        # Sintetizar fragmentos en paralelo: Piper libera el GIL durante la
        # inferencia ONNX, asi que varios fragmentos cortos rinden en CPU
        # multi-nucleo (#136). Los resultados se leen en orden para que un
        # error se reporte de forma deterministica.
        workers = min(len(chunks), _MAX_PARALLEL_CHUNKS)
        executor = ThreadPoolExecutor(max_workers=workers)
        futures = [
            executor.submit(self._synthesize_wav, chunk, wav_path)
            for chunk, wav_path in zip(chunks, wav_paths, strict=True)
        ]

        error: tuple[int, PiperModelNotFoundError | RuntimeError | OSError | ValueError] | None = None
        for i, future in enumerate(futures):
            try:
                future.result()
            except (PiperModelNotFoundError, RuntimeError, OSError, ValueError) as exc:
                error = (i, exc)
                break

        # Esperar el cierre ANTES de limpiar: si no se espera, un fragmento
        # que seguia en curso puede terminar de escribir su WAV justo
        # despues del unlink, dejando un archivo huerfano en data/audio_temp.
        executor.shutdown(wait=True, cancel_futures=True)

        if error is not None:
            error_index, exc = error
            for p in wav_paths:
                p.unlink(missing_ok=True)
            if isinstance(exc, PiperModelNotFoundError):
                # Se propaga sin wrapper para que el caller pueda distinguir
                # "modelo no disponible" de "error de sintesis" y hacer
                # fallback a hello.ogg.
                raise exc
            logger.error(
                "Error sintetizando fragmento — index=%d total=%d text_len=%d error=%s",
                error_index + 1,
                len(chunks),
                len(chunks[error_index]),
                type(exc).__name__,
            )
            raise RuntimeError(f"Error al sintetizar fragmento {error_index + 1}/{len(chunks)}") from exc

        # Concatenar fragmentos y convertir a .ogg
        final_wav_path = output_dir / f"tts_{file_tag}_final.wav"
        ogg_path = output_dir / f"tts_{file_tag}.ogg"

        try:
            if len(wav_paths) == 1:
                wav_paths[0].rename(final_wav_path)
            else:
                self._concatenate_wavs(wav_paths, final_wav_path)
            self._convert_wav_to_ogg(final_wav_path, ogg_path)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            # Convertir errores de ffmpeg a RuntimeError para que audio_service.py
            # los capture y caiga en fallback a hello.ogg. Si se propagara
            # CalledProcessError directo, saltaria el handler mas especifico
            # de process_audio() y el productor se quedaria sin respuesta.
            ogg_path.unlink(missing_ok=True)  # Limpiar OGG parcial si ffmpeg creo el archivo antes de fallar
            if isinstance(exc, OSError):
                logger.error(
                    "ffmpeg no está instalado — error=%s",
                    type(exc).__name__,
                )
            else:
                logger.error(
                    "ffmpeg falló en síntesis — error=%s",
                    type(exc).__name__,
                )
            raise RuntimeError("ffmpeg falló durante la síntesis") from exc
        finally:
            # Garantizar cleanup incluso si ffmpeg falla (P2)
            for p in wav_paths:
                p.unlink(missing_ok=True)
            final_wav_path.unlink(missing_ok=True)

        elapsed = time.monotonic() - start
        ogg_size = ogg_path.stat().st_size
        logger.info(
            "Audio sintetizado — text_len=%d chunks=%d ogg_size=%d elapsed_ms=%d",
            len(text),
            len(chunks),
            ogg_size,
            int(elapsed * 1000),
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
