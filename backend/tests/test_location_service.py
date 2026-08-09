"""Pruebas de ubicación GPS compartida, consentimiento, TTL y clima."""

import datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.phone_hash import hash_phone
from app.models.user_prefs import UserPrefs
from app.services.location_service import (
    LocationConsentError,
    UserLocation,
    clear_user_location,
    get_user_location,
    purge_expired_locations,
    save_user_location,
)


def _consentir_ubicacion(db, phone_hash: str) -> None:  # type: ignore[no-untyped-def]
    """Prepara el opt-in que en producción entrega el admin durante onboarding."""
    db.add(UserPrefs(phone_hash=phone_hash, location_consent=True))
    db.commit()


def test_guardar_ubicacion_consentida_es_recuperable(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """El pin requiere prefs existentes con consentimiento explícito."""
    phone_hash = "a" * 64
    monkeypatch.setattr(settings, "location_sharing_enabled", True)
    _consentir_ubicacion(db, phone_hash)

    saved = save_user_location(phone_hash, -38.2412, -72.6911)
    loaded = get_user_location(phone_hash)

    assert saved == UserLocation(lat=-38.2412, lng=-72.6911)
    assert loaded == saved
    prefs = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
    assert prefs is not None
    assert prefs.comuna is None
    assert prefs.location_consent is True
    assert prefs.location_updated_at is not None


def test_guardar_ubicacion_actualiza_pin_anterior(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Compartir otro pin reemplaza el anterior sin crear otra identidad."""
    phone_hash = "b" * 64
    monkeypatch.setattr(settings, "location_sharing_enabled", True)
    _consentir_ubicacion(db, phone_hash)
    save_user_location(phone_hash, -38.2, -72.6)
    save_user_location(phone_hash, -33.45, -70.65)

    prefs = db.scalars(select(UserPrefs)).all()
    assert len(prefs) == 1
    assert prefs[0].lat == pytest.approx(-33.45)
    assert prefs[0].lng == pytest.approx(-70.65)


def test_revocar_ubicacion_borra_coordenadas_y_conserva_preferencias(db) -> None:  # type: ignore[no-untyped-def]
    """La revocación elimina lat/lng sin destruir comuna u onboarding."""
    phone_hash = "d" * 64
    prefs = UserPrefs(
        phone_hash=phone_hash,
        comuna="Traiguén",
        lat=-38.2,
        lng=-72.6,
        location_consent=True,
        location_updated_at=datetime.datetime(2026, 7, 1),
    )
    db.add(prefs)
    db.commit()

    assert clear_user_location(phone_hash) is True
    db.expire_all()

    stored = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
    assert stored is not None
    assert stored.lat is None
    assert stored.lng is None
    assert stored.comuna == "Traiguén"
    assert get_user_location(phone_hash) is None


@pytest.mark.asyncio
async def test_orden_whatsapp_revoca_ubicacion_guardada(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Texto y audio comparten el fast-path explícito de revocación."""
    from app.services.pipeline_service import AgroVozPipeline

    phone_hash = "e" * 64
    monkeypatch.setattr(settings, "location_sharing_enabled", True)
    _consentir_ubicacion(db, phone_hash)
    save_user_location(phone_hash, -38.2, -72.6)
    origin = [""]

    response, intent = await AgroVozPipeline._generate_response(
        "Por favor, borra mi ubicación",
        phone_hash,
        origin,
    )

    assert response == "Listo. Eliminé la ubicación GPS guardada de tu parcela."
    assert intent == "clima"
    assert origin == ["ubicacion_borrada"]
    assert get_user_location(phone_hash) is None


@pytest.mark.parametrize(
    ("lat", "lng"),
    [(91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0)],
)
def test_guardar_ubicacion_rechaza_rangos_invalidos(
    db,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
    lat: float,
    lng: float,
) -> None:
    """Los límites WGS84 se validan antes de persistir datos."""
    monkeypatch.setattr(settings, "location_sharing_enabled", True)
    _consentir_ubicacion(db, "c" * 64)
    with pytest.raises(ValueError):
        save_user_location("c" * 64, lat, lng)


def test_guardar_ubicacion_bloquea_sin_consentimiento(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Sin location_consent el pin no se persiste aunque el gate esté activo."""
    phone_hash = "f" * 64
    monkeypatch.setattr(settings, "location_sharing_enabled", True)
    db.add(UserPrefs(phone_hash=phone_hash, location_consent=False))
    db.commit()

    with pytest.raises(LocationConsentError):
        save_user_location(phone_hash, -38.2, -72.6)
    assert get_user_location(phone_hash) is None


def test_guardar_ubicacion_bloquea_con_gate_apagado(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """El gate apagado bloquea nuevas escrituras incluso con opt-in."""
    phone_hash = "1" * 64
    _consentir_ubicacion(db, phone_hash)
    monkeypatch.setattr(settings, "location_sharing_enabled", False)

    with pytest.raises(LocationConsentError):
        save_user_location(phone_hash, -38.2, -72.6)


def test_purge_expired_locations_limpia_pin_y_conserva_fila(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """La purga elimina solo coordenadas vencidas y deja preferencias intactas."""
    monkeypatch.setattr(settings, "location_retention_days", 180)
    old = UserPrefs(
        phone_hash="2" * 64,
        comuna="Traiguén",
        lat=-38.2,
        lng=-72.6,
        location_consent=True,
        location_updated_at=datetime.datetime(2025, 1, 1),
    )
    fresh = UserPrefs(
        phone_hash="3" * 64,
        comuna="Temuco",
        lat=-38.7,
        lng=-72.6,
        location_consent=True,
        location_updated_at=datetime.datetime(2026, 7, 1),
    )
    db.add_all([old, fresh])
    db.commit()

    purged = purge_expired_locations(now=datetime.datetime(2026, 8, 1))

    assert purged == 1
    db.expire_all()
    stored_old = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == "2" * 64))
    stored_fresh = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == "3" * 64))
    assert stored_old is not None and stored_old.lat is None and stored_old.lng is None
    assert stored_old.comuna == "Traiguén"
    assert stored_fresh is not None and stored_fresh.lat == pytest.approx(-38.7)


@pytest.mark.asyncio
async def test_procesar_ubicacion_guarda_y_envia_pronostico(
    db,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El flujo de WhatsApp persiste el pin y confirma el pronóstico exacto."""
    from app.services.audio_service import AudioService
    from app.services.openwa_service import OpenWAService

    chat_id = "56912345678@c.us"
    phone_hash = hash_phone(chat_id, settings.phone_hash_pepper)
    monkeypatch.setattr(settings, "location_sharing_enabled", True)
    _consentir_ubicacion(db, phone_hash)
    sent: list[str] = []

    async def fake_send_text(_self: OpenWAService, _target: str, message: str) -> dict[str, object]:
        sent.append(message)
        return {"status": "sent"}

    monkeypatch.setattr(OpenWAService, "send_text", fake_send_text)
    monkeypatch.setattr(OpenWAService, "send_typing_indicator", AsyncMock())

    async def fake_forecast(
        comuna: str | None = None,
        dias: int = 2,
        phone_hash: str | None = None,
    ) -> str:
        assert comuna is None
        assert dias == 2
        assert phone_hash is not None
        return "Mañana en tu parcela: sin lluvia. Según OpenMeteo."

    monkeypatch.setattr("app.services.weather_service.get_pronostico", fake_forecast)

    await AudioService().process_location(
        lat=-38.2412,
        lng=-72.6911,
        chat_id=chat_id,
        request_id="request-location-test",
    )

    prefs = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
    assert prefs is not None
    assert prefs.lat == pytest.approx(-38.2412)
    assert prefs.lng == pytest.approx(-72.6911)
    assert sent == ["Ubicación de tu parcela actualizada.\n\nMañana en tu parcela: sin lluvia. Según OpenMeteo."]
