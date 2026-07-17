"""Schemas Pydantic para preferencias de usuario (onboarding, #86).

DTOs para los endpoints admin de user_prefs (set/get comuna).
El phone_hash llega como path param (validado), la comuna en el body.
"""

from __future__ import annotations

import datetime
import json

from pydantic import BaseModel, Field, field_validator

# Largo máximo por cultivo. Los nombres reales son cortos ("papa", "trigo");
# este tope descarta payloads abusivos en la columna TEXT de SQLite (#125).
_MAX_CULTIVO_LEN = 100


class ComunaRequest(BaseModel):
    """Body del PUT /admin/users/{phone_hash}/comuna (#86, #96, #125).

    Permite registrar o actualizar la comuna del productor,
    el consentimiento para retención de audio en el dataset de voz
    rural chilena y los cultivos de interés del productor.
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
    cultivos: list[str] | None = Field(
        default=None,
        max_length=20,
        description="Cultivos de interés del agricultor, ej: ['papa', 'trigo', 'tomate'] (#125). "
                    "None = no modificar el valor actual. Máximo 20 cultivos.",
    )

    @field_validator("cultivos")
    @classmethod
    def _validar_cultivos(cls, v: list[str] | None) -> list[str] | None:
        """Normaliza cultivos: limpia, minúscula, descarta vacíos/sobredimensionados y deduplica."""
        if v is None:
            return None
        # Limpiar espacios, normalizar a minúscula, descartar vacíos y payloads abusivos.
        limpios = [
            c.strip().lower()
            for c in v
            if c.strip() and len(c.strip()) <= _MAX_CULTIVO_LEN
        ]
        if not limpios:
            return None
        # Deduplicar preservando el orden de aparición y limitar a 20.
        return list(dict.fromkeys(limpios))[:20]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "comuna": "Traiguén",
                    "dataset_consent": True,
                    "cultivos": ["papa", "trigo", "tomate"],
                }
            ]
        },
    }


class UserPrefsResponse(BaseModel):
    """Respuesta de GET /admin/users/{phone_hash}.

    Solo expone phone_hash (HMAC-SHA256), nunca el número en claro.
    Incluye cultivos de interés del productor (issue #125).
    """

    phone_hash: str = Field(description="Hash HMAC-SHA256 del teléfono (64 chars hex)")
    comuna: str | None = Field(default=None, description="Comuna registrada o None")
    dataset_consent: bool = Field(description="Consentimiento para retener audio en dataset (#96)")
    cultivos: list[str] | None = Field(
        default=None,
        description="Cultivos de interés del agricultor o None (#125)",
    )
    created_at: datetime.datetime = Field(description="Timestamp de creación (ISO 8601)")

    @field_validator("cultivos", mode="before")
    @classmethod
    def _deserializar_cultivos(cls, v: str | list[str] | None) -> list[str] | None:
        """Deserializa cultivos desde JSON string (SQLite TEXT) a lista."""
        if v is None:
            return None
        if isinstance(v, list):
            return v
        try:
            parsed = json.loads(v)
            if isinstance(parsed, list):
                return parsed
            return None
        except (json.JSONDecodeError, TypeError):
            return None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "phone_hash": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
                    "comuna": "Traiguén",
                    "dataset_consent": True,
                    "cultivos": ["papa", "trigo", "tomate"],
                    "created_at": "2026-06-15T10:30:00",
                }
            ]
        },
    }
