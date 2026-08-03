"""Schemas de entrada/salida para identificación visual desde la PWA."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class VisionAlternativeResponse(BaseModel):
    """Una alternativa del clasificador local."""

    label: str
    confidence: float = Field(ge=0.0, le=1.0)


class VisionIdentifyResponse(BaseModel):
    """Resultado seguro, con confianza y cita INIA opcional."""

    status: str
    classification: str | None
    detected_label: str
    confidence: float = Field(ge=0.0, le=1.0)
    alternatives: list[VisionAlternativeResponse]
    message: str
    rule: str | None
    source: str | None
    source_url: str | None
    verified_on: str | None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "identified",
                "classification": "Tizón tardío de la papa",
                "detected_label": "Potato___Late_blight",
                "confidence": 0.94,
                "alternatives": [],
                "message": "Identificación orientativa: Tizón tardío de la papa (94%).",
                "rule": "Regla INIA vigente...",
                "source": "INIA Chile — Enfermedades de la papa: Tizón tardío",
                "source_url": "https://enfermedadespapa.inia.cl/tizonTardio.php",
                "verified_on": "2026-07-30",
            }
        }
    )
