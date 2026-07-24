"""Tests para MCP server (issue #193)."""

import pytest
from fastapi.testclient import TestClient

from app.mcp.router import _TOOL_SCOPES, _check_scope, router


class TestScopeCheck:
    """Verifica la lógica de scopes."""

    def test_read_scope_suficiente_para_read_tool(self) -> None:
        assert _check_scope("get_metrics", "read") is True

    def test_read_scope_insuficiente_para_write_tool(self) -> None:
        assert _check_scope("sync_odepa", "read") is False

    def test_admin_write_suficiente_para_write_tool(self) -> None:
        assert _check_scope("sync_odepa", "admin:write") is True

    def test_tool_desconocida_requiere_admin(self) -> None:
        assert _check_scope("tool_inexistente", "read") is False


class TestMcpAuth:
    """Verifica autenticación del MCP server."""

    @pytest.fixture
    def client(self) -> TestClient:
        """Cliente de test HTTP."""
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_sin_api_key_retorna_401(self, client: TestClient) -> None:
        response = client.get("/mcp/tools/list")
        assert response.status_code == 401

    def test_con_api_key_lista_tools(self, client: TestClient) -> None:
        response = client.get(
            "/mcp/tools/list", headers={"X-MCP-Key": "test-key"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "tools" in data
        assert len(data["tools"]) == len(_TOOL_SCOPES)

    def test_tool_desconocida_retorna_400(self, client: TestClient) -> None:
        response = client.post(
            "/mcp/tools",
            json={"tool": "herramienta_falsa"},
            headers={"X-MCP-Key": "test-key"},
        )
        assert response.status_code == 400
