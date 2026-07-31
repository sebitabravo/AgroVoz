"""Servicio de historial de consultas con gate de consentimiento (issue #195).

Guarda el historial SOLO si el agricultor dio consentimiento explícito
(``history_consent`` en ``user_prefs``). Sin consentimiento, no guarda nada.

Cumplimiento Ley 21.719:
- Opt-in explícito y específico para historial.
- Sin consentimiento → stateless (comportamiento actual sin cambios).
- Borrado a pedido → irreversible, registrado en auditoría.
- Retención automática: TTL configurable, con purga y evidencia agregada.

Post-MVP: feature-gated. No se llama desde el pipeline productivo hasta
que se active CONSULTATION_HISTORY_ENABLED.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.consultation import Consultation
from app.models.consultation_history import (
    ConsultationHistory,
    ConsultationHistoryDeletionAudit,
)
from app.models.user_prefs import UserPrefs

logger = logging.getLogger(__name__)

HistoryDeletionReason = Literal["user_request", "consent_revoked"]
HistoryDeletionSource = Literal["verified_whatsapp", "admin_api"]
HistoryDeletionOutcome = Literal["completed", "no_records"]
HistoryPurgeOutcome = Literal["disabled", "completed", "no_records"]

HISTORY_CONTEXT_QUERY_MAX_CHARS = 500
HISTORY_CONTEXT_RESPONSE_MAX_CHARS = 1_500
HISTORY_CONTEXT_INTENT_MAX_CHARS = 64
HISTORY_CONTEXT_PRODUCT_MAX_CHARS = 100

_HISTORY_CONTEXT_TRIM_CHARS = " \t\r\n\v\f"
_HISTORY_ELIGIBLE_INTENTS = frozenset(
    {
        "precio",
        "clima",
        "credito",
        "corpus",
    }
)
_AUDIT_KEY_VERSION = 1
_ALLOWED_SOURCES: dict[str, frozenset[str]] = {
    "user_request": frozenset({"verified_whatsapp", "admin_api"}),
    "consent_revoked": frozenset({"verified_whatsapp", "admin_api"}),
}


class HistoryOperationError(RuntimeError):
    """Error operativo que impide asegurar una acción sobre el historial."""


class DeliveredHistorySaveOutcome(StrEnum):
    """Resultados cerrados del guardado posterior a una entrega confirmada."""

    DISABLED = "disabled"
    SAVED = "saved"
    ALREADY_SAVED = "already_saved"
    NOT_ELIGIBLE = "not_eligible"
    NO_CONSENT = "no_consent"
    DATABASE_ERROR = "database_error"


@dataclass(frozen=True, slots=True)
class LatestConsultationContext:
    """Contexto mínimo e inmutable de la última consulta consentida."""

    query_text: str
    response_text: str
    intent: str
    producto: str | None


@dataclass(frozen=True, slots=True)
class HistoryDeletionResult:
    """Resultado inmutable de un borrado auditado o de su reintento."""

    event_id: str
    records_deleted: int
    outcome: HistoryDeletionOutcome


@dataclass(frozen=True, slots=True)
class HistoryPurgeResult:
    """Resultado inmutable de una purga TTL o de un gate desactivado."""

    event_id: str | None
    records_deleted: int
    cutoff_at: datetime
    outcome: HistoryPurgeOutcome


@dataclass(frozen=True, slots=True)
class _HistoryDeletionRequest:
    """Datos mínimos y ya validados para ejecutar un borrado de sujeto."""

    event_id: str
    subject_token: str
    reason: HistoryDeletionReason
    requested_via: HistoryDeletionSource


@dataclass(frozen=True, slots=True)
class _HistoryPurgeRequest:
    """Datos mínimos y validados de una purga TTL agregada."""

    event_id: str
    cutoff_at: datetime


def save_to_history_if_consented(
    phone_hash: str,
    query_text: str,
    response_text: str,
    intent: str,
    producto: str | None = None,
) -> bool:
    """Guarda la consulta en el historial SOLO si hay consentimiento.

    Args:
        phone_hash: Hash del número de teléfono.
        query_text: Texto transcrito por Whisper.
        response_text: Respuesta generada por el LLM.
        intent: Tipo de consulta ("precio", "clima", etc).
        producto: Producto detectado (opcional).

    Returns:
        True si se guardó, False si no había consentimiento o falló.
    """
    if not settings.consultation_history_enabled:
        logger.debug("Historial no guardado — feature gate desactivado")
        return False
    if intent not in _HISTORY_ELIGIBLE_INTENTS:
        logger.debug("Historial no guardado — intención no sustantiva")
        return False
    if not query_text.strip() or not response_text.strip():
        logger.debug("Historial no guardado — contenido vacío")
        return False

    session = SessionLocal()
    try:
        # Verificar consentimiento explícito.
        prefs = session.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
        if prefs is None or not prefs.history_consent:
            logger.debug("Historial no guardado — falta consentimiento")
            return False

        entry = ConsultationHistory(
            phone_hash=phone_hash,
            query_text=query_text,
            response_text=response_text,
            producto=producto,
            intent=intent,
        )
        session.add(entry)
        session.commit()
        logger.debug("Historial consentido guardado")
        return True
    except SQLAlchemyError:
        session.rollback()
        logger.error("Error de base de datos guardando historial")
        return False
    finally:
        session.close()


def _get_eligible_delivered_consultation(
    session: Session,
    consultation_id: int,
) -> Consultation | None:
    """Carga solo una consulta entregada con contenido no vacío."""
    return session.scalar(
        select(Consultation).where(
            Consultation.id == consultation_id,
            Consultation.delivery_status == "delivered",
            Consultation.intent.in_(_HISTORY_ELIGIBLE_INTENTS),
            func.length(
                func.trim(
                    Consultation.query_text,
                    _HISTORY_CONTEXT_TRIM_CHARS,
                )
            )
            > 0,
            func.length(
                func.trim(
                    Consultation.response_text,
                    _HISTORY_CONTEXT_TRIM_CHARS,
                )
            )
            > 0,
        )
    )


def _history_source_exists(session: Session, consultation_id: int) -> bool:
    """Indica si la consulta entregada ya originó una fila de historial."""
    return (
        session.scalar(
            select(ConsultationHistory.id).where(ConsultationHistory.source_consultation_id == consultation_id)
        )
        is not None
    )


def _rollback_history_session(session: Session | None) -> None:
    """Revierte sin propagar errores hacia el camino de entrega."""
    if session is None:
        return
    try:
        session.rollback()
    except SQLAlchemyError:
        logger.error("No se pudo revertir una operación de historial")


def _close_history_session(session: Session | None) -> None:
    """Cierra la sesión sin afectar una entrega ya confirmada."""
    if session is None:
        return
    try:
        session.close()
    except SQLAlchemyError:
        logger.error("No se pudo cerrar una sesión de historial")


def save_delivered_consultation_to_history(
    consultation_id: int,
) -> DeliveredHistorySaveOutcome:
    """Guarda una entrega confirmada si el sujeto conserva su consentimiento.

    La función es best-effort para no convertir una falla secundaria del
    historial en un fallo de entrega de WhatsApp.
    """
    if not settings.consultation_history_enabled:
        logger.debug("Historial post-entrega omitido — feature gate desactivado")
        return DeliveredHistorySaveOutcome.DISABLED

    session: Session | None = None
    try:
        session = SessionLocal()
        consultation = _get_eligible_delivered_consultation(
            session,
            consultation_id,
        )
        if consultation is None:
            logger.debug("Historial post-entrega omitido — consulta no elegible")
            return DeliveredHistorySaveOutcome.NOT_ELIGIBLE

        prefs = session.scalar(select(UserPrefs).where(UserPrefs.phone_hash == consultation.phone_hash))
        if prefs is None or not prefs.history_consent:
            logger.debug("Historial post-entrega omitido — falta consentimiento")
            return DeliveredHistorySaveOutcome.NO_CONSENT

        if _history_source_exists(session, consultation_id):
            return DeliveredHistorySaveOutcome.ALREADY_SAVED

        session.add(
            ConsultationHistory(
                source_consultation_id=consultation.id,
                phone_hash=consultation.phone_hash,
                query_text=consultation.query_text,
                response_text=consultation.response_text,
                producto=consultation.producto,
                intent=consultation.intent,
            )
        )
        session.commit()
        logger.debug("Historial post-entrega guardado")
        return DeliveredHistorySaveOutcome.SAVED
    except IntegrityError:
        _rollback_history_session(session)
        try:
            if session is not None and _history_source_exists(
                session,
                consultation_id,
            ):
                return DeliveredHistorySaveOutcome.ALREADY_SAVED
        except SQLAlchemyError:
            pass
        logger.error("Error de base de datos guardando historial post-entrega")
        return DeliveredHistorySaveOutcome.DATABASE_ERROR
    except SQLAlchemyError:
        _rollback_history_session(session)
        logger.error("Error de base de datos guardando historial post-entrega")
        return DeliveredHistorySaveOutcome.DATABASE_ERROR
    finally:
        _close_history_session(session)


def _bounded_history_text(value: object, max_chars: int) -> str:
    """Normaliza un campo ya acotado por SQL sin confiar en su tipo runtime."""
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_chars]


def get_latest_consultation_context(
    phone_hash: str,
) -> LatestConsultationContext | None:
    """Obtiene contexto reciente solo con gate y consentimiento vigentes.

    Los textos se recortan en SQL antes de salir de SQLite para evitar cargar
    contenido ilimitado. Las respuestas meta se excluyen para impedir que una
    consulta sobre el propio historial reemplace el último dato sustantivo.
    """
    if not settings.consultation_history_enabled:
        logger.debug("Contexto histórico omitido — feature gate desactivado")
        return None
    if not phone_hash.strip():
        logger.debug("Contexto histórico omitido — sujeto inválido")
        return None

    session: Session | None = None
    try:
        session = SessionLocal()
        has_consent = session.scalar(
            select(UserPrefs.history_consent).where(
                UserPrefs.phone_hash == phone_hash,
            )
        )
        if has_consent is not True:
            logger.debug("Contexto histórico omitido — falta consentimiento")
            return None

        row = (
            session.execute(
                select(
                    func.substr(
                        func.trim(
                            ConsultationHistory.query_text,
                            _HISTORY_CONTEXT_TRIM_CHARS,
                        ),
                        1,
                        HISTORY_CONTEXT_QUERY_MAX_CHARS,
                    ).label("query_text"),
                    func.substr(
                        func.trim(
                            ConsultationHistory.response_text,
                            _HISTORY_CONTEXT_TRIM_CHARS,
                        ),
                        1,
                        HISTORY_CONTEXT_RESPONSE_MAX_CHARS,
                    ).label("response_text"),
                    func.substr(
                        func.trim(
                            ConsultationHistory.intent,
                            _HISTORY_CONTEXT_TRIM_CHARS,
                        ),
                        1,
                        HISTORY_CONTEXT_INTENT_MAX_CHARS,
                    ).label("intent"),
                    func.substr(
                        func.trim(
                            ConsultationHistory.producto,
                            _HISTORY_CONTEXT_TRIM_CHARS,
                        ),
                        1,
                        HISTORY_CONTEXT_PRODUCT_MAX_CHARS,
                    ).label("producto"),
                )
                .where(
                    ConsultationHistory.phone_hash == phone_hash,
                    ConsultationHistory.created_at >= _history_cutoff(),
                    func.trim(
                        ConsultationHistory.intent,
                        _HISTORY_CONTEXT_TRIM_CHARS,
                    ).in_(_HISTORY_ELIGIBLE_INTENTS),
                )
                .order_by(
                    ConsultationHistory.created_at.desc(),
                    ConsultationHistory.id.desc(),
                )
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None

        query_text = _bounded_history_text(
            row["query_text"],
            HISTORY_CONTEXT_QUERY_MAX_CHARS,
        )
        response_text = _bounded_history_text(
            row["response_text"],
            HISTORY_CONTEXT_RESPONSE_MAX_CHARS,
        )
        if not query_text or not response_text:
            logger.debug("Contexto histórico omitido — contenido no utilizable")
            return None

        producto = _bounded_history_text(
            row["producto"],
            HISTORY_CONTEXT_PRODUCT_MAX_CHARS,
        )
        return LatestConsultationContext(
            query_text=query_text,
            response_text=response_text,
            intent=_bounded_history_text(
                row["intent"],
                HISTORY_CONTEXT_INTENT_MAX_CHARS,
            ),
            producto=producto or None,
        )
    except SQLAlchemyError:
        logger.error("Error de base de datos leyendo contexto histórico")
        return None
    finally:
        _close_history_session(session)


def _build_deletion_request(
    phone_hash: str,
    reason: str,
    requested_via: str,
    event_id: str | None,
) -> _HistoryDeletionRequest:
    """Valida el contexto y genera el token HMAC dedicado de auditoría."""
    if not phone_hash:
        raise HistoryOperationError("El sujeto del borrado no puede estar vacío.")

    allowed_sources = _ALLOWED_SOURCES.get(reason)
    if allowed_sources is None or requested_via not in allowed_sources:
        raise HistoryOperationError("La combinación de motivo y origen del borrado no está permitida.")

    audit_key = _get_audit_key()
    normalized_event_id = _normalize_event_id(event_id)

    subject_token = hmac.new(
        audit_key.encode("utf-8"),
        phone_hash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return _HistoryDeletionRequest(
        event_id=normalized_event_id,
        subject_token=subject_token,
        reason=cast(HistoryDeletionReason, reason),
        requested_via=cast(HistoryDeletionSource, requested_via),
    )


def _get_audit_key() -> str:
    """Obtiene la clave dedicada o rechaza una operación no auditable."""
    audit_key = settings.consultation_history_audit_key.get_secret_value()
    if len(audit_key.strip()) < 32:
        raise HistoryOperationError("La clave dedicada de auditoría no está configurada de forma segura.")
    return audit_key


def _normalize_event_id(event_id: str | None) -> str:
    """Normaliza el UUID idempotente o genera uno para una operación nueva."""
    try:
        return str(UUID(event_id)) if event_id else str(uuid4())
    except (AttributeError, ValueError) as exc:
        raise HistoryOperationError("El event_id no es un UUID válido.") from exc


def _normalize_utc(value: datetime) -> datetime:
    """Normaliza una fecha aware a UTC y rechaza fechas sin zona horaria."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise HistoryOperationError("La fecha de purga debe incluir zona horaria.")
    return value.astimezone(UTC)


def _history_cutoff(now: datetime | None = None) -> datetime:
    """Instante mínimo de ``created_at`` para que una fila siga vigente.

    Lo usan tanto la purga como las lecturas: una fila anterior al corte ya
    venció aunque la purga programada todavía no haya pasado, y no puede
    seguir saliendo por ninguna vía.
    """
    return _normalize_utc(now or datetime.now(UTC)) - timedelta(days=settings.consultation_history_ttl_days)


def _get_idempotent_result(
    session: Session,
    request: _HistoryDeletionRequest,
) -> HistoryDeletionResult | None:
    """Retorna un reintento válido y rechaza reutilizaciones incompatibles."""
    existing = session.scalar(
        select(ConsultationHistoryDeletionAudit).where(ConsultationHistoryDeletionAudit.event_id == request.event_id)
    )
    if existing is None:
        return None

    matches_request = (
        existing.subject_token == request.subject_token
        and existing.key_version == _AUDIT_KEY_VERSION
        and existing.reason == request.reason
        and existing.requested_via == request.requested_via
    )
    if not matches_request:
        raise HistoryOperationError("El event_id ya pertenece a otra solicitud de borrado.")

    return HistoryDeletionResult(
        event_id=existing.event_id,
        records_deleted=existing.records_deleted,
        outcome=cast(HistoryDeletionOutcome, existing.outcome),
    )


def _get_idempotent_purge_result(
    session: Session,
    request: _HistoryPurgeRequest,
) -> HistoryPurgeResult | None:
    """Retorna una purga repetida y rechaza UUID con otro cutoff."""
    existing = session.scalar(
        select(ConsultationHistoryDeletionAudit).where(ConsultationHistoryDeletionAudit.event_id == request.event_id)
    )
    if existing is None:
        return None

    existing_cutoff = existing.cutoff_at
    if existing_cutoff is not None and existing_cutoff.tzinfo is None:
        # SQLite pierde el tzinfo al leer, pero AgroVoz persiste siempre UTC.
        existing_cutoff = existing_cutoff.replace(tzinfo=UTC)

    matches_request = (
        existing.subject_token is None
        and existing.key_version == _AUDIT_KEY_VERSION
        and existing.reason == "ttl"
        and existing.requested_via == "system_retention"
        and existing_cutoff is not None
        and existing_cutoff.astimezone(UTC) == request.cutoff_at
    )
    if not matches_request:
        raise HistoryOperationError("El event_id ya pertenece a otra operación de historial.")

    return HistoryPurgeResult(
        event_id=existing.event_id,
        records_deleted=existing.records_deleted,
        cutoff_at=request.cutoff_at,
        outcome=cast(HistoryPurgeOutcome, existing.outcome),
    )


def delete_history(
    phone_hash: str,
    *,
    reason: HistoryDeletionReason = "user_request",
    requested_via: HistoryDeletionSource = "verified_whatsapp",
    event_id: str | None = None,
) -> HistoryDeletionResult:
    """Borra historial y redacta consultas dentro de una transacción auditada.

    ``records_deleted`` cuenta exclusivamente filas de
    ``ConsultationHistory`` para conservar el contrato de auditoría. La
    redacción de ``Consultation`` siempre se ejecuta, incluso si no había
    historial separado. Un reintento confirmado por ``event_id`` retorna antes
    de abrir la mutación.

    Args:
        phone_hash: Hash del número de teléfono.
        reason: Motivo cerrado del borrado.
        requested_via: Origen autenticado que inició la operación.
        event_id: UUID opcional para reintentos idempotentes.

    Returns:
        Resultado auditado, incluida la cantidad de registros eliminados.

    Raises:
        HistoryOperationError: Si no se puede garantizar borrado y auditoría.
    """
    request = _build_deletion_request(
        phone_hash,
        reason,
        requested_via,
        event_id,
    )
    session = SessionLocal()
    try:
        existing_result = _get_idempotent_result(session, request)
        if existing_result is not None:
            return existing_result

        connection = session.connection()
        if request.reason == "consent_revoked":
            connection.execute(
                update(UserPrefs).where(UserPrefs.phone_hash == phone_hash).values(history_consent=False)
            )
        connection.execute(
            update(Consultation).where(Consultation.phone_hash == phone_hash).values(query_text="", response_text="")
        )
        delete_result = connection.execute(
            delete(ConsultationHistory).where(ConsultationHistory.phone_hash == phone_hash)
        )
        records_deleted = max(delete_result.rowcount or 0, 0)
        outcome: HistoryDeletionOutcome = "completed" if records_deleted > 0 else "no_records"
        session.add(
            ConsultationHistoryDeletionAudit(
                event_id=request.event_id,
                subject_token=request.subject_token,
                key_version=_AUDIT_KEY_VERSION,
                reason=request.reason,
                records_deleted=records_deleted,
                requested_via=request.requested_via,
                cutoff_at=None,
                outcome=outcome,
            )
        )
        session.commit()
        logger.info(
            "Solicitud de borrado de historial completada — registros=%d",
            records_deleted,
        )
        return HistoryDeletionResult(
            event_id=request.event_id,
            records_deleted=records_deleted,
            outcome=outcome,
        )
    except IntegrityError as exc:
        session.rollback()
        # Una carrera puede haber confirmado el mismo event_id en otra sesión.
        existing_result = _get_idempotent_result(session, request)
        if existing_result is not None:
            return existing_result
        logger.error("Falló la transacción de borrado auditado")
        raise HistoryOperationError("No fue posible confirmar el borrado auditado.") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        logger.error("Falló la transacción de borrado auditado")
        raise HistoryOperationError("No fue posible confirmar el borrado auditado.") from exc
    finally:
        session.close()


def purge_expired_history(
    *,
    now: datetime | None = None,
    event_id: str | None = None,
) -> HistoryPurgeResult:
    """Purga historial anterior al TTL y registra una auditoría agregada.

    Args:
        now: Instante aware inyectable; por defecto usa la hora actual UTC.
        event_id: UUID opcional para reintentos idempotentes.

    Returns:
        Resultado tipado con cutoff y cantidad eliminada.

    Raises:
        HistoryOperationError: Si no se puede confirmar purga y auditoría.
    """
    cutoff_at = _history_cutoff(now)
    if not settings.consultation_history_enabled:
        logger.debug("Purga de historial omitida — feature gate desactivado")
        return HistoryPurgeResult(
            event_id=None,
            records_deleted=0,
            cutoff_at=cutoff_at,
            outcome="disabled",
        )

    _get_audit_key()
    request = _HistoryPurgeRequest(
        event_id=_normalize_event_id(event_id),
        cutoff_at=cutoff_at,
    )
    session = SessionLocal()
    try:
        existing_result = _get_idempotent_purge_result(session, request)
        if existing_result is not None:
            return existing_result

        delete_result = session.connection().execute(
            delete(ConsultationHistory).where(ConsultationHistory.created_at < request.cutoff_at)
        )
        records_deleted = max(delete_result.rowcount or 0, 0)
        outcome: HistoryDeletionOutcome = "completed" if records_deleted > 0 else "no_records"
        session.add(
            ConsultationHistoryDeletionAudit(
                event_id=request.event_id,
                subject_token=None,
                key_version=_AUDIT_KEY_VERSION,
                reason="ttl",
                records_deleted=records_deleted,
                requested_via="system_retention",
                cutoff_at=request.cutoff_at,
                outcome=outcome,
            )
        )
        session.commit()
        logger.info(
            "Purga TTL de historial completada — registros=%d",
            records_deleted,
        )
        return HistoryPurgeResult(
            event_id=request.event_id,
            records_deleted=records_deleted,
            cutoff_at=request.cutoff_at,
            outcome=outcome,
        )
    except IntegrityError as exc:
        session.rollback()
        existing_result = _get_idempotent_purge_result(session, request)
        if existing_result is not None:
            return existing_result
        logger.error("Falló la transacción de purga TTL auditada")
        raise HistoryOperationError("No fue posible confirmar la purga TTL auditada.") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        logger.error("Falló la transacción de purga TTL auditada")
        raise HistoryOperationError("No fue posible confirmar la purga TTL auditada.") from exc
    finally:
        session.close()


def get_history(phone_hash: str, limit: int = 5) -> list[dict[str, object]]:
    """Obtiene consultas vigentes de un sujeto que consintió el historial.

    Devuelve ``query_text`` y ``response_text`` en crudo, así que aplica los
    mismos tres cortes que el resto del servicio antes de leer: gate propio,
    consentimiento del sujeto y TTL. No alcanza con que el caller sea
    confiable, porque el dato que retorna es justamente el que protegen.

    Args:
        phone_hash: Hash del número de teléfono.
        limit: Máximo de registros a retornar.

    Returns:
        Lista de diccionarios con los registros más recientes primero, o
        lista vacía si falta el gate, el consentimiento o no hay filas.
    """
    if not settings.consultation_history_enabled:
        logger.debug("Historial omitido — feature gate desactivado")
        return []
    if not phone_hash.strip():
        logger.debug("Historial omitido — sujeto inválido")
        return []

    session = SessionLocal()
    try:
        has_consent = session.scalar(
            select(UserPrefs.history_consent).where(UserPrefs.phone_hash == phone_hash)
        )
        if has_consent is not True:
            logger.debug("Historial omitido — falta consentimiento")
            return []

        stmt = (
            select(ConsultationHistory)
            .where(
                ConsultationHistory.phone_hash == phone_hash,
                ConsultationHistory.created_at >= _history_cutoff(),
            )
            .order_by(ConsultationHistory.created_at.desc())
            .limit(limit)
        )
        entries = session.scalars(stmt).all()
        return [
            {
                "query": e.query_text,
                "response": e.response_text,
                "producto": e.producto,
                "intent": e.intent,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in entries
        ]
    except SQLAlchemyError:
        logger.error("Error de base de datos leyendo historial")
        return []
    finally:
        session.close()
