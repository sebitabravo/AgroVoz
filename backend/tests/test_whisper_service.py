"""Tests para el servicio de transcripcion Whisper (T2.1).

Usa mocks para evitar descargar el modelo (~500MB) en cada ejecucion de test.
Usa audio sintetico generado con el modulo `wave` de la stdlib.
"""

import logging
from collections.abc import Generator
from pathlib import Path
from types import ModuleType
from typing import cast
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.core.config import settings
from app.services.whisper_service import WhisperService, clear_model_cache


@pytest.fixture(autouse=True)
def _mock_whisper_import(
    mock_whisper: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Auto-mockea WhisperService._import_whisper para que CI corra sin openai-whisper.

    Crea un modulo whisper simulado con load_model() que retorna mock_whisper.
    Tests individuales pueden sobrescribir el return_value de load_model
    accediendo via WhisperService._import_whisper.return_value.load_model.

    Fija el backend en "openai" de forma explicita: sin esto la clase sigue el
    default de produccion y estos tests pasarian solo mientras faster-whisper
    no este instalado en el entorno.
    """
    monkeypatch.setattr(settings, "whisper_backend", "openai")
    mock_module = MagicMock()
    mock_module.load_model.return_value = mock_whisper
    with patch.object(WhisperService, "_import_whisper", return_value=mock_module):
        yield


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
def _clean_cache() -> Generator[None, None, None]:
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
    """Tests de transcripcion con Whisper.

    WhisperService._import_whisper ya esta mockeado por el fixture autouse _mock_whisper_import.
    Los tests solo ejercitan la logica de whisper_service (validaciones, cache,
    manejo de errores) sin necesitar openai-whisper instalado.
    """

    def test_transcribe_exitoso(
        self,
        wav_path: Path,
    ) -> None:
        """Debe transcribir audio exitosamente y retornar formato esperado."""
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

    def test_transcribe_carga_modelo_lazy(
        self,
        wav_path: Path,
    ) -> None:
        """El modelo debe cargarse solo al transcribir (lazy loading)."""
        service = WhisperService()
        assert not service.is_loaded

        service.transcribe(str(wav_path))
        WhisperService._import_whisper.assert_called()  # type: ignore[attr-defined]

    def test_transcribe_reusa_cache(
        self,
        wav_path: Path,
    ) -> None:
        """Dos instancias con mismo modelo deben reusar la cache."""
        s1 = WhisperService()
        s2 = WhisperService()

        s1.transcribe(str(wav_path))
        s2.transcribe(str(wav_path))

        # WhisperService._import_whisper debe llamarse solo una vez (segunda usa cache)
        assert WhisperService._import_whisper.call_count == 1  # type: ignore[attr-defined]

    def test_transcribe_archivo_no_existe(self) -> None:
        """Debe lanzar FileNotFoundError si el archivo no existe."""
        service = WhisperService()
        with pytest.raises(FileNotFoundError, match="no encontrado"):
            service.transcribe("/no/existe.wav")

    def test_transcribe_archivo_vacio(
        self,
        empty_wav_path: Path,
    ) -> None:
        """Debe lanzar ValueError si el archivo esta vacio."""
        service = WhisperService()
        with pytest.raises(ValueError, match="vacío"):
            service.transcribe(str(empty_wav_path))

    def test_transcribe_error_whisper(
        self,
        wav_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Debe lanzar RuntimeError si Whisper falla."""
        caplog.set_level(logging.ERROR, logger="app.services.whisper_service")
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError(f"secreto-whisper consulta privada ruta={wav_path}")
        # Sobrescribir el return_value de load_model dentro del mock
        import_mock = cast(Mock, WhisperService._import_whisper)
        import_mock.return_value.load_model.return_value = mock_model

        service = WhisperService()
        with pytest.raises(RuntimeError, match="Error de transcripción"):
            service.transcribe(str(wav_path))
        assert "secreto-whisper" not in caplog.text
        assert "consulta privada" not in caplog.text
        assert str(wav_path) not in caplog.text

    def test_transcribe_texto_vacio(
        self,
        wav_path: Path,
    ) -> None:
        """Debe retornar texto vacio si Whisper devuelve texto vacio."""
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {
            "text": "  ",
            "language": "es",
            "segments": [],
        }
        import_mock = cast(Mock, WhisperService._import_whisper)
        import_mock.return_value.load_model.return_value = mock_model

        service = WhisperService()
        result = service.transcribe(str(wav_path))
        assert result["text"] == ""


class TestWhisperServiceCache:
    """Tests del mecanismo de cache del modelo."""

    def test_clear_model_cache_limpia(self) -> None:
        """clear_model_cache debe limpiar la cache."""
        clear_model_cache()

    def test_cache_independiente_por_modelo(
        self,
        wav_path: Path,
    ) -> None:
        """Modelos distintos deben tener entradas separadas en cache."""
        s_small = WhisperService(model_name="small")
        s_tiny = WhisperService(model_name="tiny")

        s_small.transcribe(str(wav_path))
        s_tiny.transcribe(str(wav_path))

        # Dos modelos distintos = dos llamadas a WhisperService._import_whisper
        assert WhisperService._import_whisper.call_count == 2  # type: ignore[attr-defined]


class TestDeviceDetection:
    """Tests de deteccion de dispositivo (simulados via _get_device).

    Los tests no importan torch directamente — parchean _get_device en
    el modulo whisper_service para que CI corra sin openai-whisper/torch.
    """

    @patch("app.services.whisper_service._get_device", return_value="cuda")
    def test_device_cuda(self, mock_get_device: Mock) -> None:
        """Debe usar CUDA si _get_device lo retorna."""
        service = WhisperService()
        assert service._device == "cuda"
        mock_get_device.assert_called_once()

    @patch("app.services.whisper_service._get_device", return_value="mps")
    def test_device_mps(self, mock_get_device: Mock) -> None:
        """Debe usar MPS si _get_device lo retorna."""
        service = WhisperService()
        assert service._device == "mps"
        mock_get_device.assert_called_once()

    @patch("app.services.whisper_service._get_device", return_value="cpu")
    def test_device_cpu_fallback(self, mock_get_device: Mock) -> None:
        """Debe usar CPU si torch no esta instalado (caso CI)."""
        service = WhisperService()
        assert service._device == "cpu"
        mock_get_device.assert_called_once()


class TestWhisperServiceLoadModel:
    """Tests especificos de _load_model (cache + lock)."""

    def test_load_model_descarga_si_no_existe(self) -> None:
        """_load_model debe importar whisper y cargar el modelo."""
        service = WhisperService()
        model = service._load_model()

        assert model is not None
        WhisperService._import_whisper.assert_called_once()  # type: ignore[attr-defined]

    def test_load_model_reusa_cache(self) -> None:
        """_load_model debe reusar el modelo en cache."""
        s1 = WhisperService()
        s2 = WhisperService()

        s1._load_model()
        s2._load_model()

        # Mismo modelo = misma instancia (cache): _import_whisper solo una vez
        assert WhisperService._import_whisper.call_count == 1  # type: ignore[attr-defined]

    def test_load_model_distintos_modelos_no_comparten_cache(self) -> None:
        """Distintos nombres de modelo deben tener entradas separadas."""
        import_mock = cast(Mock, WhisperService._import_whisper)
        import_mock.return_value.load_model.side_effect = [
            MagicMock(),
            MagicMock(),
        ]

        s_small = WhisperService(model_name="small")
        s_tiny = WhisperService(model_name="tiny")

        m1 = s_small._load_model()
        m2 = s_tiny._load_model()

        assert m1 is not m2
        import_mock = cast(Mock, WhisperService._import_whisper)
        assert import_mock.call_count == 2
        assert import_mock.return_value.load_model.call_count == 2


class _FakeSegment:
    """Segmento con la forma que devuelve faster-whisper."""

    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


class _FakeInfo:
    """Metadatos de transcripcion de faster-whisper."""

    def __init__(self, language: str = "es") -> None:
        self.language = language


@pytest.fixture
def faster_backend(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Activa el backend faster-whisper con un modulo simulado."""
    clear_model_cache()
    monkeypatch.setattr(settings, "whisper_backend", "faster")
    monkeypatch.setattr(settings, "whisper_compute_type", "int8")

    model = MagicMock()
    model.transcribe.return_value = (
        iter(
            [
                _FakeSegment(0.0, 1.2, "a cuánto está la papa"),
                _FakeSegment(1.2, 2.0, " en Traiguén"),
            ]
        ),
        _FakeInfo("es"),
    )
    module = MagicMock()
    module.WhisperModel.return_value = model
    monkeypatch.setattr(
        WhisperService,
        "_import_faster_whisper",
        staticmethod(lambda: module),
    )
    return module


class TestFasterWhisperBackend:
    """Camino faster-whisper INT8: es el que corre en produccion."""

    def test_backend_activo_es_faster(self, faster_backend: Mock) -> None:
        """El default de produccion no debe caer silenciosamente a openai."""
        assert WhisperService().backend == "faster"

    def test_carga_el_modelo_cuantizado_sin_sobresuscribir_cpu(
        self,
        faster_backend: Mock,
    ) -> None:
        """INT8 y un tope de hilos son lo que hace rendir el piso de 1 vCPU."""
        WhisperService(model_name="small")._load_model()

        kwargs = faster_backend.WhisperModel.call_args.kwargs
        assert faster_backend.WhisperModel.call_args.args[0] == "small"
        assert kwargs["compute_type"] == "int8"
        assert 1 <= kwargs["cpu_threads"] <= 4

    def test_transcribe_normaliza_la_salida(
        self,
        faster_backend: Mock,
        tmp_path: Path,
    ) -> None:
        """El resto del pipeline no debe notar el cambio de backend."""
        audio = _generate_synthetic_wav(tmp_path / "consulta.wav")

        result = WhisperService().transcribe(audio)

        assert result["text"] == "a cuánto está la papa en Traiguén"
        assert result["language"] == "es"
        assert len(cast(list, result["segments"])) == 2
        assert isinstance(result["duration_ms"], int)

    def test_conserva_prompt_de_dominio_y_fallback_de_temperatura(
        self,
        faster_backend: Mock,
        tmp_path: Path,
    ) -> None:
        """Sin el prompt de dominio Whisper vuelve a inventar palabras (#136)."""
        audio = _generate_synthetic_wav(tmp_path / "consulta.wav")

        WhisperService().transcribe(audio)

        kwargs = faster_backend.WhisperModel.return_value.transcribe.call_args.kwargs
        assert "papa" in kwargs["initial_prompt"]
        assert "Traiguén" in kwargs["initial_prompt"]
        assert kwargs["language"] == "es"
        assert len(kwargs["temperature"]) > 1

    def test_cache_separa_backends_del_mismo_modelo(
        self,
        faster_backend: Mock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """faster y openai cargan objetos incompatibles del mismo 'small'."""
        faster_service = WhisperService(model_name="small")
        faster_service._load_model()
        assert faster_service.is_loaded

        monkeypatch.setattr(settings, "whisper_backend", "openai")
        openai_service = WhisperService(model_name="small")

        assert openai_service.backend == "openai"
        assert not openai_service.is_loaded

    def test_sin_libreria_instalada_degrada_a_openai(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Un despliegue sin faster-whisper no puede quedarse mudo."""
        monkeypatch.setattr(settings, "whisper_backend", "faster")

        def _missing() -> ModuleType:
            raise ImportError("No module named 'faster_whisper'")

        monkeypatch.setattr(
            WhisperService,
            "_import_faster_whisper",
            staticmethod(_missing),
        )

        assert WhisperService().backend == "openai"
