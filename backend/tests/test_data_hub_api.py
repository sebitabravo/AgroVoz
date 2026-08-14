"""Pruebas de los endpoints públicos y admin del Data Hub."""

from httpx import AsyncClient

from app.core.config import settings
from app.services.data_hub_remote import RemoteDataHubSyncResult


class TestDataHubPublicAPI:
    async def test_sources_expone_estado_sin_secretos(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/data/sources")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 10
        assert {row["key"] for row in data} >= {
            "odepa_precios_mayoristas",
            "ciren_ide_minagri",
        }
        assert all("admin_api_key" not in row for row in data)
        assert any(row["status"] == "not_connected" for row in data)

    async def test_search_devuelve_fuente_y_vigencia(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/data/search", params={"q": "oficina INDAP Temuco"})

        assert response.status_code == 200
        data = response.json()
        assert data["results"]
        assert data["results"][0]["source"]
        assert data["results"][0]["source_url"].startswith("https://")
        assert data["results"][0]["verified_on"]

    async def test_search_sin_match_falla_cerrado(self, client: AsyncClient) -> None:
        response = await client.get(
            "/api/v1/data/search",
            params={"q": "receta de cocina italiana"},
        )

        assert response.status_code == 200
        assert response.json()["results"] == []
        assert "No se encontró" in response.json()["message"]


class TestDataHubAdminAPI:
    async def test_status_requiere_key(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/admin/data-hub/status")

        assert response.status_code == 401

    async def test_sync_retorna_conteos_y_fuentes_no_conectadas(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            "/api/v1/admin/data-hub/sync",
            headers={"X-Admin-Key": settings.admin_api_key},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["sources_synced"] == 10
        assert data["facts_synced"] == 121
        assert "inia_agroclima" in data["not_connected_sources"]
        assert data["remote_sources_synced"] == 0
        assert data["remote_source_errors"] == []

    async def test_sync_remoto_expone_resultado_saneado(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        """La verificación remota es opt-in y no filtra mensajes externos."""
        monkeypatch.setattr(
            "app.api.admin.data_hub.sync_remote_data_hub",
            lambda _db: RemoteDataHubSyncResult(
                sources_synced=2,
                source_errors=("ciren_ide_minagri:http_status",),
            ),
        )

        response = await client.post(
            "/api/v1/admin/data-hub/sync?remote=true",
            headers={"X-Admin-Key": settings.admin_api_key},
        )

        assert response.status_code == 200
        assert response.json()["remote_sources_synced"] == 2
        assert response.json()["remote_source_errors"] == [
            "ciren_ide_minagri:http_status"
        ]
