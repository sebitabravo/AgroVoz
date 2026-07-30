"""Transiciones persistentes del estado de entrega de una consulta."""

import datetime
import logging
from typing import Final, Literal

from sqlalchemy import or_, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core import database
from app.models.consultation import Consultation

logger = logging.getLogger(__name__)

_DeliveryStatus = Literal["delivered", "failed"]
_STAGING_RETENTION_HOURS: Final[int] = 24

# Lista cerrada: impide persistir mensajes de excepción, teléfonos u otra PII.
_ALLOWED_DELIVERY_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        "openwa_connection_error",
        "openwa_http_error",
        "openwa_invalid_response",
        "openwa_rejected",
        "openwa_request_error",
        "openwa_send_failed",
        "openwa_timeout",
        "openwa_unexpected_error",
        "pipeline_no_response",
    }
)


class ContentRedactionError(RuntimeError):
    """La limpieza de contenido transitorio no pudo confirmarse."""


def mark_delivery_delivered(consultation_id: int) -> bool:
    """Marca una consulta como entregada y registra el instante UTC."""
    return _persist_delivery_state(
        consultation_id,
        status="delivered",
        error_code=None,
    )


def mark_delivery_failed(consultation_id: int, error_code: str) -> bool:
    """Marca una entrega fallida y elimina su contenido transitorio."""
    if error_code not in _ALLOWED_DELIVERY_ERROR_CODES:
        logger.warning(
            "Código de entrega rechazado para consultation_id=%s",
            consultation_id,
        )
        return False

    return _persist_delivery_state(
        consultation_id,
        status="failed",
        error_code=error_code,
    )


def redact_consultation_content(consultation_id: int) -> bool:
    """Elimina pregunta y respuesta transitorias tras procesar la entrega."""
    if consultation_id <= 0:
        return False

    db: Session | None = None
    try:
        db = database.SessionLocal()
        consultation = db.get(Consultation, consultation_id)
        if consultation is None:
            return False
        _redact_content(consultation)
        db.commit()
        return True
    except SQLAlchemyError:
        if db is not None:
            _rollback_safely(db)
        logger.error(
            "No se pudo redactar contenido para consultation_id=%s",
            consultation_id,
        )
        return False
    finally:
        if db is not None:
            _close_safely(db)


def redact_stale_consultation_content(
    *,
    now: datetime.datetime | None = None,
) -> int:
    """Redacta staging con más de 24 horas, incluso tras reinicios abruptos."""
    reference = now or datetime.datetime.now(datetime.UTC)
    if reference.tzinfo is not None:
        reference = reference.astimezone(datetime.UTC).replace(tzinfo=None)
    cutoff = reference - datetime.timedelta(hours=_STAGING_RETENTION_HOURS)

    db: Session | None = None
    try:
        db = database.SessionLocal()
        result = db.execute(
            update(Consultation)
            .where(
                Consultation.created_at < cutoff,
                or_(
                    Consultation.query_text != "",
                    Consultation.response_text != "",
                ),
            )
            .values(query_text="", response_text="")
        )
        db.commit()
        records_redacted = int(getattr(result, "rowcount", 0) or 0)
        logger.info(
            "Limpieza de contenido transitorio finalizada — registros=%d",
            records_redacted,
        )
        return max(records_redacted, 0)
    except SQLAlchemyError as exc:
        if db is not None:
            _rollback_safely(db)
        logger.error("No se pudo confirmar limpieza de contenido transitorio")
        raise ContentRedactionError("No se pudo confirmar limpieza de contenido transitorio") from exc
    finally:
        if db is not None:
            _close_safely(db)


def _persist_delivery_state(
    consultation_id: int,
    *,
    status: _DeliveryStatus,
    error_code: str | None,
) -> bool:
    if consultation_id <= 0:
        return False

    db: Session | None = None
    try:
        db = database.SessionLocal()
        consultation = db.get(Consultation, consultation_id)
        if consultation is None:
            return False

        _apply_delivery_state(consultation, status=status, error_code=error_code)
        db.commit()
        return True
    except SQLAlchemyError:
        if db is not None:
            _rollback_safely(db)
        logger.error(
            "No se pudo persistir el estado de entrega para consultation_id=%s",
            consultation_id,
        )
        return False
    finally:
        if db is not None:
            _close_safely(db)


def _apply_delivery_state(
    consultation: Consultation,
    *,
    status: _DeliveryStatus,
    error_code: str | None,
) -> None:
    consultation.delivery_status = status
    if status == "delivered":
        consultation.delivered_at = _utc_now()
        consultation.delivery_error_code = None
        return

    consultation.delivered_at = None
    consultation.delivery_error_code = error_code
    consultation.requires_review = True
    _redact_content(consultation)


def _redact_content(consultation: Consultation) -> None:
    """Vacía contenido libre sin eliminar las métricas de la consulta."""
    consultation.query_text = ""
    consultation.response_text = ""


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _rollback_safely(db: Session) -> None:
    try:
        db.rollback()
    except SQLAlchemyError:
        logger.error("No se pudo revertir la transacción de estado de entrega")


def _close_safely(db: Session) -> None:
    try:
        db.close()
    except SQLAlchemyError:
        logger.error("No se pudo cerrar la sesión de estado de entrega")
