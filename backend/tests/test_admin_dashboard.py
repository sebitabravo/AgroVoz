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

import json
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

    async def test_actividad_explica_contenido_redactado(
        self,
        client: AsyncClient,
    ) -> None:
        """El log conserva métricas útiles sin dejar una celda ambigua."""
        from app.core.database import get_db as original_get_db
        from app.main import app
        from app.models.consultation import Consultation

        session_gen = app.dependency_overrides[original_get_db]()
        session = next(session_gen)
        try:
            session.add(
                Consultation(
                    phone_hash="a" * 64,
                    intent="precio",
                    producto="papa",
                    query_text="",
                    response_text="",
                    delivery_status="delivered",
                )
            )
            session.commit()
        finally:
            session.close()
            with suppress(StopIteration):
                next(session_gen)

        _autenticar(client)
        response = await client.get("/admin/activity")

        assert response.status_code == 200
        assert "Contenido no conservado" in response.text

    async def test_credito_aparece_en_contexto_donut_y_barras(
        self,
        client: AsyncClient,
    ) -> None:
        """Crédito usa conteo propio y el total operativo como denominador."""
        from app.core.database import get_db as original_get_db
        from app.main import app
        from app.models.consultation import Consultation

        session_gen = app.dependency_overrides[original_get_db]()
        session = next(session_gen)
        try:
            intents = ("precio", "clima", "credito", "credito", "desconocido", "corpus")
            for index, intent in enumerate(intents):
                session.add(
                    Consultation(
                        phone_hash=f"{index:064x}",
                        intent=intent,
                        query_text=f"consulta {intent}",
                        response_text="respuesta",
                        audio_duration_ms=0,
                        latency_ms=10,
                        delivery_status="delivered",
                    )
                )
            # Esta fila sintética no puede alterar conteos ni porcentajes.
            session.add(
                Consultation(
                    phone_hash="f" * 64,
                    intent="credito",
                    query_text="consulta sintética",
                    response_text="respuesta",
                    audio_duration_ms=0,
                    latency_ms=1,
                    delivery_status="delivered",
                    is_test=True,
                )
            )
            session.commit()
        finally:
            session.close()
            with suppress(StopIteration):
                next(session_gen)

        _autenticar(client)
        metrics_response = await client.get("/admin/metrics?days=1")
        assert metrics_response.status_code == 200

        marker = '<script type="application/json" id="metrics-data">'
        json_start = metrics_response.text.index(marker) + len(marker)
        json_end = metrics_response.text.index("</script>", json_start)
        chart_data = json.loads(metrics_response.text[json_start:json_end])
        assert chart_data["intents"] == {
            "precio": 1,
            "clima": 1,
            "credito": 2,
            "desconocido": 1,
            "total": 5,
        }

        compact_metrics = " ".join(metrics_response.text.split())
        assert "Crédito" in compact_metrics
        assert "2 · 40%" in compact_metrics
        assert "#6d28d9" in metrics_response.text

        dashboard_response = await client.get("/admin/")
        assert dashboard_response.status_code == 200
        assert "Crédito" in dashboard_response.text
        assert "width:40.0%;background:#6d28d9" in dashboard_response.text
        assert 'class="badge badge-credito"' in dashboard_response.text


# ── Métricas grupales PRODESAL ────────────────────────────────────


class TestMetricasGrupales:
    """Tabla SSR agregada, accesible y sin datos de integrantes."""

    async def test_renderiza_tabla_semantica_sin_pii(
        self,
        client: AsyncClient,
    ) -> None:
        """Grupo e individuo comparten DB, pero solo sale el agregado grupal."""
        from app.core.database import get_db as original_get_db
        from app.main import app
        from app.models.consultation import Consultation
        from app.models.user_prefs import UserPrefs

        group_hash = "a1" * 32
        individual_hash = "b2" * 32
        group_query = "consulta grupal confidencial"
        group_response = "respuesta grupal confidencial"
        individual_query = "consulta individual confidencial"
        session_gen = app.dependency_overrides[original_get_db]()
        session = next(session_gen)
        try:
            session.add_all(
                [
                    UserPrefs(
                        phone_hash=group_hash,
                        identity_type="prodesal_group",
                        group_label="PRODESAL-TRG-01",
                        comuna="Traiguén",
                        localidad="Quino",
                    ),
                    UserPrefs(
                        phone_hash=individual_hash,
                        identity_type="individual",
                        comuna="Comuna individual confidencial",
                    ),
                ]
            )
            for status, latency_ms in (
                ("delivered", 1_000),
                ("failed", 3_000),
                ("pending", 5_000),
            ):
                session.add(
                    Consultation(
                        phone_hash=group_hash,
                        intent="precio",
                        query_text=group_query,
                        response_text=group_response,
                        latency_ms=latency_ms,
                        delivery_status=status,
                    )
                )
            session.add(
                Consultation(
                    phone_hash=individual_hash,
                    intent="precio",
                    query_text=individual_query,
                    response_text="respuesta individual confidencial",
                    latency_ms=99_000,
                    delivery_status="delivered",
                )
            )
            session.commit()
        finally:
            session.close()
            with suppress(StopIteration):
                next(session_gen)

        _autenticar(client)
        response = await client.get("/admin/metrics?days=7")
        compact = " ".join(response.text.split())

        assert response.status_code == 200
        assert '<section aria-labelledby="group-metrics-title"' in response.text
        assert '<table id="group-metrics-table"' in response.text
        assert "<caption" in response.text
        for header in (
            "Código de grupo",
            "Comuna",
            "Localidad",
            "Consultas",
            "Entregadas",
            "Fallidas",
            "Pendientes",
            "Tasa entrega",
            "Latencia media",
            "Última actividad",
        ):
            assert header in response.text
        assert response.text.count('scope="col"') >= 10
        assert 'scope="row"' in response.text

        row_start = compact.index("PRODESAL-TRG-01")
        row_end = compact.index("</tr>", row_start)
        group_row = compact[row_start:row_end]
        assert "Traiguén" in group_row
        assert "Quino" in group_row
        assert ">3</td>" in group_row
        assert ">1</td>" in group_row
        assert "50.0%" in group_row
        assert "3000 ms" in group_row

        for forbidden in (
            group_hash,
            individual_hash,
            group_query,
            group_response,
            individual_query,
            "respuesta individual confidencial",
            "Comuna individual confidencial",
        ):
            assert forbidden not in response.text

    async def test_sin_grupos_muestra_estado_vacio(
        self,
        client: AsyncClient,
    ) -> None:
        """La ausencia de identidades colectivas se explica sin tabla vacía."""
        _autenticar(client)
        response = await client.get("/admin/metrics?days=7")

        assert response.status_code == 200
        assert "Sin grupos PRODESAL registrados para mostrar." in response.text
        assert 'id="group-metrics-table"' not in response.text


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

    async def test_monitor_distingue_lazy_de_caido(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """Un servicio lazy (sin cargar aún) no debe mostrarse como 'Caído'.

        Regresión: Whisper/LLM/TTS con lazy loading devuelven ok=False antes
        de la primera consulta, igual que un error real. El template debe
        distinguirlos por el detail ('lazy (sin cargar)') para no alarmar
        al equipo con un falso "Caído".
        """
        from app.services import monitor_service
        from app.services.monitor_service import ServiceCheck

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
                services=[
                    ServiceCheck("Whisper STT", False, "small · lazy (sin cargar)"),
                    ServiceCheck("SQLite", True, "agrovoz.db · ok"),
                    ServiceCheck("Open-WA", False, "error: connection refused"),
                ],
                queue_depth=0,
            )

        monkeypatch.setattr(monitor_service, "get_monitor_snapshot", _fake_snapshot)
        _autenticar(client)
        resp = await client.get("/admin/monitor/refresh", follow_redirects=False)
        assert resp.status_code == 200
        assert "En espera" in resp.text
        assert "Operativo" in resp.text
        assert "Caído" in resp.text
        # El bloque de Whisper (lazy) debe decir "En espera", no "Caído".
        inicio = resp.text.index("small · lazy (sin cargar)")
        bloque_whisper = resp.text[inicio : inicio + 200]
        assert "En espera" in bloque_whisper
        assert "Caído" not in bloque_whisper

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
