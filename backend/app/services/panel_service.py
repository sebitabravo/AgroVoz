"""Panel PWA del agricultor: link firmado, sin login ni contraseña (C3).

Complemento opcional a WhatsApp — nunca lo reemplaza ni lo exige. El
agricultor pide un resumen y recibe un link de un solo uso, firmado con HMAC
y de duración corta. No es una cuenta ni una sesión persistente: vencido el
link, hay que pedir uno nuevo por WhatsApp. Coherente con el hard constraint
"sin autenticación de usuarios" — no se crea ningún sistema de login.

El resumen solo expone lo que el productor ya consintió explícitamente
(parcelas, alertas): revocar un consentimiento lo saca del resumen de
inmediato, sin cambios en este servicio.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.phone_hash import validate_phone_hash
from app.models.alert import Alert
from app.models.parcela import Parcela
from app.models.user_prefs import UserPrefs

logger = logging.getLogger(__name__)

_TOKEN_SEPARATOR = "."


class PanelLinkError(RuntimeError):
    """El link no pudo generarse o el secreto no está configurado de forma segura."""


def _utcnow_naive() -> datetime.datetime:
    """Entrega UTC sin tzinfo, mismo formato que usa parcela_service en SQLite."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _get_link_secret() -> str:
    """Obtiene la clave dedicada o rechaza una operación no firmable."""
    secret = settings.panel_link_secret.get_secret_value()
    if len(secret.strip()) < 32:
        raise PanelLinkError("panel_link_secret no está configurada de forma segura")
    return secret


def _sign(phone_hash: str, expires_at: int) -> str:
    """Firma phone_hash + expiración con la clave dedicada del panel."""
    secret = _get_link_secret()
    message = f"{phone_hash}{_TOKEN_SEPARATOR}{expires_at}"
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_panel_token(phone_hash: str, *, now: int | None = None) -> str:
    """Genera un token firmado y de duración corta para el panel.

    Args:
        phone_hash: Identidad HMAC-SHA256 del productor.
        now: Timestamp Unix inyectable para tests.

    Returns:
        Token ``{phone_hash}.{expires_at}.{firma}``.

    Raises:
        ValueError: Si phone_hash no tiene formato válido.
        PanelLinkError: Si la clave de firma no está configurada de forma segura.
    """
    if not validate_phone_hash(phone_hash):
        raise ValueError("phone_hash inválido")

    effective_now = now if now is not None else int(time.time())
    expires_at = effective_now + settings.panel_link_ttl_hours * 3600
    signature = _sign(phone_hash, expires_at)
    return f"{phone_hash}{_TOKEN_SEPARATOR}{expires_at}{_TOKEN_SEPARATOR}{signature}"


def verify_panel_token(token: str, *, now: int | None = None) -> str | None:
    """Verifica un token del panel y retorna el phone_hash si es válido.

    Returns:
        El phone_hash si el token es válido y no ha vencido, o ``None`` si es
        inválido, está corrupto o venció. Nunca lanza sobre entrada no confiable.
    """
    parts = token.split(_TOKEN_SEPARATOR)
    if len(parts) != 3:
        return None
    phone_hash, expires_at_raw, signature = parts
    if not validate_phone_hash(phone_hash):
        return None
    try:
        expires_at = int(expires_at_raw)
    except ValueError:
        return None

    try:
        expected_signature = _sign(phone_hash, expires_at)
    except PanelLinkError:
        return None
    if not hmac.compare_digest(expected_signature, signature):
        return None

    effective_now = now if now is not None else int(time.time())
    if effective_now > expires_at:
        return None
    return phone_hash


def get_panel_link_for_llm(phone_hash: str = "") -> str:
    """Genera el mensaje con el link del panel para enviar por WhatsApp.

    Returns:
        Mensaje con el link, o explicación de por qué no se generó.
    """
    if not settings.farmer_panel_enabled:
        return "El panel web todavía no está disponible."
    if not settings.panel_base_url:
        return "El panel web todavía no está disponible."
    if not validate_phone_hash(phone_hash):
        return "No pude generar tu link de forma segura."

    try:
        token = generate_panel_token(phone_hash)
    except PanelLinkError:
        logger.error("No se pudo firmar el link del panel — clave insegura")
        return "Tuve un problema generando tu link. ¿Probamos de nuevo?"

    link = f"{settings.panel_base_url.rstrip('/')}/{token}"
    horas = settings.panel_link_ttl_hours
    return f"Aquí está tu resumen: {link} El link vence en {horas} horas."


@dataclass(frozen=True, slots=True)
class PanelSummary:
    """Resumen del panel: solo lo que el productor ya consintió."""

    comuna: str | None
    cultivos: list[str]
    parcelas: list[dict[str, Any]]
    alertas: list[dict[str, Any]]


def get_panel_summary(session: Session, phone_hash: str) -> PanelSummary | None:
    """Arma el resumen del panel respetando cada consentimiento por separado.

    Returns:
        ``None`` si el phone_hash no tiene preferencias registradas.
    """
    if not settings.farmer_panel_enabled:
        return None

    prefs = session.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
    if prefs is None:
        return None

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
        # Solo parcelas vigentes: una fila vencida ya no debe mostrarse aunque la
        # purga programada todavía no haya pasado. Mismo criterio que
        # parcela_service.get_parcelas_for_llm.
        parcela_rows = session.scalars(
            select(Parcela).where(Parcela.phone_hash == phone_hash, Parcela.expires_at > _utcnow_naive())
        ).all()
        parcelas = [
            {
                "cultivo": parcela.cultivo,
                "superficie_ha": float(parcela.superficie_ha),
                "comuna": parcela.comuna,
            }
            for parcela in parcela_rows
        ]

    alertas: list[dict[str, Any]] = []
    if prefs.alert_consent:
        alert_rows = session.scalars(select(Alert).where(Alert.phone_hash == phone_hash, Alert.activa.is_(True))).all()
        alertas = [
            {
                "tipo": alert.tipo,
                "producto": alert.producto,
                "condicion": alert.condicion,
                "umbral": float(alert.umbral) if alert.umbral is not None else None,
            }
            for alert in alert_rows
        ]

    return PanelSummary(
        comuna=prefs.comuna,
        cultivos=cultivos,
        parcelas=parcelas,
        alertas=alertas,
    )
