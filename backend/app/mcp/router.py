"""Router HTTP del MCP server (issue #193).

Expone tools MCP vía POST /mcp/tools con autenticación por header
X-MCP-Key y scopes granulares.

⚠️ IMPORTANTE: Este router NO está montado en main.py.
Es andamiaje adelantado post-MVP. Si se monta, asegurar que:
1. settings.mcp_api_key y settings.mcp_admin_key estén configuradas.
2. Los handlers de tools estén implementados (hoy son stubs).
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])

# Scopes asignados a cada tool MCP.
_TOOL_SCOPES: dict[str, str] = {
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


@router.post("/tools")
async def mcp_tools_handler(
    request: Request,
    x_mcp_key: str | None = Header(default=None, alias="X-MCP-Key"),
) -> JSONResponse:
    """Endpoint principal del MCP server.

    Recibe tool calls y los enruta al handler correspondiente.
    Autentica por header X-MCP-Key con secrets.compare_digest y verifica scopes.
    """
    api_key_scope = _authenticate(x_mcp_key)

    body = await request.json()
    tool_name = body.get("tool", "")

    if tool_name not in _TOOL_SCOPES:
        raise HTTPException(status_code=400, detail=f"Tool desconocida: {tool_name}")

    if not _check_scope(tool_name, api_key_scope):
        raise HTTPException(
            status_code=403, detail=f"Scope insuficiente para {tool_name}"
        )

    return JSONResponse(
        content={"tool": tool_name, "result": "stub — implementación pendiente"}
    )


@router.get("/tools/list")
async def mcp_tools_list(
    x_mcp_key: str | None = Header(default=None, alias="X-MCP-Key"),
) -> JSONResponse:
    """Lista las tools MCP disponibles."""
    _authenticate(x_mcp_key)  # Solo verificamos auth, no scope para listar.
    return JSONResponse(
        content={
            "tools": [
                {"name": name, "scope": scope}
                for name, scope in _TOOL_SCOPES.items()
            ]
        }
    )
