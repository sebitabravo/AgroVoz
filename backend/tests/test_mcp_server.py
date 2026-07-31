"""Tests para MCP server (issue #193)."""

import asyncio
from collections.abc import Iterator
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.rate_limiter import reset_mcp_rate_limiter
from app.main import RequestIDMiddleware, _mount_mcp_router, lifespan
from app.mcp.router import _TOOL_SCOPES, _check_scope, router
from app.models.consultation import Consultation
from app.schemas.mcp import McpToolRequest
from app.services import delivery_service, mcp_service
from app.services.metrics_service import get_dashboard_kpis


@pytest.fixture(autouse=True)
def _reset_mcp_limiter() -> Iterator[None]:
    """Evita que el bucket global MCP filtre estado entre pruebas."""
    reset_mcp_rate_limiter()
    yield
    reset_mcp_rate_limiter()


@pytest.fixture
def mcp_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[sessionmaker[Session]]:
    """Crea SQLite aislado y compartible con los threads del endpoint."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Consultation.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(mcp_service, "SessionLocal", factory)
    yield factory
    engine.dispose()


def _insert_consultation(
    factory: sessionmaker[Session],
    *,
    intent: str = "precio",
    producto: str | None = "papa",
    delivery_status: str = "delivered",
    delivery_error_code: str | None = None,
    query_text: str = "PII_QUERY_NUNCA_SALE",
    response_text: str = "PII_RESPONSE_NUNCA_SALE",
) -> int:
    """Inserta una consulta sintética y retorna su ID."""
    with factory() as db:
        consultation = Consultation(
            phone_hash="hash_secreto_nunca_sale",
            intent=intent,
            producto=producto,
            query_text=query_text,
            response_text=response_text,
            latency_ms=1200,
            whisper_ms=300,
            llm_ms=500,
            tts_ms=400,
            delivery_status=delivery_status,
            delivery_error_code=delivery_error_code,
            requires_review=delivery_status == "failed",
            is_test=False,
        )
        db.add(consultation)
        db.commit()
        db.refresh(consultation)
        return consultation.id


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


class TestMcpMountGate:
    """Verifica que el router administrativo respete su feature gate."""

    @pytest.mark.parametrize("path", ["/mcp/tools", "/mcp/tools/list"])
    def test_gate_apagado_no_expone_rutas(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        """Sin activación explícita, ambos endpoints responden 404."""
        monkeypatch.setattr(settings, "mcp_enabled", False)
        test_app = FastAPI()

        _mount_mcp_router(test_app)

        with TestClient(test_app) as client:
            assert client.get(path).status_code == 404

    def test_gate_activo_monta_las_rutas(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Claves fuertes y gate activo vuelven accesible el router MCP."""
        read_key = "read-" + "a7f93c2e" * 4
        monkeypatch.setattr(settings, "mcp_enabled", True)
        monkeypatch.setattr(settings, "mcp_api_key", read_key)
        monkeypatch.setattr(settings, "mcp_admin_key", "admin-" + "b7c92f1e" * 4)
        test_app = FastAPI()

        _mount_mcp_router(test_app)

        with TestClient(test_app) as client:
            list_response = client.get(
                "/mcp/tools/list",
                headers={"X-MCP-Key": read_key},
            )
            tools_response = client.post(
                "/mcp/tools",
                json={"tool": "herramienta_falsa"},
                headers={"X-MCP-Key": read_key},
            )
        assert list_response.status_code == 200
        assert tools_response.status_code == 400

    @pytest.mark.asyncio
    async def test_lifespan_revalida_settings_mutado_y_falla_antes_de_arrancar(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Una mutación insegura posterior al import no alcanza el arranque."""
        monkeypatch.setattr(settings, "mcp_enabled", True)
        monkeypatch.setattr(settings, "mcp_api_key", "clave-corta")
        monkeypatch.setattr(settings, "mcp_admin_key", "admin-" + "b7c92f1e" * 4)

        with pytest.raises(ValueError, match="MCP_API_KEY"):
            async with lifespan(FastAPI()):
                pytest.fail("El lifespan no debía alcanzar el cuerpo")


class TestMcpAuth:
    """Verifica autenticación del MCP server."""

    @pytest.fixture(autouse=True)
    def _setup_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Configura API keys para tests."""
        monkeypatch.setattr(settings, "mcp_api_key", "test-read-key")
        monkeypatch.setattr(settings, "mcp_admin_key", "test-admin-key")

    @pytest.fixture
    def client(self) -> TestClient:
        """Cliente de test HTTP."""
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_sin_api_key_retorna_401(self, client: TestClient) -> None:
        response = client.get("/mcp/tools/list")
        assert response.status_code == 401

    def test_key_invalida_retorna_401(self, client: TestClient) -> None:
        response = client.get(
            "/mcp/tools/list", headers={"X-MCP-Key": "wrong-key"}
        )
        assert response.status_code == 401

    def test_con_read_key_lista_tools(self, client: TestClient) -> None:
        response = client.get(
            "/mcp/tools/list", headers={"X-MCP-Key": "test-read-key"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "tools" in data
        assert len(data["tools"]) == len(_TOOL_SCOPES)

    def test_read_key_rechaza_sync_odepa(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Read key no puede ejecutar sync_odepa (admin:write)."""
        execute = AsyncMock()
        monkeypatch.setattr(mcp_service, "execute_tool", execute)

        response = client.post(
            "/mcp/tools",
            json={"tool": "sync_odepa"},
            headers={"X-MCP-Key": "test-read-key"},
        )

        assert response.status_code == 403
        execute.assert_not_awaited()

    def test_admin_key_ejecuta_sync_odepa(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Admin key puede ejecutar sync_odepa."""
        execute = AsyncMock(
            return_value={
                "status": "completed",
                "inserted": 2,
                "updated": 3,
                "total": 5,
            }
        )
        monkeypatch.setattr(mcp_service, "execute_tool", execute)

        response = client.post(
            "/mcp/tools",
            json={"tool": "sync_odepa"},
            headers={"X-MCP-Key": "test-admin-key"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "tool": "sync_odepa",
            "result": {
                "status": "completed",
                "inserted": 2,
                "updated": 3,
                "total": 5,
            },
        }
        execute.assert_awaited_once_with("sync_odepa", {})

    def test_tool_desconocida_retorna_400(self, client: TestClient) -> None:
        unknown_tool = "herramienta_falsa"
        response = client.post(
            "/mcp/tools",
            json={"tool": unknown_tool},
            headers={"X-MCP-Key": "test-read-key"},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == {
            "code": "MCP_TOOL_UNKNOWN",
            "message": "La tool solicitada no está disponible.",
        }
        assert unknown_tool not in response.text


class TestMcpPayloadValidation:
    """Valida que cuerpos malformados produzcan 4xx estables, nunca 500."""

    @pytest.fixture(autouse=True)
    def _setup_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "mcp_api_key", "test-read-key")
        monkeypatch.setattr(settings, "mcp_admin_key", "test-admin-key")

    @pytest.fixture
    def client(self) -> TestClient:
        test_app = FastAPI()
        test_app.include_router(router)
        return TestClient(test_app)

    def test_arguments_usa_diccionario_vacio_por_defecto(self) -> None:
        """Omitir argumentos crea un objeto nuevo y vacío por solicitud."""
        first = McpToolRequest(tool="get_metrics")
        second = McpToolRequest(tool="get_metrics")

        assert first.arguments == {}
        assert first.arguments is not second.arguments

    def test_json_invalido_retorna_422(self, client: TestClient) -> None:
        response = client.post(
            "/mcp/tools",
            content="{",
            headers={
                "Content-Type": "application/json",
                "X-MCP-Key": "test-read-key",
            },
        )

        assert response.status_code == 422

    def test_array_en_vez_de_objeto_retorna_422(self, client: TestClient) -> None:
        response = client.post(
            "/mcp/tools",
            json=[],
            headers={"X-MCP-Key": "test-read-key"},
        )

        assert response.status_code == 422

    def test_tool_faltante_retorna_422(self, client: TestClient) -> None:
        response = client.post(
            "/mcp/tools",
            json={"arguments": {}},
            headers={"X-MCP-Key": "test-read-key"},
        )

        assert response.status_code == 422

    @pytest.mark.parametrize("arguments", [[], "texto", None, 7])
    def test_arguments_no_objeto_retorna_422(
        self, client: TestClient, arguments: object
    ) -> None:
        response = client.post(
            "/mcp/tools",
            json={"tool": "get_metrics", "arguments": arguments},
            headers={"X-MCP-Key": "test-read-key"},
        )

        assert response.status_code == 422

    @pytest.mark.parametrize(
        "payload",
        [
            {"tool": ""},
            {"tool": "a" * 65},
            {"tool": "get-metrics"},
            {"tool": 123},
            {"tool": "get_metrics", "extra": True},
        ],
    )
    def test_tool_fuera_de_contrato_retorna_422(
        self, client: TestClient, payload: object
    ) -> None:
        response = client.post(
            "/mcp/tools",
            json=payload,
            headers={"X-MCP-Key": "test-read-key"},
        )

        assert response.status_code == 422


class TestMcpReadHandlers:
    """Verifica las tools MCP de lectura y su proyección sin PII."""

    @pytest.fixture(autouse=True)
    def _setup_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "mcp_api_key", "test-read-key")
        monkeypatch.setattr(settings, "mcp_admin_key", "test-admin-key")

    @pytest.fixture
    def client(self) -> TestClient:
        test_app = FastAPI()
        test_app.include_router(router)
        return TestClient(test_app)

    def test_router_delega_exactamente_una_vez(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """El endpoint hace un solo despacho y conserva el envelope tipado."""
        execute = AsyncMock(return_value={"today": 3})
        monkeypatch.setattr(mcp_service, "execute_tool", execute)

        response = client.post(
            "/mcp/tools",
            json={"tool": "get_metrics"},
            headers={"X-MCP-Key": "test-read-key"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "tool": "get_metrics",
            "result": {"today": 3},
        }
        execute.assert_awaited_once_with("get_metrics", {})

    def test_get_metrics_reutiliza_agregador_existente(
        self,
        mcp_session_factory: sessionmaker[Session],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """La tool delega las fórmulas al mismo agregador del dashboard."""
        del mcp_session_factory  # La fixture instala SessionLocal aislado.
        aggregate = Mock(wraps=get_dashboard_kpis)
        monkeypatch.setattr(
            mcp_service.metrics_service,
            "get_dashboard_kpis",
            aggregate,
        )

        result = mcp_service.get_metrics({})

        aggregate.assert_called_once()
        assert result["today"] == 0
        assert "success_rate" in result
        assert "phone_hash" not in result

    def test_list_conversations_pagina_por_cursor_id(
        self,
        client: TestClient,
        mcp_session_factory: sessionmaker[Session],
    ) -> None:
        first_id = _insert_consultation(mcp_session_factory, producto="papa")
        second_id = _insert_consultation(mcp_session_factory, producto="tomate")
        third_id = _insert_consultation(mcp_session_factory, producto="cebolla")

        first_page = client.post(
            "/mcp/tools",
            json={
                "tool": "list_conversations",
                "arguments": {"limit": 2},
            },
            headers={"X-MCP-Key": "test-read-key"},
        )
        first_result = first_page.json()["result"]
        second_page = client.post(
            "/mcp/tools",
            json={
                "tool": "list_conversations",
                "arguments": {
                    "limit": 2,
                    "cursor": first_result["next_cursor"],
                },
            },
            headers={"X-MCP-Key": "test-read-key"},
        )

        assert first_page.status_code == 200
        assert [item["id"] for item in first_result["items"]] == [
            third_id,
            second_id,
        ]
        assert first_result["next_cursor"] == second_id
        assert second_page.status_code == 200
        assert [item["id"] for item in second_page.json()["result"]["items"]] == [
            first_id
        ]
        assert second_page.json()["result"]["next_cursor"] is None

    def test_get_conversation_por_id_usa_allowlist_sin_pii(
        self,
        client: TestClient,
        mcp_session_factory: sessionmaker[Session],
    ) -> None:
        conversation_id = _insert_consultation(
            mcp_session_factory,
            query_text="NOMBRE_PERSONA_PRIVADO",
            response_text="RESPUESTA_PRIVADA",
            delivery_status="failed",
            delivery_error_code="/private/db.sqlite STACK SECRET",
        )

        response = client.post(
            "/mcp/tools",
            json={
                "tool": "get_conversation",
                "arguments": {"id": conversation_id},
            },
            headers={"X-MCP-Key": "test-read-key"},
        )

        assert response.status_code == 200
        result = response.json()["result"]
        assert set(result) == {
            "id",
            "intent",
            "producto",
            "created_at",
            "delivered_at",
            "delivery_status",
            "delivery_error_code",
            "latency_ms",
            "whisper_ms",
            "llm_ms",
            "tts_ms",
            "requires_review",
        }
        assert result["id"] == conversation_id
        assert result["delivery_error_code"] == "delivery_error_unknown"
        serialized = response.text.lower()
        for forbidden in (
            "phone_hash",
            "query_text",
            "response_text",
            "nombre_persona_privado",
            "respuesta_privada",
            "hash_secreto",
            "/private/",
            "stack",
            "secret",
        ):
            assert forbidden not in serialized

    def test_get_conversation_inexistente_retorna_404_estable(
        self,
        client: TestClient,
        mcp_session_factory: sessionmaker[Session],
    ) -> None:
        del mcp_session_factory

        response = client.post(
            "/mcp/tools",
            json={"tool": "get_conversation", "arguments": {"id": 999}},
            headers={"X-MCP-Key": "test-read-key"},
        )

        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "MCP_CONVERSATION_NOT_FOUND"

    def test_get_error_log_retorna_solo_fallos_estructurados(
        self,
        client: TestClient,
        mcp_session_factory: sessionmaker[Session],
    ) -> None:
        _insert_consultation(mcp_session_factory, delivery_status="delivered")
        known_id = _insert_consultation(
            mcp_session_factory,
            delivery_status="failed",
            delivery_error_code="pipeline_no_response",
        )
        unknown_id = _insert_consultation(
            mcp_session_factory,
            delivery_status="failed",
            delivery_error_code="traceback /srv/app secret",
        )

        response = client.post(
            "/mcp/tools",
            json={"tool": "get_error_log", "arguments": {"limit": 10}},
            headers={"X-MCP-Key": "test-read-key"},
        )

        assert response.status_code == 200
        items = response.json()["result"]["items"]
        assert [item["id"] for item in items] == [unknown_id, known_id]
        assert {item["delivery_status"] for item in items} == {"failed"}
        assert items[0]["delivery_error_code"] == "delivery_error_unknown"
        assert items[1]["delivery_error_code"] == "pipeline_no_response"
        assert "traceback" not in response.text.lower()
        assert "/srv/" not in response.text.lower()

    def test_get_error_log_conserva_todo_codigo_que_delivery_service_acepta(
        self,
        client: TestClient,
        mcp_session_factory: sessionmaker[Session],
    ) -> None:
        """Regresión: la whitelist de lectura no puede quedarse corta.

        Antes MCP tenía su propia copia de la lista y códigos válidos como
        openwa_send_failed se leían como delivery_error_unknown.
        """
        esperados = sorted(delivery_service.ALLOWED_DELIVERY_ERROR_CODES)
        for codigo in esperados:
            _insert_consultation(
                mcp_session_factory,
                delivery_status="failed",
                delivery_error_code=codigo,
            )

        response = client.post(
            "/mcp/tools",
            json={"tool": "get_error_log", "arguments": {"limit": 50}},
            headers={"X-MCP-Key": "test-read-key"},
        )

        assert response.status_code == 200
        items = response.json()["result"]["items"]
        assert sorted(item["delivery_error_code"] for item in items) == esperados
        assert "delivery_error_unknown" not in {item["delivery_error_code"] for item in items}

    @pytest.mark.parametrize(
        ("tool", "arguments", "error_code"),
        [
            ("list_conversations", {"limit": 0}, "MCP_INVALID_LIMIT"),
            ("list_conversations", {"limit": 51}, "MCP_INVALID_LIMIT"),
            ("list_conversations", {"limit": True}, "MCP_INVALID_LIMIT"),
            ("list_conversations", {"cursor": 0}, "MCP_INVALID_CURSOR"),
            ("list_conversations", {"cursor": "1"}, "MCP_INVALID_CURSOR"),
            ("get_conversation", {}, "MCP_ID_REQUIRED"),
            ("get_conversation", {"id": 0}, "MCP_INVALID_ID"),
            ("get_conversation", {"id": "1"}, "MCP_INVALID_ID"),
            ("get_metrics", {"extra": 1}, "MCP_INVALID_ARGUMENTS"),
        ],
    )
    def test_argumentos_invalidos_retornan_400_estable(
        self,
        client: TestClient,
        tool: str,
        arguments: dict[str, object],
        error_code: str,
    ) -> None:
        response = client.post(
            "/mcp/tools",
            json={"tool": tool, "arguments": arguments},
            headers={"X-MCP-Key": "test-read-key"},
        )

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == error_code

    def test_error_sqlalchemy_retorna_503_y_cierra_sesion(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class BrokenSession:
            closed = False

            def execute(self, statement: object) -> None:
                del statement
                raise SQLAlchemyError("sqlite:////private/path STACK SECRET")

            def close(self) -> None:
                self.closed = True

        broken_session = BrokenSession()
        monkeypatch.setattr(mcp_service, "SessionLocal", lambda: broken_session)

        response = client.post(
            "/mcp/tools",
            json={"tool": "list_conversations"},
            headers={"X-MCP-Key": "test-read-key"},
        )

        assert response.status_code == 503
        assert response.json()["detail"] == {
            "code": "MCP_DATABASE_ERROR",
            "message": "No fue posible consultar los datos administrativos.",
        }
        assert broken_session.closed is True
        assert "/private/" not in response.text
        assert "STACK" not in response.text
        assert "SECRET" not in response.text


class TestMcpOdepaSync:
    """Verifica sincronización ODEPA administrativa y serializada."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "mcp_api_key", "test-read-key")
        monkeypatch.setattr(settings, "mcp_admin_key", "test-admin-key")
        monkeypatch.setattr(mcp_service, "_sync_odepa_lock", asyncio.Lock())

    @pytest.fixture
    def client(self) -> TestClient:
        test_app = FastAPI()
        test_app.include_router(router)
        return TestClient(test_app)

    @pytest.mark.asyncio
    async def test_servicio_reutiliza_sync_y_retorna_solo_conteos(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sync = AsyncMock(
            return_value=mcp_service.odepa_service.SyncResult(
                insertados=4,
                actualizados=2,
            )
        )
        monkeypatch.setattr(mcp_service.odepa_service, "sync_odepa", sync)

        result = await mcp_service.execute_tool("sync_odepa", {})

        assert result == {
            "status": "completed",
            "inserted": 4,
            "updated": 2,
            "total": 6,
        }
        sync.assert_awaited_once_with()

    def test_argumentos_no_vacios_retornan_400_sin_ejecutar(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sync = AsyncMock()
        monkeypatch.setattr(mcp_service.odepa_service, "sync_odepa", sync)

        response = client.post(
            "/mcp/tools",
            json={
                "tool": "sync_odepa",
                "arguments": {"force": True},
            },
            headers={"X-MCP-Key": "test-admin-key"},
        )

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "MCP_INVALID_ARGUMENTS"
        sync.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lock_rechaza_ejecucion_concurrente(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_sync() -> mcp_service.odepa_service.SyncResult:
            started.set()
            await release.wait()
            return mcp_service.odepa_service.SyncResult(insertados=1)

        monkeypatch.setattr(
            mcp_service.odepa_service,
            "sync_odepa",
            slow_sync,
        )
        first = asyncio.create_task(mcp_service.execute_tool("sync_odepa", {}))
        await asyncio.wait_for(started.wait(), timeout=1)

        try:
            with pytest.raises(
                mcp_service.McpConflictError,
                match="sincronización ODEPA",
            ) as conflict:
                await mcp_service.execute_tool("sync_odepa", {})
            assert conflict.value.code == "MCP_ODEPA_SYNC_IN_PROGRESS"
        finally:
            release.set()

        assert await first == {
            "status": "completed",
            "inserted": 1,
            "updated": 0,
            "total": 1,
        }

    def test_conflicto_se_traduce_a_409_estable(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        execute = AsyncMock(
            side_effect=mcp_service.McpConflictError(
                "MCP_ODEPA_SYNC_IN_PROGRESS",
                "Ya existe una sincronización ODEPA en curso.",
            )
        )
        monkeypatch.setattr(mcp_service, "execute_tool", execute)

        response = client.post(
            "/mcp/tools",
            json={"tool": "sync_odepa"},
            headers={"X-MCP-Key": "test-admin-key"},
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "MCP_ODEPA_SYNC_IN_PROGRESS"
        execute.assert_awaited_once_with("sync_odepa", {})

    @pytest.mark.parametrize(
        "failure",
        [
            mcp_service.odepa_service.OdepaSyncError(
                "https://privado.example/SECRET"
            ),
            SQLAlchemyError("sqlite:////private/path STACK SECRET"),
        ],
    )
    def test_fallo_externo_o_db_retorna_503_saneado(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        failure: Exception,
    ) -> None:
        sync = AsyncMock(side_effect=failure)
        monkeypatch.setattr(mcp_service.odepa_service, "sync_odepa", sync)

        response = client.post(
            "/mcp/tools",
            json={"tool": "sync_odepa"},
            headers={"X-MCP-Key": "test-admin-key"},
        )

        assert response.status_code == 503
        assert response.json()["detail"] == {
            "code": "MCP_ODEPA_SYNC_ERROR",
            "message": "No fue posible completar la sincronización ODEPA.",
        }
        serialized = response.text.lower()
        assert "privado.example" not in serialized
        assert "/private/" not in serialized
        assert "stack" not in serialized
        assert "secret" not in serialized

    @pytest.mark.asyncio
    async def test_lock_se_libera_despues_de_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sync = AsyncMock(
            side_effect=[
                mcp_service.odepa_service.OdepaSyncError("SECRET"),
                mcp_service.odepa_service.SyncResult(actualizados=2),
            ]
        )
        monkeypatch.setattr(mcp_service.odepa_service, "sync_odepa", sync)

        with pytest.raises(mcp_service.McpUpstreamError):
            await mcp_service.execute_tool("sync_odepa", {})

        assert mcp_service._sync_odepa_lock.locked() is False
        assert await mcp_service.execute_tool("sync_odepa", {}) == {
            "status": "completed",
            "inserted": 0,
            "updated": 2,
            "total": 2,
        }
        assert sync.await_count == 2

    def test_log_incluye_tool_resultado_y_request_id_sin_credenciales(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        execute = AsyncMock(
            return_value={
                "status": "completed",
                "inserted": 1,
                "updated": 0,
                "total": 1,
            }
        )
        monkeypatch.setattr(mcp_service, "execute_tool", execute)
        test_app = FastAPI()
        test_app.include_router(router)
        test_app.add_middleware(RequestIDMiddleware)

        with (
            caplog.at_level("INFO", logger="app.mcp.router"),
            TestClient(test_app) as client,
        ):
            response = client.post(
                "/mcp/tools",
                json={"tool": "sync_odepa"},
                headers={
                    "X-MCP-Key": "test-admin-key",
                    "X-Request-ID": "req-sync-123",
                },
            )

        assert response.status_code == 200
        assert "tool=sync_odepa" in caplog.text
        assert "result=completed" in caplog.text
        assert "request_id=req-sync-123" in caplog.text
        assert "test-admin-key" not in caplog.text
        assert "arguments" not in caplog.text


class TestMcpRateLimit:
    """Verifica la cuota MCP dedicada antes de autenticación."""

    @pytest.fixture(autouse=True)
    def _setup_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "mcp_api_key", "test-read-key")
        monkeypatch.setattr(settings, "mcp_admin_key", "test-admin-key")
        monkeypatch.setattr(
            mcp_service,
            "execute_read_tool",
            lambda _tool, _arguments: {"ok": True},
        )

    @staticmethod
    def _client(ip: str = "testclient") -> TestClient:
        test_app = FastAPI()
        test_app.include_router(router)
        return TestClient(test_app, client=(ip, 50000))

    @pytest.mark.parametrize(
        ("method", "path", "kwargs"),
        [
            ("get", "/mcp/tools/list", {}),
            ("post", "/mcp/tools", {"json": {"tool": "get_metrics"}}),
        ],
    )
    def test_sexto_intento_retorna_429_con_retry_after(
        self,
        method: str,
        path: str,
        kwargs: dict[str, object],
    ) -> None:
        with self._client() as client:
            for _ in range(5):
                response = client.request(
                    method,
                    path,
                    headers={"X-MCP-Key": "test-read-key"},
                    **kwargs,
                )
                assert response.status_code == 200

            limited = client.request(
                method,
                path,
                headers={"X-MCP-Key": "test-read-key"},
                **kwargs,
            )

        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) >= 1

    def test_claves_invalidas_consumen_cupo_antes_de_auth(self) -> None:
        with self._client() as client:
            for _ in range(5):
                response = client.get(
                    "/mcp/tools/list",
                    headers={"X-MCP-Key": "incorrecta"},
                )
                assert response.status_code == 401

            limited = client.get(
                "/mcp/tools/list",
                headers={"X-MCP-Key": "test-read-key"},
            )

        assert limited.status_code == 429

    def test_ambas_rutas_comparten_el_bucket(self) -> None:
        with self._client() as client:
            for _ in range(3):
                assert (
                    client.get(
                        "/mcp/tools/list",
                        headers={"X-MCP-Key": "test-read-key"},
                    ).status_code
                    == 200
                )
            for _ in range(2):
                assert (
                    client.post(
                        "/mcp/tools",
                        json={"tool": "get_metrics"},
                        headers={"X-MCP-Key": "test-read-key"},
                    ).status_code
                    == 200
                )

            limited = client.get(
                "/mcp/tools/list",
                headers={"X-MCP-Key": "test-read-key"},
            )

        assert limited.status_code == 429

    def test_ips_distintas_tienen_buckets_separados(self) -> None:
        with (
            self._client("198.51.100.10") as first_client,
            self._client("198.51.100.11") as second_client,
        ):
            for _ in range(5):
                assert (
                    first_client.get(
                        "/mcp/tools/list",
                        headers={"X-MCP-Key": "test-read-key"},
                    ).status_code
                    == 200
                )

            response = second_client.get(
                "/mcp/tools/list",
                headers={"X-MCP-Key": "test-read-key"},
            )

        assert response.status_code == 200
