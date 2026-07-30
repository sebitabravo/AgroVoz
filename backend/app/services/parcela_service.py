"""Registro consentido de parcelas del agricultor con TTL y borrado (C5).

Alimenta el motor de reglas agronómicas (cultivo) y el clima por parcela
(comuna). La feature permanece apagada por defecto. Cuando se habilite, exige
además ``parcela_consent=True`` para la identidad seudonimizada. Nunca
persiste el número de WhatsApp ni texto libre fuera de cultivo/comuna.
"""

from __future__ import annotations

import datetime
import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Result, delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.phone_hash import validate_phone_hash
from app.models.parcela import Parcela
from app.models.user_prefs import UserPrefs

logger = logging.getLogger(__name__)

_MAX_SUPERFICIE_HA = Decimal("100000")


class ParcelaOperationError(RuntimeError):
    """La operación no pudo confirmarse en SQLite."""


def _affected_rows(result: Result[Any]) -> int:
    """Lee ``rowcount`` de un DELETE sin depender del tipo concreto del driver."""
    return int(getattr(result, "rowcount", 0) or 0)


def _utcnow_naive() -> datetime.datetime:
    """Entrega UTC sin tzinfo, formato estable para SQLite."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _normalize_required_text(value: str, *, max_length: int) -> str | None:
    """Colapsa whitespace/control y aplica un tope antes de persistir."""
    cleaned = " ".join(str(value).split()).strip().lower()
    if not cleaned or len(cleaned) > max_length:
        return None
    return cleaned


def _parse_superficie_ha(raw_superficie: str) -> Decimal | None:
    """Convierte una superficie hablada en hectáreas a un Decimal positivo.

    Acepta ejemplos como ``2``, ``2.5`` y ``2,5``. Rechaza cero, negativos,
    valores no numéricos y superficies fuera de un dominio razonable para
    evitar registros accidentales o abusivos.
    """
    text = str(raw_superficie).strip().replace(",", ".")
    try:
        superficie = Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    if superficie <= 0 or superficie > _MAX_SUPERFICIE_HA:
        return None
    return superficie


def _has_parcela_consent(session: Session, phone_hash: str) -> bool:
    """Comprueba el opt-in específico sin cargar otros datos personales."""
    return session.scalar(select(UserPrefs.parcela_consent).where(UserPrefs.phone_hash == phone_hash)) is True


def register_parcela_for_llm(
    session: Session,
    cultivo: str,
    superficie_ha: str,
    comuna: str,
    phone_hash: str = "",
) -> str:
    """Registra una parcela solo con gate, identidad válida y consentimiento.

    Args:
        session: Sesión SQLAlchemy exclusiva de la tool.
        cultivo: Cultivo de la parcela, ej. "papa" o "trigo".
        superficie_ha: Superficie en hectáreas, con formato numérico.
        comuna: Comuna donde está la parcela.
        phone_hash: Identidad HMAC-SHA256, nunca teléfono en claro.

    Returns:
        Confirmación o explicación de por qué no se guardó.
    """
    if not settings.parcela_tracking_enabled:
        return "El registro de parcelas todavía no está habilitado. No guardé la parcela que indicaste."
    if not validate_phone_hash(phone_hash):
        return "No pude asociar la parcela de forma segura. No guardé la parcela que indicaste."
    if not _has_parcela_consent(session, phone_hash):
        return "No tengo tu consentimiento para guardar parcelas. No guardé la parcela que indicaste."

    normalized_cultivo = _normalize_required_text(cultivo, max_length=100)
    normalized_comuna = _normalize_required_text(comuna, max_length=100)
    superficie = _parse_superficie_ha(superficie_ha)

    if normalized_cultivo is None:
        return "No entendí qué cultivo tiene la parcela. ¿Podrías repetirlo?"
    if normalized_comuna is None:
        return "No entendí en qué comuna está la parcela. ¿Podrías repetirla?"
    if superficie is None:
        return "No entendí la superficie de la parcela. ¿Podrías repetir cuántas hectáreas son?"

    expires_at = _utcnow_naive() + datetime.timedelta(days=settings.parcela_retention_days)
    session.add(
        Parcela(
            phone_hash=phone_hash,
            cultivo=normalized_cultivo,
            superficie_ha=superficie,
            comuna=normalized_comuna,
            expires_at=expires_at,
        )
    )
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.error("No se pudo persistir la parcela — error de base de datos")
        return "Tuve un problema al guardar la parcela. ¿Probamos de nuevo?"

    return (
        f"Listo. Registré tu parcela de {normalized_cultivo} de {superficie} hectáreas "
        f"en {normalized_comuna}. Se eliminará automáticamente en {settings.parcela_retention_days} días."
    )


def get_parcelas_for_llm(session: Session, phone_hash: str = "") -> str:
    """Lista las parcelas vigentes del agricultor, si el gate y el consentimiento siguen activos."""
    if not settings.parcela_tracking_enabled:
        return "El registro de parcelas todavía no está habilitado."
    if not validate_phone_hash(phone_hash):
        return "No pude asociar tus parcelas de forma segura."
    if not _has_parcela_consent(session, phone_hash):
        return "No tengo tu consentimiento para consultar parcelas."

    now = _utcnow_naive()
    parcelas = session.scalars(
        select(Parcela).where(Parcela.phone_hash == phone_hash, Parcela.expires_at > now).order_by(Parcela.created_at)
    ).all()
    if not parcelas:
        return "No tienes parcelas registradas."

    detalle = "; ".join(f"{p.cultivo} ({p.superficie_ha} ha) en {p.comuna}" for p in parcelas)
    return f"Tienes {len(parcelas)} parcela(s) registrada(s): {detalle}."


def delete_parcelas_for_subject(
    phone_hash: str,
    *,
    session: Session | None = None,
) -> int:
    """Borra todas las parcelas del sujeto, incluso si el feature gate está apagado."""
    if not validate_phone_hash(phone_hash):
        raise ValueError("phone_hash inválido")

    owns_session = session is None
    db = session or SessionLocal()
    try:
        result = db.execute(delete(Parcela).where(Parcela.phone_hash == phone_hash))
        records_deleted = _affected_rows(result)
        db.commit()
        logger.info("Parcelas eliminadas a pedido — registros=%d", records_deleted)
        return records_deleted
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("No se pudo confirmar el borrado de parcelas")
        raise ParcelaOperationError("parcela_deletion_failed") from exc
    finally:
        if owns_session:
            db.close()


def purge_expired_parcelas(
    *,
    session: Session | None = None,
    now: datetime.datetime | None = None,
) -> int:
    """Elimina filas vencidas sin depender del gate de nuevas escrituras."""
    cutoff = now or _utcnow_naive()
    owns_session = session is None
    db = session or SessionLocal()
    try:
        result = db.execute(delete(Parcela).where(Parcela.expires_at <= cutoff))
        records_deleted = _affected_rows(result)
        db.commit()
        logger.info("Purga TTL de parcelas finalizada — registros=%d", records_deleted)
        return records_deleted
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("No se pudo confirmar la purga TTL de parcelas")
        raise ParcelaOperationError("parcela_purge_failed") from exc
    finally:
        if owns_session:
            db.close()
