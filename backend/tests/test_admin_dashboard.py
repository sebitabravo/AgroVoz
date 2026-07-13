"""Tests del dashboard admin HTML (SSR Jinja2, T5.3).

Cubre:
- Login: GET formulario, POST key correcto (setea cookie + redirect),
  POST key incorrecto (redirect con error).
- Páginas protegidas: dashboard, métricas, odepa, monitor, actividad
  renderizan 200 con cookie válida.
- Partials HTMX: /admin/monitor/refresh, /admin/odepa/sync (mockeado).
- Botones de acción: reload-llm, clear-weather-cache, wa-check, clear-audio-temp.
- Logout: borra cookie + redirect.
"""

from contextlib import suppress
from decimal import Decimal
from types import SimpleNamespace
from typing import ClassVar

import pytest
from httpx import AsyncClient

from app.admin.auth import COOKIE_NAME, create_session_cookie
from app.core.config import settings


def _autenticar(client: AsyncClient) -> None:
    """Setea cookie de sesión admin válida en el client.

    Sin domain: httpx envía la cookie a cualquier host del client. Usar
    domain='testserver' no matchea porque cookiejar rechaza TLDs no válidos.
    """
    client.cookies.set(COOKIE_NAME, create_session_cookie())


# ── Login ──────────────────────────────────────────────────────────


class TestLogin:
    async def test_get_login_renderiza_formulario(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/login", follow_redirects=False)
        assert resp.status_code == 200
        body = resp.text
        assert "admin_key" in body  # campo del form
        assert "AgroVoz" in body  # brand

    async def test_post_login_correcto_setea_cookie_y_redirige(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/admin/login",
            data={"admin_key": settings.admin_api_key},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/"
        set_cookie = resp.headers.get("set-cookie", "")
        assert COOKIE_NAME in set_cookie

    async def test_post_login_incorrecto_redirige_con_error(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/admin/login",
            data={"admin_key": "incorrecto"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=1" in resp.headers["location"]
        # No setea cookie de sesión en fracaso.
        assert COOKIE_NAME not in resp.headers.get("set-cookie", "")


# ── Páginas protegidas ────────────────────────────────────────────


class TestPaginasProtegidas:
    """Cada tab del dashboard renderiza con cookie válida."""

    @pytest.mark.parametrize(
        "path",
        [
            "/admin/",
            "/admin/metrics",
            "/admin/odepa",
            "/admin/alerts",
            "/admin/monitor",
            "/admin/activity",
        ],
    )
    async def test_pagina_renderiza_con_cookie(self, client: AsyncClient, path: str) -> None:
        _autenticar(client)
        resp = await client.get(path, follow_redirects=False)
        assert resp.status_code == 200
        assert "AgroVoz" in resp.text

    async def test_dashboard_sin_cookie_redirige(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/login"


# ── Partials HTMX ──────────────────────────────────────────────────


class TestPartialsHtmx:
    async def test_monitor_refresh_retorna_partial(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """Evita I/O real: mockea el snapshot con servicios estables."""
        from app.services import monitor_service

        async def _fake_snapshot() -> SimpleNamespace:
            return SimpleNamespace(
                started_at=__import__("datetime").datetime.now(),
                uptime_seconds=42.0,
                system=SimpleNamespace(
                    cpu_percent=10.0,
                    ram_percent=50.0,
                    ram_used_mb=8000,
                    ram_total_mb=16000,
                    disk_percent=60.0,
                    disk_used_gb=80,
                    disk_total_gb=160,
                ),
                services=[],
                queue_depth=0,
            )

        monkeypatch.setattr(monitor_service, "get_monitor_snapshot", _fake_snapshot)
        _autenticar(client)
        resp = await client.get("/admin/monitor/refresh", follow_redirects=False)
        assert resp.status_code == 200
        assert 'id="monitor-snapshot"' in resp.text
        # El wrapper HTMX debe autopollear cada 30s.
        assert "every 30s" in resp.text

    async def test_odepa_sync_retorna_partial_con_flag(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mockea sync_odepa en el módulo fuente (admin.py importa localmente)."""

        async def _fake_sync() -> SimpleNamespace:
            return SimpleNamespace(insertados=3, actualizados=1, total=4)

        monkeypatch.setattr("app.services.odepa_service.sync_odepa", _fake_sync)
        _autenticar(client)
        resp = await client.post("/admin/odepa/sync", follow_redirects=False)
        assert resp.status_code == 200
        assert 'id="odepa-status"' in resp.text


# ── Botones de acción (monitor) ──────────────────────────────────────


class TestMonitorActions:
    """Endpoints POST de acciones operativas del monitor."""

    async def _fake_snapshot(self) -> SimpleNamespace:
        import datetime as _dt

        return SimpleNamespace(
            started_at=_dt.datetime.now(tz=_dt.UTC),
            uptime_seconds=42.0,
            system=SimpleNamespace(
                cpu_percent=10.0,
                ram_percent=50.0,
                ram_used_mb=8000,
                ram_total_mb=16000,
                disk_percent=60.0,
                disk_used_gb=80,
                disk_total_gb=160,
            ),
            services=[],
            queue_depth=0,
        )

    _ACTION_PATHS: ClassVar[list[str]] = [
        "/admin/monitor/reload-llm",
        "/admin/monitor/clear-weather-cache",
        "/admin/monitor/wa-check",
        "/admin/monitor/clear-audio-temp",
    ]

    @pytest.mark.parametrize("path", _ACTION_PATHS)
    async def test_accion_retorna_partial_con_snapshot(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        path: str,
    ) -> None:
        from app.services import monitor_service

        monkeypatch.setattr(monitor_service, "get_monitor_snapshot", self._fake_snapshot)
        _autenticar(client)
        resp = await client.post(path, follow_redirects=False)
        assert resp.status_code == 200
        assert 'id="monitor-snapshot"' in resp.text

    @pytest.mark.parametrize("path", _ACTION_PATHS)
    async def test_accion_sin_cookie_redirige(
        self,
        client: AsyncClient,
        path: str,
    ) -> None:
        resp = await client.post(path, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/login"


# ── Alertas ───────────────────────────────────────────────────────


class TestAlertsAdmin:
    """Vista y cancelacion de alertas proactivas desde el admin."""

    async def test_alerts_page_lista_alertas(self, client: AsyncClient) -> None:
        from app.core.database import get_db as original_get_db
        from app.main import app
        from app.models.alert import Alert

        session_gen = app.dependency_overrides[original_get_db]()
        session = next(session_gen)
        try:
            session.add(
                Alert(
                    phone_hash="a" * 64,
                    wa_chat_id="56912345678@c.us",
                    tipo="precio",
                    producto="papa",
                    condicion=">",
                    umbral=Decimal("10000"),
                    activa=True,
                )
            )
            session.commit()
        finally:
            session.close()
            with suppress(StopIteration):
                next(session_gen)

        _autenticar(client)
        resp = await client.get("/admin/alerts", follow_redirects=False)
        assert resp.status_code == 200
        assert "Alertas proactivas" in resp.text
        assert "papa" in resp.text

    async def test_cancel_alert_desactiva_y_redirige(self, client: AsyncClient) -> None:
        from app.core.database import get_db as original_get_db
        from app.main import app
        from app.models.alert import Alert

        # Usar la misma sesion que el endpoint (engine temporal del client).
        session_gen = app.dependency_overrides[original_get_db]()
        session = next(session_gen)
        try:
            alerta = Alert(
                phone_hash="a" * 64,
                wa_chat_id="56912345678@c.us",
                tipo="precio",
                producto="papa",
                condicion=">",
                umbral=Decimal("10000"),
                activa=True,
            )
            session.add(alerta)
            session.commit()
            alerta_id = alerta.id
        finally:
            session.close()
            with suppress(StopIteration):
                next(session_gen)

        _autenticar(client)
        resp = await client.post(
            f"/admin/alerts/{alerta_id}/cancel",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/alerts"

        session_gen = app.dependency_overrides[original_get_db]()
        session = next(session_gen)
        try:
            alerta_cancelada = session.get(Alert, alerta_id)
            assert alerta_cancelada is not None
            assert alerta_cancelada.activa is False
        finally:
            session.close()
            with suppress(StopIteration):
                next(session_gen)


# ── Logout ────────────────────────────────────────────────────────


class TestLogout:
    async def test_logout_borra_cookie_y_redirige(self, client: AsyncClient) -> None:
        _autenticar(client)  # logout requiere sesión activa (ruta protegida).
        resp = await client.post("/admin/logout", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/login"
        set_cookie = resp.headers.get("set-cookie", "")
        # delete_cookie envía Max-Age=0 / expires en el pasado.
        assert COOKIE_NAME in set_cookie
        assert "Max-Age=0" in set_cookie or "1970" in set_cookie or "expires=" in set_cookie.lower()
