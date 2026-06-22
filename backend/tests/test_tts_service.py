"""Tests para el servicio TTS Piper (Issue #26).

Usa mocks para evitar descargar el modelo de voz (~100MB) en cada
ejecucion de test. Mockea PiperVoice.load y subprocess.run (ffmpeg).

El split de texto se prueba sin mocks (es logica pura de strings).
"""

import subprocess
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.services.tts_service import (
    _MAX_PIPER_CHARS,
    _SENTENCE_SPLIT_RE,
    PiperModelNotFoundError,
    TTSService,
)

# ───────────────────────── Fixtures compartidas ─────────────────────────

@pytest.fixture(autouse=True)
def _mock_ffmpeg() -> Generator[None, None, None]:
    """Mockea subprocess.run para que CI no necesite ffmpeg.

    Crea un archivo dummy en la ruta de salida para simular que ffmpeg
    genero el archivo correctamente. Tests que sobrescriben el mock de
    subprocess.run (ej: para simular errores de ffmpeg) lo heredan
    naturalmente.
    """
    def _mock_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        # Extraer el ultimo argumento que no es flag: es el output file
        output_path = None
        if isinstance(cmd, list) and "ffmpeg" in str(cmd[0]):
            for i in range(len(cmd) - 1, 0, -1):
                if not cmd[i].startswith("-"):
                    output_path = Path(cmd[i])
                    break

        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake audio content")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = b""
        mock_result.stderr = b""
        return mock_result

    with patch("app.services.tts_service.subprocess.run", side_effect=_mock_run):
        yield


@pytest.fixture
def tmp_audio_dir(tmp_path: Path) -> Path:
    """Directorio temporal para archivos de audio en tests."""
    audio_dir = tmp_path / "audio_temp"
    audio_dir.mkdir(parents=True, exist_ok=True)
    return audio_dir


def _create_fake_model_file(tmp_path: Path) -> Path:
    """Crea un archivo .voice ficticio para pruebas de carga de modelo.

    Piper espera un archivo .voice (que contiene el modelo ONNX +
    config JSON). Para testing, creamos un archivo dummy que existe
    pero no es un modelo valido de Piper.

    Returns:
        Path al archivo .voice ficticio.
    """
    model_path = tmp_path / "es_ES-carlfm-x_low.voice"
    model_path.write_text("fake model content")
    return model_path


def _fake_synthesize_text(
    text: str,
    syn_config: Any = None,
    include_alignments: bool = False,
) -> list[Any]:
    """Simula PiperVoice.synthesize() (API v1.4) retornando AudioChunks.

    Piper v1.4 retorna un iterable de AudioChunk con audio_float_array.
    Generamos 2 chunks de ~0.5s de silencio cada uno para simular
    el comportamiento real.

    Returns:
        Lista de objetos con interfaz AudioChunk (sample_rate, sample_width,
        sample_channels, audio_float_array).
    """
    import numpy as np

    sample_rate = 16000
    num_samples = sample_rate // 2  # ~0.5s de audio cada chunk
    silence = np.zeros(num_samples, dtype=np.float32)

    # Crear objetos con interfaz de AudioChunk
    class FakeAudioChunk:
        def __init__(self, sr: int, sw: int, ch: int, data: Any) -> None:
            self.sample_rate = sr
            self.sample_width = sw
            self.sample_channels = ch
            self.audio_float_array = data

    return [
        FakeAudioChunk(sample_rate, 2, 1, silence),
        FakeAudioChunk(sample_rate, 2, 1, silence),
    ]


@pytest.fixture
def fake_model_file(tmp_path: Path) -> Path:
    """Crea un archivo .voice ficticio y configura settings para usarlo.

    Returns:
        Path al archivo .voice.
    """
    return _create_fake_model_file(tmp_path)


@pytest.fixture
def mock_piper_voice(fake_model_file: Path) -> Mock:
    """Crea un mock de PiperVoice con synthesize() que produce WAV valido.

    Mockea PiperVoice.load() para retornar un objeto con metodo
    synthesize() que escribe un WAV valido de silencio.

    Returns:
        Mock de PiperVoice.
    """
    mock_voice = MagicMock()
    mock_voice.synthesize.side_effect = _fake_synthesize_text
    return mock_voice


# ───────────────────────── Tests de inicializacion ─────────────────────────


class TestTTSServiceInit:
    """Tests de inicializacion del servicio TTS."""

    def test_init_con_path_default(self) -> None:
        """Debe usar el path del settings si no se especifica."""
        service = TTSService()
        assert service.model_path is not None
        assert len(service.model_path) > 0

    def test_init_con_path_personalizado(self, tmp_path: Path) -> None:
        """Debe usar el path especificado."""
        custom_path = str(tmp_path / "mi_voz.voice")
        service = TTSService(model_path=custom_path)
        assert service.model_path == custom_path

    def test_init_no_carga_modelo(self) -> None:
        """El modelo NO debe cargarse en __init__ (lazy loading)."""
        service = TTSService()
        assert not service.is_loaded

    def test_init_voice_name_default(self) -> None:
        """Debe tener un nombre de voz configurado."""
        service = TTSService()
        assert service.voice_name is not None
        assert len(service.voice_name) > 0


# ───────────────────────── Tests de split de texto ─────────────────────────


class TestTTSServiceSplitText:
    """Tests del metodo _split_text (logica pura, sin mocks)."""

    def test_split_text_corto(self) -> None:
        """Texto corto no debe dividirse."""
        text = "El precio de la papa es 500 pesos."
        chunks = TTSService._split_text(text)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_split_text_vacio(self) -> None:
        """Texto vacio debe retornar lista vacia."""
        assert TTSService._split_text("") == []
        assert TTSService._split_text("   ") == []

    def test_split_text_por_oraciones(self) -> None:
        """Texto con varias oraciones debe dividirse por oraciones."""
        text = "El precio de la papa es 500 pesos. El clima en Traiguen esta soleado."
        chunks = TTSService._split_text(text)
        assert len(chunks) == 1  # Ambas oraciones entran en un chunk
        assert chunks[0] == text

    def test_split_text_largo_excede_limite(self) -> None:
        """Texto que excede _MAX_PIPER_CHARS debe dividirse."""
        sentence = "El precio de la papa en Santiago es de 500 pesos por kilo. "
        # Repetir la oracion hasta exceder el limite ampliamente
        repeat = (_MAX_PIPER_CHARS // len(sentence)) + 2
        text = sentence * repeat
        assert len(text) > _MAX_PIPER_CHARS

        chunks = TTSService._split_text(text)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= _MAX_PIPER_CHARS, f"Chunk excede limite: {len(chunk)} chars"
        # Cada chunk debe contener oraciones completas
        reconstructed = " ".join(chunks)
        assert sentence.strip() in reconstructed

    def test_split_text_oracion_muy_larga(self) -> None:
        """Oracion que excede el limite debe dividirse por comas."""
        # Crear una oracion que excede el limite con comas
        base = "El precio de la papa en la feria de Santiago centro es de 500 pesos, "
        # Repetir hasta exceder el limite
        while len(base) < _MAX_PIPER_CHARS + 50:
            base += "y tambien se vende en la feria de Temuco a 450 pesos, "

        chunks = TTSService._split_text(base)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= _MAX_PIPER_CHARS

    def test_split_text_exact_limit(self) -> None:
        """Limite exacto de _MAX_PIPER_CHARS no debe dividir."""
        text = "a" * _MAX_PIPER_CHARS
        chunks = TTSService._split_text(text)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_split_text_unicode(self) -> None:
        """Textos con acentos y enie deben manejarse correctamente."""
        text = (
            "El precio de la cebolla en la feria de Temuco es de 300 pesos. "
            "El clima en Traiguen es nublado con 15 grados."
        )
        chunks = TTSService._split_text(text)
        assert len(chunks) >= 1
        assert "cebolla" in chunks[0]
        assert "Traiguen" in chunks[-1]


# ───────────────────────── Tests de sintesis (con mocks) ─────────────────────────


class TestTTSServiceSynthesize:
    """Tests del metodo synthesize con Piper y ffmpeg mockeados."""

    def test_synthesize_exitoso(
        self,
        tmp_audio_dir: Path,
        mock_piper_voice: Mock,
        tmp_path: Path,
    ) -> None:
        """Debe sintetizar texto exitosamente y retornar path a .ogg."""
        model_path = _create_fake_model_file(tmp_path)

        with patch(
            "app.services.tts_service.TTSService._load_model",
            return_value=mock_piper_voice,
        ):
            service = TTSService(model_path=str(model_path))
            result = service.synthesize(
                "El precio de la papa es 500 pesos.",
                output_dir=tmp_audio_dir,
            )

        assert isinstance(result, str)
        assert result.endswith(".ogg")
        assert Path(result).exists()
        assert Path(result).stat().st_size > 0

    def test_synthesize_texto_vacio(
        self,
        tmp_audio_dir: Path,
    ) -> None:
        """Texto vacio debe lanzar ValueError."""
        service = TTSService()
        with pytest.raises(ValueError, match="vacio"):
            service.synthesize("  ", output_dir=tmp_audio_dir)

    def test_synthesize_modelo_no_encontrado(
        self,
        tmp_audio_dir: Path,
    ) -> None:
        """Si el modelo no existe, debe lanzar PiperModelNotFoundError."""
        service = TTSService(model_path="/no/existe.voice")
        with pytest.raises(PiperModelNotFoundError, match="no encontrado"):
            service.synthesize("Hola mundo", output_dir=tmp_audio_dir)

    def test_synthesize_carga_modelo_lazy(
        self,
        tmp_audio_dir: Path,
        mock_piper_voice: Mock,
        tmp_path: Path,
    ) -> None:
        """El modelo debe cargarse solo al sintetizar (lazy loading)."""
        model_path = _create_fake_model_file(tmp_path)

        service = TTSService(model_path=str(model_path))
        assert not service.is_loaded

        with patch(
            "app.services.tts_service.TTSService._load_model",
            return_value=mock_piper_voice,
        ) as mock_load:
            service.synthesize("Hola mundo", output_dir=tmp_audio_dir)
            mock_load.assert_called_once()

    def test_synthesize_texto_largo_multiple_chunks(
        self,
        tmp_audio_dir: Path,
        mock_piper_voice: Mock,
        tmp_path: Path,
    ) -> None:
        """Texto largo debe generar multiples fragmentos y concatenarse."""
        model_path = _create_fake_model_file(tmp_path)

        # Generar texto que requiere 2+ chunks
        chunk1 = "El precio de la papa en Santiago es de 500 pesos. "
        chunk2 = "En Temuco el precio es de 450 pesos. "
        text = chunk1 * 20 + chunk2 * 20  # Texto largo

        with patch(
            "app.services.tts_service.TTSService._load_model",
            return_value=mock_piper_voice,
        ):
            service = TTSService(model_path=str(model_path))
            result = service.synthesize(text, output_dir=tmp_audio_dir)

        assert Path(result).exists()
        assert result.endswith(".ogg")

    def test_synthesize_reusa_cache(
        self,
        tmp_audio_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Dos llamadas a synthesize deben reusar el modelo cargado.

        Mockea el modulo `piper` via sys.modules para que _load_model
        pueda ejecutar su logica real (import lazy + cache via self._model).
        PiperVoice.load debe llamarse UNA sola vez; la segunda synthesize()
        debe servirse del cache interno.
        """
        model_path = _create_fake_model_file(tmp_path)

        from unittest.mock import MagicMock

        mock_voice = MagicMock()
        mock_voice.synthesize.side_effect = _fake_synthesize_text

        # Mock del modulo piper para que el import lazy funcione
        mock_piper = MagicMock()
        mock_piper.PiperVoice = MagicMock()
        mock_piper.PiperVoice.load.return_value = mock_voice

        with patch.dict("sys.modules", {"piper": mock_piper}):
            service = TTSService(model_path=str(model_path))
            service.synthesize("Hola mundo", output_dir=tmp_audio_dir)
            service.synthesize("Otra prueba", output_dir=tmp_audio_dir)

            # PiperVoice.load debe llamarse solo 1 vez (cache en segunda llamada)
            assert mock_piper.PiperVoice.load.call_count == 1

    def test_synthesize_con_output_dir_personalizado_string(
        self,
        mock_piper_voice: Mock,
        tmp_path: Path,
    ) -> None:
        """output_dir puede ser un str en vez de Path."""
        model_path = _create_fake_model_file(tmp_path)
        custom_dir = tmp_path / "mi_directorio"
        custom_dir.mkdir()

        with patch(
            "app.services.tts_service.TTSService._load_model",
            return_value=mock_piper_voice,
        ):
            service = TTSService(model_path=str(model_path))
            result = service.synthesize("Hola mundo", output_dir=str(custom_dir))

        assert Path(result).parent == custom_dir


# ───────────────────────── Tests de ffmpeg ─────────────────────────


class TestTTSServiceConversion:
    """Tests de conversion y concatenacion con ffmpeg (mockeado)."""

    def test_convert_wav_to_ogg_llama_ffmpeg(
        self,
        tmp_path: Path,
    ) -> None:
        """_convert_wav_to_ogg debe llamar a ffmpeg con argumentos correctos."""
        input_wav = tmp_path / "input.wav"
        input_wav.write_bytes(b"fake wav content")
        output_ogg = tmp_path / "output.ogg"

        with patch("app.services.tts_service.subprocess.run") as mock_run:
            TTSService._convert_wav_to_ogg(input_wav, output_ogg)

            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
        assert "ffmpeg" in str(call_args[0])
        assert "-i" in call_args
        assert str(input_wav) in call_args
        assert "libopus" in call_args
        assert str(output_ogg) in call_args

    def test_convert_wav_to_ogg_ffmpeg_falla(
        self,
        tmp_path: Path,
    ) -> None:
        """Si ffmpeg falla, debe propagar CalledProcessError."""
        input_wav = tmp_path / "input.wav"
        input_wav.write_bytes(b"fake wav content")
        output_ogg = tmp_path / "output.ogg"

        with (
            patch(
                "app.services.tts_service.subprocess.run",
                side_effect=subprocess.CalledProcessError(
                    1, ["ffmpeg"], stderr=b"error simulado"
                ),
            ),
            pytest.raises(subprocess.CalledProcessError),
        ):
            TTSService._convert_wav_to_ogg(input_wav, output_ogg)

    def test_concatenate_wavs_llama_ffmpeg(
        self,
        tmp_path: Path,
    ) -> None:
        """_concatenate_wavs debe llamar a ffmpeg con argumentos correctos."""
        wav1 = tmp_path / "part1.wav"
        wav2 = tmp_path / "part2.wav"
        wav1.write_bytes(b"fake1")
        wav2.write_bytes(b"fake2")
        output = tmp_path / "combined.wav"

        with patch("app.services.tts_service.subprocess.run") as mock_run:
            TTSService._concatenate_wavs([wav1, wav2], output)

            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
        assert "ffmpeg" in str(call_args[0])
        assert "-f" in call_args
        assert "concat" in call_args

    def test_concatenate_wavs_limpia_lista_temporal(
        self,
        tmp_path: Path,
    ) -> None:
        """El archivo de lista temporal debe eliminarse despues de concatenar."""
        wav1 = tmp_path / "part1.wav"
        wav2 = tmp_path / "part2.wav"
        wav1.write_bytes(b"fake1")
        wav2.write_bytes(b"fake2")
        output = tmp_path / "combined.wav"

        TTSService._concatenate_wavs([wav1, wav2], output)

        # El archivo .txt temporal debe eliminarse
        list_file = output.with_suffix(".txt")
        assert not list_file.exists()


# ───────────────────────── Tests de regex de split ─────────────────────────


class TestSentenceSplitRegex:
    """Tests del regex _SENTENCE_SPLIT_RE usado para dividir oraciones."""

    def test_split_por_punto(self) -> None:
        """Debe dividir por punto seguido."""
        text = "Hola. Mundo."
        result = _SENTENCE_SPLIT_RE.split(text)
        assert result == ["Hola.", "Mundo."]

    def test_split_por_signos(self) -> None:
        """Debe dividir por signos de exclamacion e interrogacion."""
        text = "Hola! Como estas? Bien."
        result = _SENTENCE_SPLIT_RE.split(text)
        assert result == ["Hola!", "Como estas?", "Bien."]

    def test_no_split_sin_puntuacion(self) -> None:
        """Texto sin puntuacion no debe dividirse."""
        text = "Hola mundo esto es una prueba"
        result = _SENTENCE_SPLIT_RE.split(text)
        assert result == ["Hola mundo esto es una prueba"]


# ───────────────────────── Tests de cleanup de cache ─────────────────────────


class TestTTSServiceCleanup:
    """Tests de limpieza del modelo (compatibilidad con patron Whisper)."""

    def test_clear_model_cache_no_errors(self) -> None:
        """clear_model_cache no debe lanzar errores (marcador de tests)."""
        from app.services.tts_service import clear_model_cache

        clear_model_cache()  # No-op, solo verificar que no explota
