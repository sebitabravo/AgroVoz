"""Precarga o verifica el backend de Whisper seleccionado por configuración.

El entrypoint no debe conocer detalles de openai-whisper ni faster-whisper:
``WhisperService`` resuelve ``WHISPER_BACKEND`` y mantiene ambos caminos
compatibles. Este script solo activa la carga lazy para que producción no pague
el costo en la primera consulta.
"""

import argparse
from pathlib import Path

from app.core.config import settings
from app.services.whisper_service import WhisperService

_FASTER_WHISPER_FILES = (
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
)


def preload() -> None:
    """Carga el modelo usando el backend efectivo de Whisper."""
    WhisperService().preload()


def is_cached() -> bool:
    """Verifica que el backend efectivo pueda cargar solo desde el cache local."""
    service = WhisperService()

    if service.backend == "openai":
        cache_dir = Path(settings.whisper_model_path or Path.home() / ".cache" / "whisper")
        model_file = cache_dir / f"{service.model_name}.pt"
        return model_file.is_file() and model_file.stat().st_size > 0

    # faster-whisper delega el cache a Hugging Face. local_files_only evita
    # cualquier descarga y devuelve el mismo snapshot que usará en runtime.
    from faster_whisper.utils import download_model

    try:
        model_dir = Path(
            download_model(
                service.model_name,
                cache_dir=settings.whisper_model_path or None,
                local_files_only=True,
            )
        )
    except (FileNotFoundError, OSError, ValueError):
        return False

    return all((model_dir / filename).is_file() for filename in _FASTER_WHISPER_FILES)


def main() -> None:
    """Ejecuta la precarga o la comprobación offline del cache."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-cache",
        action="store_true",
        help="sale con 0 solo si el modelo ya puede cargarse sin red",
    )
    arguments = parser.parse_args()

    if arguments.check_cache:
        raise SystemExit(0 if is_cached() else 1)

    preload()


if __name__ == "__main__":
    main()
