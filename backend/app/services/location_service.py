"""Persistencia de la ubicación compartida por WhatsApp.

La identidad sigue siendo el ``phone_hash``; nunca se guarda el chat ID en
claro. El servicio es síncrono porque el proyecto usa SQLAlchemy síncrono y
los callers asíncronos lo ejecutan con ``asyncio.to_thread``.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core import database
from app.core.config import settings
from app.core.phone_hash import validate_phone_hash
from app.models.user_prefs import UserPrefs

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UserLocation:
    """Coordenadas decimales guardadas para una identidad de WhatsApp."""

    lat: float
    lng: float


class LocationConsentError(RuntimeError):
    """La ubicación no puede guardarse sin gate y consentimiento explícito."""


class LocationOperationError(RuntimeError):
    """La operación de retención de ubicación no pudo confirmarse en SQLite."""


def _validate_coordinates(lat: float, lng: float) -> None:
    """Valida rangos WGS84 antes de tocar SQLite."""
    if not (-90.0 <= lat <= 90.0):
        raise ValueError("La latitud debe estar entre -90° y 90°")
    if not (-180.0 <= lng <= 180.0):
        raise ValueError("La longitud debe estar entre -180° y 180°")


def _utcnow_naive() -> datetime.datetime:
    """Entrega UTC sin tzinfo, formato estable para SQLite."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def save_user_location(phone_hash: str, lat: float, lng: float) -> UserLocation:
    """Crea o actualiza la ubicación de una identidad seudonimizada.

    La ubicación compartida reemplaza la anterior para que siempre se consulte
    la parcela más recientemente elegida por el productor.
    """
    if not settings.location_sharing_enabled:
        raise LocationConsentError("location_sharing_disabled")
    if not validate_phone_hash(phone_hash):
        raise ValueError("phone_hash inválido")
    _validate_coordinates(lat, lng)

    session = database.SessionLocal()
    try:
        prefs = session.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
        if prefs is None or prefs.location_consent is not True:
            raise LocationConsentError("location_consent_required")
        prefs.lat = lat
        prefs.lng = lng
        prefs.location_updated_at = _utcnow_naive()
        session.commit()
        return UserLocation(lat=lat, lng=lng)
    except SQLAlchemyError as exc:
        session.rollback()
        raise LocationOperationError("location_save_failed") from exc
    finally:
        session.close()


def get_user_location(phone_hash: str | None) -> UserLocation | None:
    """Obtiene coordenadas guardadas o ``None`` si aún no existen."""
    if phone_hash is None or not validate_phone_hash(phone_hash):
        return None

    session = database.SessionLocal()
    try:
        prefs = session.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
        if (
            prefs is None
            or prefs.location_consent is not True
            or prefs.lat is None
            or prefs.lng is None
        ):
            return None
        return UserLocation(lat=float(prefs.lat), lng=float(prefs.lng))
    finally:
        session.close()


def clear_user_location(phone_hash: str) -> bool:
    """Revoca el pin GPS guardado sin borrar otras preferencias del contacto."""
    if not validate_phone_hash(phone_hash):
        raise ValueError("phone_hash inválido")

    session = database.SessionLocal()
    try:
        prefs = session.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
        if prefs is None or (prefs.lat is None and prefs.lng is None):
            return False
        prefs.lat = None
        prefs.lng = None
        prefs.location_updated_at = None
        session.commit()
        return True
    except SQLAlchemyError as exc:
        session.rollback()
        raise LocationOperationError("location_clear_failed") from exc
    finally:
        session.close()


def purge_expired_locations(
    *,
    session: Session | None = None,
    now: datetime.datetime | None = None,
) -> int:
    """Limpia pins cuyo timestamp excede la retención configurada.

    La fila de preferencias se conserva para no borrar comuna, cultivos ni
    otros consentimientos del productor.
    """
    cutoff = now or _utcnow_naive()
    cutoff -= datetime.timedelta(days=settings.location_retention_days)
    owns_session = session is None
    db = session or database.SessionLocal()
    try:
        locations = db.scalars(
            select(UserPrefs).where(
                or_(
                    UserPrefs.location_updated_at <= cutoff,
                    and_(
                        UserPrefs.location_updated_at.is_(None),
                        UserPrefs.lat.is_not(None),
                        UserPrefs.lng.is_not(None),
                    ),
                )
            )
        ).all()
        for prefs in locations:
            prefs.lat = None
            prefs.lng = None
            prefs.location_updated_at = None
        db.commit()
        logger.info("Purga TTL de ubicaciones finalizada — registros=%d", len(locations))
        return len(locations)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("No se pudo confirmar la purga TTL de ubicaciones")
        raise LocationOperationError("location_purge_failed") from exc
    finally:
        if owns_session:
            db.close()
