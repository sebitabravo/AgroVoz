"""Cliente OpenRouter para el proveedor remoto primario y su fallback.

Se usa solo si OPENROUTER_API_KEY esta configurada. En el orden global de
proveedores puede atender primero consultas de lectura; el orquestador aplica
un deadline corto y deriva a Qwen2.5 local cuando falla. Usa el router
"openrouter/free" (modelos gratuitos que rotan sin aviso segun disponibilidad
del proveedor) via la API OpenAI-compatible de OpenRouter, con tool calling
nativo (tools=, tool_calls en la respuesta).

Este modulo no decide la cadena de proveedores: solo ejecuta una llamada
remota o propaga el error para que llm_service.py aplique el fallback local y,
si corresponde, el fallback determinista.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Cliente HTTP compartido (mismo patron que weather_service._get_http_client).
_http_client: httpx.AsyncClient | None = None
_http_client_lock = asyncio.Lock()


def is_configured() -> bool:
    """True si hay una API key configurada para habilitar el remoto."""
    return bool(settings.openrouter_api_key.strip())


async def _get_http_client() -> httpx.AsyncClient:
    """Cliente HTTP singleton con timeout configurable para OpenRouter."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        async with _http_client_lock:
            if _http_client is None or _http_client.is_closed:
                _http_client = httpx.AsyncClient(timeout=settings.openrouter_timeout_seconds)
    return _http_client


async def close_http_client() -> None:
    """Cierra el cliente HTTP (llamado desde el shutdown del lifespan)."""
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None


async def chat_completion_with_tools(
    messages: list[dict[str, object]],
    tools: list[dict[str, object]],
    max_tokens: int = 256,
) -> dict[str, Any]:
    """Llama a OpenRouter con tool calling nativo (formato OpenAI).

    Args:
        messages: Lista de mensajes (system/user/assistant/tool).
        tools: Definiciones de tools en formato OpenAI function-calling
               (el mismo TOOLS de llm_service.py, sin modificar).
        max_tokens: Tokens maximos de la respuesta generada.

    Returns:
        JSON crudo de la respuesta (formato OpenAI chat completion).

    Raises:
        httpx.HTTPError: si la request falla (red, timeout, status >= 400).
        El caller decide como degradar — este modulo no atrapa errores.
    """
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        # Recomendados por OpenRouter para atribucion en su ranking publico,
        # no son secretos ni afectan el comportamiento de la API.
        "HTTP-Referer": "https://agrovoz.cl",
        "X-Title": "AgroVoz",
    }
    payload = {
        "model": settings.openrouter_model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }

    client = await _get_http_client()
    response = await client.post(_OPENROUTER_URL, headers=headers, json=payload)
    response.raise_for_status()
    return cast(dict[str, Any], response.json())
