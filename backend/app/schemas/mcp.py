"""Esquemas tipados para el transporte HTTP administrativo MCP."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

McpScope = Literal["read", "admin:write"]
type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | list[JsonValue] | dict[str, JsonValue]


class McpToolRequest(BaseModel):
    """Solicitud estricta para ejecutar una tool MCP."""

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class McpToolResponse(BaseModel):
    """Envelope tipado para resultados JSON seguros de una tool."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    result: JsonValue


class McpToolDefinition(BaseModel):
    """Tool expuesta por MCP y scope mínimo requerido."""

    model_config = ConfigDict(extra="forbid")

    name: str
    scope: McpScope


class McpToolListResponse(BaseModel):
    """Listado tipado de tools MCP disponibles."""

    model_config = ConfigDict(extra="forbid")

    tools: list[McpToolDefinition]
