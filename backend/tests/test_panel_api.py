"""Tests del endpoint GET /api/v1/panel/{token} (C3)."""

from __future__ import annotations

import datetime
from collections.abc import Generator
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.odepa_price import OdepaPrice
from app.models.parcela import Parcela
from app.models.user_prefs import UserPrefs
from app.services import panel_service
from app.services.panel_service import generate_panel_token

_PHONE_HASH = "a" * 64


def _session_test_db(tmp_path: Path) -> Generator[Session, None, None]:
    """Sesión SQLite del mismo archivo que usa el fixture ``client``."""
    db_path = tmp_path / "test_agrovoz.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    from app.core.database import Base
    from app.models import Consultation, OdepaPrice, Parcela, UserPrefs  # noqa: F401

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _configure_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "panel_link_secret", SecretStr("x" * 32))
    monkeypatch.setattr(settings, "panel_link_ttl_hours", 24)
    monkeypatch.setattr(settings, "farmer_panel_enabled", True)


async def test_gate_apagado_retorna_503(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "farmer_panel_enabled", False)
    token = generate_panel_token(_PHONE_HASH)

    resp = await client.get(f"/api/v1/panel/{token}")

    assert resp.status_code == 503


async def test_precios_gate_apagado_retorna_503(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "farmer_panel_enabled", False)
    token = generate_panel_token(_PHONE_HASH)

    resp = await client.get(f"/api/v1/panel/{token}/prices")

    assert resp.status_code == 503


async def test_token_invalido_retorna_401(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/panel/token-invalido")

    assert resp.status_code == 401


async def test_precios_token_invalido_retorna_401(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/panel/token-invalido/prices")

    assert resp.status_code == 401


async def test_token_vencido_retorna_401(client: AsyncClient) -> None:
    token = generate_panel_token(_PHONE_HASH, now=1_000_000_000)

    resp = await client.get(f"/api/v1/panel/{token}")

    assert resp.status_code == 401


async def test_sin_preferencias_registradas_retorna_404(client: AsyncClient) -> None:
    token = generate_panel_token(_PHONE_HASH)

    resp = await client.get(f"/api/v1/panel/{token}")

    assert resp.status_code == 404


async def test_token_valido_retorna_resumen(
    client: AsyncClient,
    tmp_path: Path,
) -> None:
    with next(_session_test_db(tmp_path)) as db:
        db.add(UserPrefs(phone_hash=_PHONE_HASH, comuna="Traiguén", cultivos='["papa"]'))
        db.commit()

    token = generate_panel_token(_PHONE_HASH)

    resp = await client.get(f"/api/v1/panel/{token}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["comuna"] == "Traiguén"
    assert data["cultivos"] == ["papa"]
    assert data["parcelas"] == []
    assert data["alertas"] == []


async def test_token_valido_retorna_historial_precios(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fecha_referencia = datetime.date(2026, 8, 3)
    monkeypatch.setattr(panel_service, "_panel_today", lambda: fecha_referencia)
    with next(_session_test_db(tmp_path)) as db:
        db.add(UserPrefs(phone_hash=_PHONE_HASH, comuna="Traiguén", cultivos='["papa"]'))
        db.add_all(
            [
                OdepaPrice(
                    producto="papa",
                    mercado="Vega Modelo de Temuco",
                    precio_kg=Decimal("1000"),
                    unidad="kg",
                    fecha=fecha_referencia - datetime.timedelta(days=27),
                ),
                OdepaPrice(
                    producto="papa",
                    mercado="Vega Modelo de Temuco",
                    precio_kg=Decimal("1200"),
                    unidad="kg",
                    fecha=fecha_referencia,
                ),
                OdepaPrice(
                    producto="papa",
                    mercado="Vega Modelo de Temuco",
                    precio_kg=Decimal("500"),
                    unidad="kg",
                    fecha=fecha_referencia - datetime.timedelta(days=28),
                ),
            ]
        )
        db.commit()

    token = generate_panel_token(_PHONE_HASH)

    resp = await client.get(f"/api/v1/panel/{token}/prices")

    assert resp.status_code == 200
    data = resp.json()
    assert data["desde"] == "2026-07-07"
    assert data["hasta"] == "2026-08-03"
    assert data["dias"] == 28
    assert data["cultivos"] == [
        {
            "cultivo": "papa",
            "mercado": "Vega Modelo de Temuco",
            "unidad": "kg",
            "fuente": "ODEPA",
            "precios": [
                {"fecha": "2026-07-07", "precio": 1000.0},
                {"fecha": "2026-08-03", "precio": 1200.0},
            ],
        }
    ]


async def test_incluye_parcelas_con_gate_y_consentimiento(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "parcela_tracking_enabled", True)
    with next(_session_test_db(tmp_path)) as db:
        import datetime

        db.add(
            UserPrefs(
                phone_hash=_PHONE_HASH,
                comuna="Traiguén",
                parcela_consent=True,
            )
        )
        db.add(
            Parcela(
                phone_hash=_PHONE_HASH,
                cultivo="papa",
                superficie_ha=2.5,
                comuna="traiguén",
                expires_at=datetime.datetime.now() + datetime.timedelta(days=300),
            )
        )
        db.commit()

    token = generate_panel_token(_PHONE_HASH)

    resp = await client.get(f"/api/v1/panel/{token}")

    assert resp.status_code == 200
    assert resp.json()["parcelas"] == [{"cultivo": "papa", "superficie_ha": 2.5, "comuna": "traiguén"}]


async def test_token_de_otro_sujeto_no_expone_datos_ajenos(
    client: AsyncClient,
    tmp_path: Path,
) -> None:
    """Un token válido solo puede leer los datos de SU propio phone_hash."""
    otro_hash = "b" * 64
    with next(_session_test_db(tmp_path)) as db:
        db.add(UserPrefs(phone_hash=_PHONE_HASH, comuna="Traiguén"))
        db.add(UserPrefs(phone_hash=otro_hash, comuna="Victoria"))
        db.commit()

    token = generate_panel_token(_PHONE_HASH)

    resp = await client.get(f"/api/v1/panel/{token}")

    assert resp.status_code == 200
    assert resp.json()["comuna"] == "Traiguén"
