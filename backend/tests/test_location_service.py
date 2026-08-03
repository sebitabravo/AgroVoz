"""Pruebas de ubicación GPS compartida y su integración con clima."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.phone_hash import hash_phone
from app.models.user_prefs import UserPrefs
from app.services.location_service import UserLocation, get_user_location, save_user_location


def test_guardar_ubicacion_crea_preferencias_y_es_recuperable(db) -> None:  # type: ignore[no-untyped-def]
    """El pin crea user_prefs si el contacto todavía no tenía onboarding."""
    phone_hash = "a" * 64

    saved = save_user_location(phone_hash, -38.2412, -72.6911)
    loaded = get_user_location(phone_hash)

    assert saved == UserLocation(lat=-38.2412, lng=-72.6911)
    assert loaded == saved
    prefs = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
    assert prefs is not None
    assert prefs.comuna is None


def test_guardar_ubicacion_actualiza_pin_anterior(db) -> None:  # type: ignore[no-untyped-def]
    """Compartir otro pin reemplaza el anterior sin crear otra identidad."""
    phone_hash = "b" * 64
    save_user_location(phone_hash, -38.2, -72.6)
    save_user_location(phone_hash, -33.45, -70.65)

    prefs = db.scalars(select(UserPrefs)).all()
    assert len(prefs) == 1
    assert prefs[0].lat == pytest.approx(-33.45)
    assert prefs[0].lng == pytest.approx(-70.65)


@pytest.mark.parametrize(
    ("lat", "lng"),
    [(91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0)],
)
def test_guardar_ubicacion_rechaza_rangos_invalidos(
    db,  # type: ignore[no-untyped-def]
    lat: float,
    lng: float,
) -> None:
    """Los límites WGS84 se validan antes de persistir datos."""
    with pytest.raises(ValueError):
        save_user_location("c" * 64, lat, lng)


@pytest.mark.asyncio
async def test_procesar_ubicacion_guarda_y_envia_pronostico(
    db,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El flujo de WhatsApp persiste el pin y confirma el pronóstico exacto."""
    from app.services.audio_service import AudioService
    from app.services.openwa_service import OpenWAService

    chat_id = "56912345678@c.us"
    sent: list[str] = []

    async def fake_send_text(_self: OpenWAService, _target: str, message: str) -> dict[str, object]:
        sent.append(message)
        return {"status": "sent"}

    monkeypatch.setattr(OpenWAService, "send_text", fake_send_text)
    monkeypatch.setattr(OpenWAService, "send_typing_indicator", AsyncMock())

    async def fake_forecast(
        comuna: str,
        dias: int,
        phone_hash: str | None = None,
    ) -> str:
        assert comuna == "tu parcela"
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

    phone_hash = hash_phone(chat_id, settings.phone_hash_pepper)
    prefs = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
    assert prefs is not None
    assert prefs.lat == pytest.approx(-38.2412)
    assert prefs.lng == pytest.approx(-72.6911)
    assert sent == ["Ubicación de tu parcela actualizada.\n\nMañana en tu parcela: sin lluvia. Según OpenMeteo."]
