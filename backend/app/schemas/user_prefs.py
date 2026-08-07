"""Schemas Pydantic para preferencias de usuario (onboarding, #86).

DTOs para los endpoints admin de user_prefs (set/get comuna).
El phone_hash llega como path param (validado), la comuna en el body.
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Largo máximo por cultivo. Los nombres reales son cortos ("papa", "trigo");
# este tope descarta payloads abusivos en la columna TEXT de SQLite (#125).
_MAX_CULTIVO_LEN = 100
_GROUP_LABEL_PATTERN = re.compile(r"^prodesal-[a-z0-9]+(?:-[a-z0-9]+)*$")
_MIN_PHONE_DIGITS = 8

IdentityType = Literal["individual", "prodesal_group"]


class ComunaRequest(BaseModel):
    """Body del PUT /admin/users/{phone_hash}/comuna (#86, #96, #125).

    Permite registrar o actualizar la comuna del productor, consentimientos
    independientes y los cultivos de interés del productor.
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
    alert_consent: bool | None = Field(
        default=None,
        description=(
            "Opt-in explícito para recibir alertas proactivas de precio y clima. None = no modificar el valor actual."
        ),
    )
    history_consent: bool | None = Field(
        default=None,
        description=(
            "Opt-in específico para retener historial de consultas (#195, #201). None = no modificar el valor actual."
        ),
    )
    expense_consent: bool | None = Field(
        default=None,
        description=(
            "Opt-in específico para retener gastos declarados (#170). Revocarlo con false borra los gastos "
            "existentes. None = no modificar el valor actual."
        ),
    )
    parcela_consent: bool | None = Field(
        default=None,
        description=(
            "Opt-in específico para registrar parcelas (cultivo, superficie, comuna). Revocarlo con false "
            "borra las parcelas existentes. None = no modificar el valor actual."
        ),
    )
    location_consent: bool | None = Field(
        default=None,
        description=(
            "Opt-in específico para guardar ubicación GPS. Revocarlo limpia el pin existente. "
            "None = no modificar el valor actual."
        ),
    )
    cultivos: list[str] | None = Field(
        default=None,
        max_length=20,
        description="Cultivos de interés del agricultor, ej: ['papa', 'trigo', 'tomate'] (#125). "
        "None = no modificar el valor actual. Máximo 20 cultivos.",
    )
    identity_type: IdentityType | None = Field(
        default=None,
        description=("Tipo de identidad. Omitir conserva el valor actual; al crear, la omisión usa 'individual'."),
    )
    group_label: str | None = Field(
        default=None,
        min_length=3,
        max_length=100,
        description=("Código operativo no sensible con prefijo 'prodesal-'. Omitir conserva el valor actual."),
    )
    localidad: str | None = Field(
        default=None,
        description=("Localidad o sector general. Omitir conserva; null limpia el valor."),
    )

    @field_validator("cultivos")
    @classmethod
    def _validar_cultivos(cls, v: list[str] | None) -> list[str] | None:
        """Normaliza cultivos: limpia, minúscula, descarta vacíos/sobredimensionados y deduplica."""
        if v is None:
            return None
        # Limpiar espacios, normalizar a minúscula, descartar vacíos y payloads abusivos.
        limpios = [c.strip().lower() for c in v if c.strip() and len(c.strip()) <= _MAX_CULTIVO_LEN]
        if not limpios:
            return None
        # Deduplicar preservando el orden de aparición y limitar a 20.
        return list(dict.fromkeys(limpios))[:20]

    @field_validator("group_label")
    @classmethod
    def _validar_group_label(cls, value: str | None) -> str | None:
        """Acepta solo códigos slug no sensibles y descarta teléfonos."""
        if value is None:
            return None
        if _GROUP_LABEL_PATTERN.fullmatch(value) is None:
            raise ValueError("group_label debe ser un slug lowercase con prefijo 'prodesal-'")
        digits = "".join(character for character in value if character.isdigit())
        if len(digits) >= _MIN_PHONE_DIGITS:
            raise ValueError("group_label no puede contener un teléfono")
        return value

    @field_validator("localidad")
    @classmethod
    def _normalizar_localidad(cls, value: str | None) -> str | None:
        """Limpia la localidad y limita su tamaño después del trim."""
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("localidad no puede estar vacía")
        if len(cleaned) > 120:
            raise ValueError("localidad no puede exceder 120 caracteres")
        return cleaned

    @model_validator(mode="after")
    def _validar_cambio_de_identidad(self) -> ComunaRequest:
        """Exige una etiqueta explícita al seleccionar identidad grupal."""
        provided_fields = self.model_fields_set
        if "identity_type" not in provided_fields:
            return self
        if self.identity_type is None:
            raise ValueError("identity_type explícito no puede ser null")
        if self.identity_type == "prodesal_group":
            if "group_label" not in provided_fields or self.group_label is None:
                raise ValueError("prodesal_group exige group_label explícito")
        elif "group_label" in provided_fields and self.group_label is not None:
            raise ValueError("individual no admite group_label")
        return self

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "comuna": "Traiguén",
                    "dataset_consent": True,
                    "alert_consent": True,
                    "history_consent": True,
                    "expense_consent": False,
                    "parcela_consent": False,
                    "location_consent": False,
                    "cultivos": ["papa", "trigo", "tomate"],
                    "identity_type": "prodesal_group",
                    "group_label": "prodesal-traiguen-norte",
                    "localidad": "Quilquén",
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
    alert_consent: bool = Field(
        default=False,
        description="Consentimiento específico para recibir alertas proactivas de precio y clima.",
    )
    history_consent: bool = Field(
        default=False, description="Consentimiento específico para retener historial (#195, #201)"
    )
    expense_consent: bool = Field(
        default=False, description="Consentimiento específico para retener gastos declarados (#170)"
    )
    parcela_consent: bool = Field(default=False, description="Consentimiento específico para registrar parcelas (C5)")
    location_consent: bool = Field(default=False, description="Consentimiento específico para guardar ubicación GPS")
    identity_type: IdentityType = Field(
        default="individual",
        description="Identidad individual o contacto compartido PRODESAL",
    )
    group_label: str | None = Field(
        default=None,
        description="Código operativo del grupo PRODESAL o None",
    )
    localidad: str | None = Field(
        default=None,
        description="Localidad o sector general, o None",
    )
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
                    "alert_consent": True,
                    "history_consent": True,
                    "expense_consent": False,
                    "parcela_consent": False,
                    "location_consent": False,
                    "identity_type": "prodesal_group",
                    "group_label": "prodesal-traiguen-norte",
                    "localidad": "Quilquén",
                    "cultivos": ["papa", "trigo", "tomate"],
                    "created_at": "2026-06-15T10:30:00",
                }
            ]
        },
    }
