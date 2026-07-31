"""Tests del endpoint admin POST /admin/agronomos/links (C4).

Cubre:
- Auth: 401 sin key, 401 key invalido.
- Generacion: 200 con link firmado, 422 group_label invalido, 503 gate apagado.
- El link generado es verificable por agronomist_console_service.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import settings
from app.services.agronomist_console_service import verify_agronomist_token

_ADMIN_HEADERS = {"X-Admin-Key": settings.admin_api_key}
_GROUP = "prodesal-traiguen-norte"


@pytest.fixture(autouse=True)
def _configure_agronomist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agronomist_link_secret", SecretStr("x" * 32))
    monkeypatch.setattr(settings, "agronomist_link_ttl_hours", 72)
    monkeypatch.setattr(settings, "agronomist_console_enabled", True)
    monkeypatch.setattr(settings, "agronomist_base_url", "https://app.agrovoz.cl/agronomo")


class TestAgronomistAdminAuth:
    """Autenticación con X-Admin-Key."""

    async def test_sin_key_retorna_401(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/admin/agronomos/links", json={"group_label": _GROUP})
        assert resp.status_code == 401

    async def test_key_invalido_retorna_401(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/admin/agronomos/links",
            json={"group_label": _GROUP},
            headers={"X-Admin-Key": "x"},
        )
        assert resp.status_code == 401


class TestCreateAgronomistLink:
    """Generación del link firmado."""

    async def test_gate_apagado_retorna_503(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "agronomist_console_enabled", False)

        resp = await client.post(
            "/api/v1/admin/agronomos/links",
            json={"group_label": _GROUP},
            headers=_ADMIN_HEADERS,
        )

        assert resp.status_code == 503

    async def test_group_label_invalido_retorna_422(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/admin/agronomos/links",
            json={"group_label": "traiguen-norte"},
            headers=_ADMIN_HEADERS,
        )

        assert resp.status_code == 422

    async def test_link_valido_incluye_url_y_ttl(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/admin/agronomos/links",
            json={"group_label": _GROUP},
            headers=_ADMIN_HEADERS,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["link"].startswith("https://app.agrovoz.cl/agronomo/")
        assert data["expires_in_hours"] == 72

    async def test_link_generado_es_verificable(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/admin/agronomos/links",
            json={"group_label": _GROUP},
            headers=_ADMIN_HEADERS,
        )

        token = resp.json()["link"].rsplit("/", 1)[-1]
        assert verify_agronomist_token(token) == _GROUP

    async def test_clave_insegura_retorna_500(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "agronomist_link_secret", SecretStr("corta"))

        resp = await client.post(
            "/api/v1/admin/agronomos/links",
            json={"group_label": _GROUP},
            headers=_ADMIN_HEADERS,
        )

        assert resp.status_code == 500
