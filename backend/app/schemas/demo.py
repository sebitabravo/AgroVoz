"""Schemas Pydantic para el endpoint demo de la landing page.

Issue #119 — endpoint de chat web interactivo para el pitch de Crea INACAP.
"""

from typing import Literal

from pydantic import BaseModel, Field


class DemoHistorialMensaje(BaseModel):
    """Turno textual efímero de la conversación mostrada en la demo."""

    rol: Literal["user", "assistant"]
    texto: str = Field(min_length=1, max_length=500)


class DemoPreguntaRequest(BaseModel):
    """Request POST /api/v1/demo/preguntar.

    Acepta texto plano o audio base64 (OGG/Opus). El texto tiene prioridad
    cuando ambos están presentes; el audio se transcribe solo si el texto
    está vacío.
    """

    texto: str = Field(
        default="",
        max_length=500,
        description="Texto de la consulta del usuario",
    )
    audio_base64: str | None = Field(
        default=None,
        max_length=7_000_000,
        description="Audio en base64 (OGG/Opus) opcional",
    )
    historial: list[DemoHistorialMensaje] = Field(
        default_factory=list,
        max_length=6,
        description="Hasta seis turnos textuales previos, no persistidos.",
    )


class DemoRespuestaResponse(BaseModel):
    """Response de POST /api/v1/demo/preguntar.

    Incluye el texto generado por el LLM y el audio sintetizado por Piper
    codificado en base64, listo para reproducir en el navegador.
    """

    texto: str = Field(description="Texto de respuesta generado por el LLM")
    audio_base64: str = Field(description="Audio OGG/Opus codificado en base64")
    intent: str = Field(description="Intencion detectada: precio, clima, saludo, desconocido")
    latency_ms: int = Field(description="Latencia total end-to-end en milisegundos")
