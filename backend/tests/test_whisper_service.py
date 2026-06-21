"""Tests para el servicio de transcripcion Whisper (T2.1).

Usa mocks para evitar descargar el modelo (~500MB) en cada ejecucion de test.
Usa audio sintetico generado con el modulo `wave` de la stdlib.
"""

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.services.whisper_service import WhisperService, clear_model_cache


def _generate_synthetic_wav(path: Path, duration_sec: float = 1.0) -> Path:
    """Genera un archivo WAV sintetico (silencio) para testing.

    Usa el modulo `wave` de la stdlib para generar un WAV valido
    sin depender de ffmpeg ni archivos externos.
    """
    import struct
    import wave

    sample_rate = 16000
    num_samples = int(sample_rate * duration_sec)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        for _ in range(num_samples):
            wf.writeframes(struct.pack("<h", 0))  # Silencio

    return path


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    """Limpia la cache de modelos antes y despues de cada test."""
    clear_model_cache()
    yield
    clear_model_cache()


@pytest.fixture
def wav_path(tmp_path: Path) -> Path:
    """Genera un archivo WAV sintetico valido."""
    return _generate_synthetic_wav(tmp_path / "test.wav", duration_sec=1.0)


@pytest.fixture
def empty_wav_path(tmp_path: Path) -> Path:
    """Genera un archivo WAV vacio (0 bytes)."""
    path = tmp_path / "empty.wav"
    path.touch()
    return path


@pytest.fixture
def mock_whisper() -> Mock:
    """Crea un mock de whisper.load_model con resultado simulado.

    El mock retorna un objeto con metodo transcribe() que devuelve
    un texto simulado en espanol.
    """
    mock_model = MagicMock()
    mock_model.transcribe.return_value = {
        "text": "Hola mundo, esta es una prueba de transcripcion",
        "language": "es",
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 2.0,
                "text": "Hola mundo, esta es una prueba de transcripcion",
            }
        ],
    }
    return mock_model


class TestWhisperServiceInit:
    """Tests de inicializacion del servicio."""

    def test_init_con_modelo_default(self) -> None:
        """Debe usar 'small' si no se especifica modelo."""
        service = WhisperService()
        assert service.model_name == "small"

    def test_init_con_modelo_personalizado(self) -> None:
        """Debe usar el modelo especificado."""
        service = WhisperService(model_name="tiny")
        assert service.model_name == "tiny"

    def test_init_no_carga_modelo(self) -> None:
        """El modelo NO debe cargarse en __init__ (lazy loading)."""
        service = WhisperService()
        assert not service.is_loaded

    def test_init_idioma_espanol(self) -> None:
        """El idioma debe ser 'es' por defecto."""
        service = WhisperService()
        assert service._language == "es"


class TestWhisperServiceTranscribe:
    """Tests de transcripcion con Whisper."""

    @patch("whisper.load_model")
    def test_transcribe_exitoso(
        self,
        mock_load: Mock,
        wav_path: Path,
        mock_whisper: Mock,
    ) -> None:
        """Debe transcribir audio exitosamente y retornar formato esperado."""
        mock_load.return_value = mock_whisper

        service = WhisperService()
        result = service.transcribe(str(wav_path))

        assert isinstance(result, dict)
        assert "text" in result
        assert "language" in result
        assert "segments" in result
        assert "duration_ms" in result

        assert result["text"] == "Hola mundo, esta es una prueba de transcripcion"
        assert result["language"] == "es"
        assert isinstance(result["duration_ms"], int)

    @patch("whisper.load_model")
    def test_transcribe_carga_modelo_lazy(
        self,
        mock_load: Mock,
        wav_path: Path,
        mock_whisper: Mock,
    ) -> None:
        """El modelo debe cargarse solo al transcribir (lazy loading)."""
        mock_load.return_value = mock_whisper

        service = WhisperService()
        assert not service.is_loaded

        service.transcribe(str(wav_path))
        assert service.is_loaded
        mock_load.assert_called_once()

    @patch("whisper.load_model")
    def test_transcribe_reusa_cache(
        self,
        mock_load: Mock,
        wav_path: Path,
        mock_whisper: Mock,
    ) -> None:
        """Dos instancias con mismo modelo deben reusar la cache."""
        mock_load.return_value = mock_whisper

        s1 = WhisperService()
        s2 = WhisperService()

        s1.transcribe(str(wav_path))
        s2.transcribe(str(wav_path))

        # load_model solo debe llamarse una vez
        mock_load.assert_called_once()

    @patch("whisper.load_model")
    def test_transcribe_archivo_no_existe(
        self,
        mock_load: Mock,
        mock_whisper: Mock,
    ) -> None:
        """Debe lanzar FileNotFoundError si el archivo no existe."""
        mock_load.return_value = mock_whisper

        service = WhisperService()
        with pytest.raises(FileNotFoundError, match="no encontrado"):
            service.transcribe("/no/existe.wav")

    @patch("whisper.load_model")
    def test_transcribe_archivo_vacio(
        self,
        mock_load: Mock,
        empty_wav_path: Path,
        mock_whisper: Mock,
    ) -> None:
        """Debe lanzar ValueError si el archivo esta vacio."""
        mock_load.return_value = mock_whisper

        service = WhisperService()
        with pytest.raises(ValueError, match="vacio"):
            service.transcribe(str(empty_wav_path))

    @patch("whisper.load_model")
    def test_transcribe_error_whisper(
        self,
        mock_load: Mock,
        wav_path: Path,
    ) -> None:
        """Debe lanzar RuntimeError si Whisper falla."""
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError("Whisper crash")
        mock_load.return_value = mock_model

        service = WhisperService()
        with pytest.raises(RuntimeError, match="Error de transcripcion"):
            service.transcribe(str(wav_path))

    @patch("whisper.load_model")
    def test_transcribe_texto_vacio(
        self,
        mock_load: Mock,
        wav_path: Path,
    ) -> None:
        """Debe retornar texto vacio si Whisper devuelve texto vacio."""
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {
            "text": "  ",
            "language": "es",
            "segments": [],
        }
        mock_load.return_value = mock_model

        service = WhisperService()
        result = service.transcribe(str(wav_path))
        assert result["text"] == ""


class TestWhisperServiceCache:
    """Tests del mecanismo de cache del modelo."""

    def test_clear_model_cache_limpia(self) -> None:
        """clear_model_cache debe limpiar la cache."""
        clear_model_cache()

    @patch("whisper.load_model")
    def test_cache_independiente_por_modelo(
        self,
        mock_load: Mock,
        wav_path: Path,
        mock_whisper: Mock,
    ) -> None:
        """Modelos distintos deben tener entradas separadas en cache."""
        mock_load.return_value = mock_whisper

        s_small = WhisperService(model_name="small")
        s_tiny = WhisperService(model_name="tiny")

        s_small.transcribe(str(wav_path))
        s_tiny.transcribe(str(wav_path))

        assert mock_load.call_count == 2


class TestDeviceDetection:
    """Tests de deteccion de dispositivo (simulados)."""

    @patch("torch.cuda.is_available")
    def test_device_cuda(self, mock_cuda: Mock) -> None:
        """Debe detectar CUDA si esta disponible."""
        from app.services.whisper_service import _get_device

        mock_cuda.return_value = True
        assert _get_device() == "cuda"

    @patch("torch.cuda.is_available")
    @patch("torch.backends.mps.is_available")
    def test_device_mps(self, mock_mps: Mock, mock_cuda: Mock) -> None:
        """Debe detectar MPS si CUDA no esta disponible."""
        from app.services.whisper_service import _get_device

        mock_cuda.return_value = False
        mock_mps.return_value = True
        assert _get_device() == "mps"

    @patch("torch.cuda.is_available")
    @patch("torch.backends.mps.is_available")
    def test_device_cpu_fallback(
        self, mock_mps: Mock, mock_cuda: Mock
    ) -> None:
        """Debe usar CPU si no hay CUDA ni MPS."""
        # Necesario incluso en equipos sin MPS: hasattr(torch.backends, "mps")
        # es True en macOS con torch instalado.
        import torch

        from app.services.whisper_service import _get_device

        mock_cuda.return_value = False
        mock_mps.return_value = False
        if not hasattr(torch.backends, "mps"):
            pytest.skip("Este equipo no tiene torch.backends.mps")
        assert _get_device() == "cpu"


class TestWhisperServiceLoadModel:
    """Tests especificos de _load_model."""

    @patch("whisper.load_model")
    def test_load_model_descarga_si_no_existe(
        self,
        mock_load: Mock,
        mock_whisper: Mock,
    ) -> None:
        """_load_model debe descargar el modelo si no esta en cache."""
        mock_load.return_value = mock_whisper

        service = WhisperService()
        model = service._load_model()

        assert model is mock_whisper
        mock_load.assert_called_once()

    @patch("whisper.load_model")
    def test_load_model_reusa_cache(
        self,
        mock_load: Mock,
        mock_whisper: Mock,
    ) -> None:
        """_load_model debe reusar el modelo en cache."""
        mock_load.return_value = mock_whisper

        s1 = WhisperService()
        s2 = WhisperService()

        assert s1._load_model() is mock_whisper
        assert s2._load_model() is mock_whisper
        mock_load.assert_called_once()

    @patch("whisper.load_model")
    def test_load_model_distintos_modelos_no_comparten_cache(
        self,
        mock_load: Mock,
    ) -> None:
        """Distintos nombres de modelo deben tener entradas separadas."""
        mock_model_small = MagicMock()
        mock_model_tiny = MagicMock()
        mock_load.side_effect = [mock_model_small, mock_model_tiny]

        s_small = WhisperService(model_name="small")
        s_tiny = WhisperService(model_name="tiny")

        m1 = s_small._load_model()
        m2 = s_tiny._load_model()

        assert m1 is mock_model_small
        assert m2 is mock_model_tiny
        assert m1 is not m2
        assert mock_load.call_count == 2
