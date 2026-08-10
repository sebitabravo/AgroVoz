"""Precarga el backend de Whisper seleccionado por configuración.

El entrypoint no debe conocer detalles de openai-whisper ni faster-whisper:
``WhisperService`` resuelve ``WHISPER_BACKEND`` y mantiene ambos caminos
compatibles. Este script solo activa la carga lazy para que producción no pague
el costo en la primera consulta.
"""

from app.services.whisper_service import WhisperService


def preload() -> None:
    """Carga el modelo usando el backend efectivo de Whisper."""
    WhisperService().preload()


if __name__ == "__main__":
    preload()
