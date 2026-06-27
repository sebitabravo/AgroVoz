"""Tests para los endpoints admin de ODEPA (T5.2).

Cubre:
- Auth: 401 sin key, 401 key inválido, 200 con key.
- /status: sin datos (ceros), con datos (totales + fecha más reciente).
- /products: lista de productos disponibles.
- /sync: dispara sync mockeada (evita descarga CSV real) y reporta resultado.
"""

import datetime
from collections.abc import Generator
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.odepa_price import OdepaPrice

_ADMIN_HEADERS = {"X-Admin-Key": settings.admin_api_key}


def _session_test_db(tmp_path: Path) -> Generator[Session, None, None]:
    db_path = tmp_path / "test_agrovoz.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _precio(
    db: Session,
    producto: str = "papa",
    mercado: str = "Lo Valledor",
    fecha: datetime.date | None = None,
) -> OdepaPrice:
    reg = OdepaPrice(
        producto=producto,
        mercado=mercado,
        precio_kg=Decimal("1200"),
        unidad="kg",
        fecha=fecha or datetime.date.today(),
        fuente="test",
    )
    db.add(reg)
    db.commit()
    return reg


# ── Auth ───────────────────────────────────────────────────────────


class TestOdepaAdminAuth:
    async def test_sin_key_retorna_401(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/admin/odepa/status")
        assert resp.status_code == 401

    async def test_key_invalido_retorna_401(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/admin/odepa/status", headers={"X-Admin-Key": "x"}
        )
        assert resp.status_code == 401

    async def test_key_valido_retorna_200(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/admin/odepa/status", headers=_ADMIN_HEADERS
        )
        assert resp.status_code == 200


# ── /status ────────────────────────────────────────────────────────


class TestOdepaStatus:
    async def test_sin_datos_retorna_ceros(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/admin/odepa/status", headers=_ADMIN_HEADERS
        )
        data = resp.json()
        assert data["total_filas"] == 0
        assert data["ultima_fecha"] is None
        assert data["productos"] == 0
        assert data["mercados"] == 0

    async def test_con_datos_reporta_totales(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _precio(db, producto="papa", mercado="Lo Valledor", fecha=datetime.date(2026, 6, 23))
            _precio(db, producto="tomate", mercado="Vega Central", fecha=datetime.date(2026, 6, 24))

        resp = await client.get(
            "/api/v1/admin/odepa/status", headers=_ADMIN_HEADERS
        )
        data = resp.json()
        assert data["total_filas"] == 2
        assert data["productos"] == 2
        assert data["mercados"] == 2
        assert data["ultima_fecha"] == "2026-06-24"


# ── /products ──────────────────────────────────────────────────────


class TestOdepaProducts:
    async def test_lista_productos_ordenados(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _precio(db, producto="tomate")
            _precio(db, producto="papa")
        resp = await client.get(
            "/api/v1/admin/odepa/products", headers=_ADMIN_HEADERS
        )
        assert resp.json() == ["papa", "tomate"]


# ── /sync ──────────────────────────────────────────────────────────


class TestOdepaSync:
    async def test_sync_reporta_resultado(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mockea sync_odepa para evitar descarga CSV real en tests."""
        async def _fake_sync() -> SimpleNamespace:
            return SimpleNamespace(insertados=5, actualizados=2, total=7)

        # sync() en odepa_admin usa sync_odepa importado en su namespace.
        import app.api.admin.odepa_admin as odepa_admin_module

        monkeypatch.setattr(odepa_admin_module, "sync_odepa", _fake_sync)

        resp = await client.post(
            "/api/v1/admin/odepa/sync", headers=_ADMIN_HEADERS
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["insertados"] == 5
        assert data["actualizados"] == 2
        assert data["total"] == 7
