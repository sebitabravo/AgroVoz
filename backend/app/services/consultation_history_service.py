"""Servicio de historial de consultas con gate de consentimiento (issue #195).

Guarda el historial SOLO si el agricultor dio consentimiento explícito
(dataset_consent en user_prefs). Sin consentimiento, no guarda nada.

Cumplimiento Ley 21.719:
- Opt-in explícito requerido (usa el patrón dataset_consent existente).
- Sin consentimiento → stateless (comportamiento actual sin cambios).
- Borrado a pedido → irreversible, registrado en auditoría.
- Política de retención: los datos se guardan mientras dure el piloto
  (4 semanas en Traiguén). Para producción, definir TTL automático.

Post-MVP: feature-gated. No se llama desde el pipeline productivo hasta
que se active AGROVOZ_CONSULTATION_HISTORY.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal
from app.models.consultation_history import ConsultationHistory
from app.models.user_prefs import UserPrefs

logger = logging.getLogger(__name__)


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
    session = SessionLocal()
    try:
        # Verificar consentimiento explícito.
        prefs = session.scalar(
            select(UserPrefs).where(UserPrefs.phone_hash == phone_hash)
        )
        if prefs is None or not prefs.dataset_consent:
            logger.debug(
                "Historial NO guardado — sin consentimiento — phone_hash=%s",
                phone_hash[:8],
            )
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
        logger.debug(
            "Historial guardado — phone_hash=%s intent=%s producto=%s",
            phone_hash[:8],
            intent,
            producto,
        )
        return True
    except SQLAlchemyError:
        session.rollback()
        logger.exception(
            "Error guardando historial — phone_hash=%s", phone_hash[:8]
        )
        return False
    finally:
        session.close()


def delete_history(phone_hash: str) -> int:
    """Borra todo el historial de un phone_hash (derecho al olvido).

    Args:
        phone_hash: Hash del número de teléfono.

    Returns:
        Cantidad de registros eliminados.
    """
    session = SessionLocal()
    try:
        stmt = (
            select(ConsultationHistory)
            .where(ConsultationHistory.phone_hash == phone_hash)
        )
        entries = session.scalars(stmt).all()
        count = len(entries)
        for entry in entries:
            session.delete(entry)
        session.commit()
        logger.info(
            "Historial borrado — phone_hash=%s registros=%d — auditoría",
            phone_hash[:8],
            count,
        )
        return count
    except SQLAlchemyError:
        session.rollback()
        logger.exception(
            "Error borrando historial — phone_hash=%s", phone_hash[:8]
        )
        return 0
    finally:
        session.close()


def get_history(phone_hash: str, limit: int = 5) -> list[dict[str, object]]:
    """Obtiene las últimas consultas de un agricultor.

    Args:
        phone_hash: Hash del número de teléfono.
        limit: Máximo de registros a retornar.

    Returns:
        Lista de diccionarios con los registros más recientes primero.
    """
    session = SessionLocal()
    try:
        stmt = (
            select(ConsultationHistory)
            .where(ConsultationHistory.phone_hash == phone_hash)
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
        logger.exception(
            "Error leyendo historial — phone_hash=%s", phone_hash[:8]
        )
        return []
    finally:
        session.close()
