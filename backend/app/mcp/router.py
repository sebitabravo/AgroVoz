"""Router HTTP de la RPC administrativa interna tipo MCP (issue #200).

Expone tools MCP vía POST /mcp/tools con autenticación por header
X-MCP-Key, scopes granulares y rate limit dedicado. ``main.py`` lo monta
únicamente cuando ``MCP_ENABLED`` está activo y sus claves son seguras.
Los handlers delegan en servicios existentes y retornan proyecciones saneadas.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.core.config import settings
from app.core.rate_limiter import check_mcp_rate_limit
from app.schemas.mcp import (
    McpScope,
    McpToolDefinition,
    McpToolListResponse,
    McpToolRequest,
    McpToolResponse,
)
from app.services import mcp_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/mcp",
    tags=["mcp"],
    # La dependencia de router se ejecuta antes de entrar al handler y, por
    # tanto, antes de autenticar. Las claves inválidas también consumen cupo.
    dependencies=[Depends(check_mcp_rate_limit)],
)

# Scopes asignados a cada tool MCP.
_TOOL_SCOPES: dict[str, McpScope] = {
    "list_conversations": "read",
    "get_conversation": "read",
    "get_metrics": "read",
    "get_error_log": "read",
    "sync_odepa": "admin:write",
}


def _authenticate(x_mcp_key: str | None) -> str:
    """Autentica la API key y retorna su scope.

    Usa secrets.compare_digest para prevenir timing attacks.

    Returns:
        "read" o "admin:write" según la key.

    Raises:
        HTTPException(401) si la key es inválida o falta.
    """
    if not x_mcp_key:
        raise HTTPException(status_code=401, detail="X-MCP-Key requerido")

    # Validar contra admin key (tiene scope admin:write).
    if settings.mcp_admin_key and secrets.compare_digest(
        x_mcp_key, settings.mcp_admin_key
    ):
        return "admin:write"

    # Validar contra read key (tiene scope read).
    if settings.mcp_api_key and secrets.compare_digest(
        x_mcp_key, settings.mcp_api_key
    ):
        return "read"

    raise HTTPException(status_code=401, detail="X-MCP-Key inválida")


def _check_scope(tool_name: str, api_key_scope: str) -> bool:
    """Verifica que la API key tenga scope suficiente para la tool."""
    required = _TOOL_SCOPES.get(tool_name, "admin:write")
    return not (required == "admin:write" and api_key_scope != "admin:write")


def _log_tool_result(request: Request, tool_name: str, result: str) -> None:
    """Registra resultado operacional sin claves, argumentos ni payloads."""
    request_id = getattr(request.state, "request_id", "-")
    if not isinstance(request_id, str):
        request_id = "-"
    logger.info(
        "MCP tool=%s result=%s request_id=%s",
        tool_name,
        result,
        request_id,
    )


@router.post("/tools", response_model=McpToolResponse)
async def mcp_tools_handler(
    request: Request,
    payload: McpToolRequest,
    x_mcp_key: str | None = Header(default=None, alias="X-MCP-Key"),
) -> McpToolResponse:
    """Endpoint principal del MCP server.

    Recibe tool calls y los enruta al handler correspondiente.
    Autentica por header X-MCP-Key con secrets.compare_digest y verifica scopes.
    """
    api_key_scope = _authenticate(x_mcp_key)
    tool_name = payload.tool

    if tool_name not in _TOOL_SCOPES:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "MCP_TOOL_UNKNOWN",
                "message": "La tool solicitada no está disponible.",
            },
        )

    if not _check_scope(tool_name, api_key_scope):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "MCP_SCOPE_INSUFFICIENT",
                "message": "La clave no tiene scope para esta tool.",
            },
        )

    try:
        result = await mcp_service.execute_tool(
            tool_name,
            payload.arguments,
        )
    except mcp_service.McpInputError as exc:
        _log_tool_result(request, tool_name, exc.code)
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": exc.message},
        ) from None
    except mcp_service.McpNotFoundError as exc:
        _log_tool_result(request, tool_name, exc.code)
        raise HTTPException(
            status_code=404,
            detail={"code": exc.code, "message": exc.message},
        ) from None
    except mcp_service.McpConflictError as exc:
        _log_tool_result(request, tool_name, exc.code)
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": exc.message},
        ) from None
    except mcp_service.McpDatabaseError as exc:
        _log_tool_result(request, tool_name, exc.code)
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": exc.message},
        ) from None
    except mcp_service.McpUpstreamError as exc:
        _log_tool_result(request, tool_name, exc.code)
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": exc.message},
        ) from None

    success_result = "completed" if tool_name == "sync_odepa" else "ok"
    _log_tool_result(request, tool_name, success_result)
    return McpToolResponse(tool=tool_name, result=result)


@router.get("/tools/list", response_model=McpToolListResponse)
async def mcp_tools_list(
    x_mcp_key: str | None = Header(default=None, alias="X-MCP-Key"),
) -> McpToolListResponse:
    """Lista las tools MCP disponibles."""
    _authenticate(x_mcp_key)  # Solo verificamos auth, no scope para listar.
    return McpToolListResponse(
        tools=[
            McpToolDefinition(name=name, scope=scope)
            for name, scope in _TOOL_SCOPES.items()
        ]
    )
