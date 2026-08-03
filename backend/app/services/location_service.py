"""Persistencia de la ubicación compartida por WhatsApp.

La identidad sigue siendo el ``phone_hash``; nunca se guarda el chat ID en
claro. El servicio es síncrono porque el proyecto usa SQLAlchemy síncrono y
los callers asíncronos lo ejecutan con ``asyncio.to_thread``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core import database
from app.core.phone_hash import validate_phone_hash
from app.models.user_prefs import UserPrefs


@dataclass(frozen=True, slots=True)
class UserLocation:
    """Coordenadas decimales guardadas para una identidad de WhatsApp."""

    lat: float
    lng: float


def _validate_coordinates(lat: float, lng: float) -> None:
    """Valida rangos WGS84 antes de tocar SQLite."""
    if not (-90.0 <= lat <= 90.0):
        raise ValueError("La latitud debe estar entre -90° y 90°")
    if not (-180.0 <= lng <= 180.0):
        raise ValueError("La longitud debe estar entre -180° y 180°")


def save_user_location(phone_hash: str, lat: float, lng: float) -> UserLocation:
    """Crea o actualiza la ubicación de una identidad seudonimizada.

    La ubicación compartida reemplaza la anterior para que siempre se consulte
    la parcela más recientemente elegida por el productor.
    """
    if not validate_phone_hash(phone_hash):
        raise ValueError("phone_hash inválido")
    _validate_coordinates(lat, lng)

    session = database.SessionLocal()
    try:
        prefs = session.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
        if prefs is None:
            prefs = UserPrefs(phone_hash=phone_hash)
            session.add(prefs)
        prefs.lat = lat
        prefs.lng = lng
        session.commit()
        return UserLocation(lat=lat, lng=lng)
    except IntegrityError:
        # Otro webhook pudo crear la fila entre el SELECT y el INSERT. Releer
        # y actualizar evita duplicar preferencias bajo concurrencia puntual.
        session.rollback()
        prefs = session.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
        if prefs is None:
            raise
        prefs.lat = lat
        prefs.lng = lng
        session.commit()
        return UserLocation(lat=lat, lng=lng)
    finally:
        session.close()


def get_user_location(phone_hash: str | None) -> UserLocation | None:
    """Obtiene coordenadas guardadas o ``None`` si aún no existen."""
    if phone_hash is None or not validate_phone_hash(phone_hash):
        return None

    session = database.SessionLocal()
    try:
        prefs = session.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
        if prefs is None or prefs.lat is None or prefs.lng is None:
            return None
        return UserLocation(lat=float(prefs.lat), lng=float(prefs.lng))
    finally:
        session.close()
