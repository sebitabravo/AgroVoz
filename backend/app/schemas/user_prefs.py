"""Schemas Pydantic para preferencias de usuario (onboarding, #86).

DTOs para los endpoints admin de user_prefs (set/get comuna).
El phone_hash llega como path param (validado), la comuna en el body.
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field


class ComunaRequest(BaseModel):
    """Body del PUT /admin/users/{phone_hash}/comuna (#86, #96).

    Permite registrar o actualizar la comuna del productor y,
    opcionalmente, el consentimiento para retención de audio en el
    dataset de voz rural chilena.
    """

    comuna: str = Field(
        min_length=1,
        max_length=100,
        description="Nombre de la comuna (ej: Traiguén).",
    )
    dataset_consent: bool | None = Field(
        default=None,
        description="Opt-in explicito para retener audio en el dataset de voz rural (#96). "
                    "None = no modificar el valor actual.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "comuna": "Traiguén",
                    "dataset_consent": True,
                }
            ]
        },
    }


class UserPrefsResponse(BaseModel):
    """Respuesta de GET /admin/users/{phone_hash}.

    Solo expone phone_hash (HMAC-SHA256), nunca el número en claro.
    """

    phone_hash: str = Field(description="Hash HMAC-SHA256 del teléfono (64 chars hex)")
    comuna: str | None = Field(default=None, description="Comuna registrada o None")
    dataset_consent: bool = Field(description="Consentimiento para retener audio en dataset (#96)")
    created_at: datetime.datetime = Field(description="Timestamp de creación (ISO 8601)")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "phone_hash": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
                    "comuna": "Traiguén",
                    "dataset_consent": True,
                    "created_at": "2026-06-15T10:30:00",
                }
            ]
        },
    }
