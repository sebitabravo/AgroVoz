"""Tests del shell web del panel del agricultor: HTML y Service Worker (C3)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config import settings


@pytest.fixture(autouse=True)
def _enable_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "farmer_panel_enabled", True)


async def test_service_worker_se_sirve_con_scope_panel(client: AsyncClient) -> None:
    """El SW debe vivir bajo /panel/ para poder controlar ese scope."""
    resp = await client.get("/panel/sw.js")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/javascript")
    assert resp.headers["cache-control"] == "no-cache"


async def test_service_worker_disponible_aunque_el_gate_este_apagado(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El script del SW no filtra datos: puede servirse incluso con el gate apagado."""
    monkeypatch.setattr(settings, "farmer_panel_enabled", False)

    resp = await client.get("/panel/sw.js")

    assert resp.status_code == 200


async def test_shell_html_se_sirve_para_cualquier_token(client: AsyncClient) -> None:
    """El shell es el mismo HTML para cualquier token: la validación real es en la API."""
    resp = await client.get("/panel/un-token-cualquiera")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "AgroVoz" in resp.text


async def test_shell_html_bloqueado_si_gate_apagado(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "farmer_panel_enabled", False)

    resp = await client.get("/panel/un-token-cualquiera")

    assert resp.status_code == 503


async def test_shell_incluye_grafico_de_precios_local(client: AsyncClient) -> None:
    """El shell incluye Chart.js local y el canvas para funcionar sin CDN."""
    resp = await client.get("/static/panel/index.html")

    assert resp.status_code == 200
    assert '<section class="card" aria-labelledby="precios-titulo">' in resp.text
    assert '<canvas id="grafico-precios"' in resp.text
    assert '<script src="/static/chart.umd.min.js"></script>' in resp.text
    assert "@media (max-width: 360px)" in resp.text


async def test_service_worker_cachea_chart_y_historial_de_precios(client: AsyncClient) -> None:
    """El SW guarda Chart.js y la respuesta /prices para consultas sin señal."""
    resp = await client.get("/panel/sw.js")

    assert resp.status_code == 200
    assert 'const CACHE_NAME = "agrovoz-panel-v3"' in resp.text
    assert '"/static/chart.umd.min.js"' in resp.text
    assert "/api/v1/panel/{token}/prices" in resp.text


async def test_service_worker_no_sirve_datos_despues_de_vencer_token(
    client: AsyncClient,
) -> None:
    """El fallback offline respeta el expiry firmado y purga 401/403."""
    resp = await client.get("/panel/sw.js")

    assert resp.status_code == 200
    assert "function tokenExpiryMs(pathname)" in resp.text
    assert "!isFreshPanelUrl(url)" in resp.text
    assert "purgeExpiredPanelEntries()" in resp.text
    assert "purgePanelToken(panelTokenFromPath(url.pathname))" in resp.text
    assert "response.status === 401 || response.status === 403" in resp.text
    assert "await cache.put(event.request, response.clone())" in resp.text
