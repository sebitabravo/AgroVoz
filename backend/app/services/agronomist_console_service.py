"""Console web para agrónomos PRODESAL: link firmado, sin cuenta (C4).

Un agrónomo "sigue a varios productores" a través de un contacto
prodesal_group (#173): ese identity_type ya representa un número de WhatsApp
compartido por varios productores. El equipo (admin) genera el link para un
group_label específico y se lo entrega al agrónomo por fuera del sistema — un
agrónomo no es un contacto de WhatsApp propio en AgroVoz, así que no puede
pedirse el link por voz como el panel del agricultor (C3).

Misma filosofía que el panel: sin login, sin cuenta, un link firmado con HMAC
y de duración corta. Clave de firma dedicada, nunca compartida con la del
panel del agricultor.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.parcela import Parcela
from app.models.user_prefs import UserPrefs

_TOKEN_SEPARATOR = "."
_GROUP_LABEL_PATTERN = re.compile(r"^prodesal-[a-z0-9]+(?:-[a-z0-9]+)*$")


class AgronomistLinkError(RuntimeError):
    """El link no pudo generarse o el secreto no está configurado de forma segura."""


def validate_group_label(group_label: str) -> bool:
    """Valida el mismo formato de slug que exige ComunaRequest."""
    return bool(_GROUP_LABEL_PATTERN.fullmatch(group_label))


def _get_link_secret() -> str:
    """Obtiene la clave dedicada o rechaza una operación no firmable."""
    secret = settings.agronomist_link_secret.get_secret_value()
    if len(secret.strip()) < 32:
        raise AgronomistLinkError("agronomist_link_secret no está configurada de forma segura")
    return secret


def _sign(group_label: str, expires_at: int) -> str:
    """Firma group_label + expiración con la clave dedicada de agrónomos."""
    secret = _get_link_secret()
    message = f"{group_label}{_TOKEN_SEPARATOR}{expires_at}"
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_agronomist_token(group_label: str, *, now: int | None = None) -> str:
    """Genera un token firmado y de duración corta para la console.

    Args:
        group_label: Código de grupo PRODESAL (ej: "prodesal-traiguen-norte").
        now: Timestamp Unix inyectable para tests.

    Returns:
        Token ``{group_label}.{expires_at}.{firma}``.

    Raises:
        ValueError: Si group_label no tiene formato de slug válido.
        AgronomistLinkError: Si la clave de firma no está configurada de forma segura.
    """
    if not validate_group_label(group_label):
        raise ValueError("group_label inválido")

    effective_now = now if now is not None else int(time.time())
    expires_at = effective_now + settings.agronomist_link_ttl_hours * 3600
    signature = _sign(group_label, expires_at)
    return f"{group_label}{_TOKEN_SEPARATOR}{expires_at}{_TOKEN_SEPARATOR}{signature}"


def verify_agronomist_token(token: str, *, now: int | None = None) -> str | None:
    """Verifica un token de la console y retorna el group_label si es válido.

    Returns:
        El group_label si el token es válido y no ha vencido, o ``None`` si es
        inválido, está corrupto o venció. Nunca lanza sobre entrada no confiable.
    """
    parts = token.split(_TOKEN_SEPARATOR)
    if len(parts) != 3:
        return None
    group_label, expires_at_raw, signature = parts
    if not validate_group_label(group_label):
        return None
    try:
        expires_at = int(expires_at_raw)
    except ValueError:
        return None

    try:
        expected_signature = _sign(group_label, expires_at)
    except AgronomistLinkError:
        return None
    if not hmac.compare_digest(expected_signature, signature):
        return None

    effective_now = now if now is not None else int(time.time())
    if effective_now > expires_at:
        return None
    return group_label


@dataclass(frozen=True, slots=True)
class FarmerSummary:
    """Resumen de un contacto del grupo, sin exponer el phone_hash."""

    comuna: str | None
    localidad: str | None
    cultivos: list[str]
    parcelas: list[dict[str, Any]]


def get_group_summary(session: Session, group_label: str) -> list[FarmerSummary] | None:
    """Arma el resumen de los contactos del grupo PRODESAL.

    Cada fila respeta el consentimiento de parcelas del propio contacto,
    igual que el panel del agricultor. El phone_hash nunca se expone: el
    agrónomo ve datos operativos del grupo, no la identidad de WhatsApp.

    Returns:
        ``None`` si no hay ningún contacto con ese group_label.
    """
    contactos = session.scalars(
        select(UserPrefs).where(
            UserPrefs.identity_type == "prodesal_group",
            UserPrefs.group_label == group_label,
        )
    ).all()
    if not contactos:
        return None

    resumenes: list[FarmerSummary] = []
    for prefs in contactos:
        cultivos: list[str] = []
        if prefs.cultivos:
            try:
                parsed = json.loads(prefs.cultivos)
                if isinstance(parsed, list):
                    cultivos = [str(item) for item in parsed]
            except (json.JSONDecodeError, TypeError):
                cultivos = []

        parcelas: list[dict[str, Any]] = []
        if settings.parcela_tracking_enabled and prefs.parcela_consent:
            rows = session.scalars(select(Parcela).where(Parcela.phone_hash == prefs.phone_hash)).all()
            parcelas = [
                {
                    "cultivo": row.cultivo,
                    "superficie_ha": float(row.superficie_ha),
                    "comuna": row.comuna,
                }
                for row in rows
            ]

        resumenes.append(
            FarmerSummary(
                comuna=prefs.comuna,
                localidad=prefs.localidad,
                cultivos=cultivos,
                parcelas=parcelas,
            )
        )
    return resumenes
