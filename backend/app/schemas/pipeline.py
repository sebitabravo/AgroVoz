"""Schemas Pydantic para el pipeline de procesamiento de audio.

Issue #18: AgroVozPipeline.process_audio() — audio → Whisper → LLM → TTS.
"""

from pydantic import BaseModel, Field


class AudioResponse(BaseModel):
    """Resultado del pipeline de voz completo.

    Retornado por AgroVozPipeline.process_audio() con métricas
    de latencia por etapa para benchmark y monitoreo.
    """

    audio_path: str = Field(description="Ruta al archivo .ogg generado por TTS")
    text_response: str = Field(description="Texto de respuesta generado por LLM, listo para TTS")
    latency_ms: int = Field(description="Latencia total end-to-end en milisegundos")
    intent: str = Field(description="Intención detectada. Valores definidos en app.core.constants.Intent.")

    # Métricas por etapa para benchmark
    whisper_ms: int = Field(default=0, description="Latencia de transcripción Whisper en ms")
    llm_ms: int = Field(default=0, description="Latencia de generación LLM (incluye Tool Calling) en ms")
    tts_ms: int = Field(default=0, description="Latencia de síntesis Piper TTS en ms")

    # Onboarding (#86): audio de bienvenida para primer contacto.
    # Si no es None, AudioService lo envía ANTES de la respuesta normal.
    # None = no es primer contacto (o la detección/bienvenida falló).
    welcome_audio_path: str | None = Field(
        default=None,
        description="Audio de bienvenida para primer contacto. None si no aplica.",
    )
