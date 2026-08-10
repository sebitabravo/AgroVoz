"""Verifica que CI instale el motor Whisper efectivo de produccion."""


def test_faster_whisper_engine_is_installed() -> None:
    """El backend faster debe estar importable sin descargar el modelo."""
    from faster_whisper import WhisperModel

    assert callable(WhisperModel)
