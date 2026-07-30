"""Tests del endpoint GET /api/v1/agronomo/{token} (C4)."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.services.agronomist_console_service import generate_agronomist_token

_GROUP = "prodesal-traiguen-norte"


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
def _configure_agronomist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agronomist_link_secret", SecretStr("x" * 32))
    monkeypatch.setattr(settings, "agronomist_link_ttl_hours", 72)
    monkeypatch.setattr(settings, "agronomist_console_enabled", True)


async def test_gate_apagado_retorna_503(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agronomist_console_enabled", False)
    token = generate_agronomist_token(_GROUP)

    resp = await client.get(f"/api/v1/agronomo/{token}")

    assert resp.status_code == 503


async def test_token_invalido_retorna_401(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/agronomo/token-invalido")

    assert resp.status_code == 401


async def test_token_vencido_retorna_401(client: AsyncClient) -> None:
    token = generate_agronomist_token(_GROUP, now=1_000_000_000)

    resp = await client.get(f"/api/v1/agronomo/{token}")

    assert resp.status_code == 401


async def test_grupo_sin_contactos_retorna_404(client: AsyncClient) -> None:
    token = generate_agronomist_token(_GROUP)

    resp = await client.get(f"/api/v1/agronomo/{token}")

    assert resp.status_code == 404


async def test_token_valido_retorna_resumen_del_grupo(
    client: AsyncClient,
    tmp_path: Path,
) -> None:
    from app.models.user_prefs import UserPrefs

    with next(_session_test_db(tmp_path)) as db:
        db.add(
            UserPrefs(
                phone_hash="a" * 64,
                identity_type="prodesal_group",
                group_label=_GROUP,
                comuna="Traiguén",
                cultivos='["papa"]',
            )
        )
        db.add(
            UserPrefs(
                phone_hash="b" * 64,
                identity_type="prodesal_group",
                group_label=_GROUP,
                comuna="Lautaro",
            )
        )
        db.commit()

    token = generate_agronomist_token(_GROUP)

    resp = await client.get(f"/api/v1/agronomo/{token}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["group_label"] == _GROUP
    assert len(data["productores"]) == 2
    assert {p["comuna"] for p in data["productores"]} == {"Traiguén", "Lautaro"}


async def test_token_de_otro_grupo_no_expone_datos_ajenos(
    client: AsyncClient,
    tmp_path: Path,
) -> None:
    from app.models.user_prefs import UserPrefs

    with next(_session_test_db(tmp_path)) as db:
        db.add(
            UserPrefs(
                phone_hash="a" * 64,
                identity_type="prodesal_group",
                group_label=_GROUP,
                comuna="Traiguén",
            )
        )
        db.add(
            UserPrefs(
                phone_hash="b" * 64,
                identity_type="prodesal_group",
                group_label="prodesal-otro-grupo",
                comuna="Victoria",
            )
        )
        db.commit()

    token = generate_agronomist_token(_GROUP)

    resp = await client.get(f"/api/v1/agronomo/{token}")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["productores"]) == 1
    assert data["productores"][0]["comuna"] == "Traiguén"


async def test_resumen_nunca_expone_phone_hash(
    client: AsyncClient,
    tmp_path: Path,
) -> None:
    from app.models.user_prefs import UserPrefs

    with next(_session_test_db(tmp_path)) as db:
        db.add(
            UserPrefs(
                phone_hash="a" * 64,
                identity_type="prodesal_group",
                group_label=_GROUP,
                comuna="Traiguén",
            )
        )
        db.commit()

    token = generate_agronomist_token(_GROUP)

    resp = await client.get(f"/api/v1/agronomo/{token}")

    assert resp.status_code == 200
    assert "phone_hash" not in resp.text
    assert "a" * 64 not in resp.text
