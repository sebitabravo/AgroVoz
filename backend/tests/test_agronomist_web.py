"""Tests del shell web de la console de agrónomos (C4)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config import settings


@pytest.fixture(autouse=True)
def _enable_agronomist_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agronomist_console_enabled", True)


async def test_shell_html_se_sirve_para_cualquier_token(client: AsyncClient) -> None:
    """El shell es el mismo HTML para cualquier token: la validación real es en la API."""
    resp = await client.get("/agronomo/un-token-cualquiera")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "AgroVoz" in resp.text


async def test_shell_html_bloqueado_si_gate_apagado(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "agronomist_console_enabled", False)

    resp = await client.get("/agronomo/un-token-cualquiera")

    assert resp.status_code == 503
