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
    assert 'id="identificar-plaga"' in resp.text
    assert "camera=(self)" in resp.headers["permissions-policy"]


async def test_cliente_pwa_captura_y_envia_imagen_de_la_camara(client: AsyncClient) -> None:
    """El shell incluye el contrato de captura para la cámara trasera móvil."""
    resp = await client.get("/static/panel/app.js")

    assert resp.status_code == 200
    assert "getUserMedia" in resp.text
    assert 'facingMode: "environment"' in resp.text
    assert 'fetch("/api/v1/vision/identify?token=" + encodeURIComponent(token)' in resp.text
    assert 'datos.append("image", blob, "captura.jpg")' in resp.text
    assert "camera=()" in resp.headers["permissions-policy"]


async def test_shell_html_bloqueado_si_gate_apagado(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "farmer_panel_enabled", False)

    resp = await client.get("/panel/un-token-cualquiera")

    assert resp.status_code == 503
