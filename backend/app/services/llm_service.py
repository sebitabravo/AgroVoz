"""Servicio LLM: Qwen2.5-3B Q4 con Tool Calling y whitelist estricta.

Carga el modelo cuantizado via llama-cpp-python (CPU, 4-bit).
Singleton con lazy loading: el modelo se carga en la primera llamada,
no al importar el modulo.

Tools disponibles (whitelist):
  - get_price(producto, mercado) -> odepa_service.get_price_for_llm()
  - get_weather(lat, lon)     -> weather_service.get_weather()

Si el LLM intenta usar cualquier otra tool, se responde con texto
de fallback. Si no entiende la query, pide reformular.

System prompt ESENCIAL definido en SYSTEM_PROMPT (literal del issue #18).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.core.config import settings

if TYPE_CHECKING:
    from llama_cpp import Llama

logger = logging.getLogger(__name__)

# ── System prompt (literal del issue #18) ──────────────────────────

SYSTEM_PROMPT = (
    "Eres AgroVoz, un asistente de voz para pequeños agricultores chilenos.\n"
    "REGLAS ESTRICTAS:\n"
    "1. SOLO entregas datos de precios (ODEPA) y clima (OpenWeatherMap).\n"
    "2. NUNCA das recomendaciones agronómicas. Si preguntan \"¿debo regar?\",\n"
    "   responde con el pronóstico de lluvia, sin interpretar.\n"
    "3. NUNCA inventas precios ni clima. Si no tienes el dato, lo dices.\n"
    "4. Respondes en español chileno, con frases cortas y claras (máximo 3 oraciones).\n"
    "5. Los precios se dan en pesos chilenos, con la unidad de medida.\n"
    "6. Si no entiendes la pregunta, pides que la reformulen.\n"
)

# Texto de fallback cuando el LLM intenta una tool fuera del whitelist.
FALLBACK_TEXT = (
    "No tengo ese dato, pero puedo consultarte el precio en ODEPA o el clima."
)

# Texto cuando el LLM no genera respuesta.
NO_RESPONSE_TEXT = "No entendí tu consulta. ¿Podrías reformularla?"

# Tool names permitidas. Cualquier otra -> fallback.
WHITELIST_TOOLS = frozenset({"get_price", "get_weather"})

# Máximo de iteraciones del Tool Calling loop (previene loops infinitos).
MAX_TOOL_ITERATIONS = 3

# Timeout de generación por llamada al LLM (segundos).
# Con CPU RTF ~2x en VPS CX43, una respuesta corta (<50 tokens) tarda ~1-2s.
# 30s es conservador para queries con tool calling (múltiples ida-vuelta).
_GENERATION_TIMEOUT = 30.0

# Contexto máximo del modelo (tokens). Qwen2.5-3B soporta hasta 32k,
# pero con 4-bit y CPU limitamos a 2048 para mantener latencia <15s.
_N_CTX = 2048

# ── Tool definitions (OpenAI function-calling format) ──────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_price",
            "description": (
                "Consulta el precio mas reciente de un producto agricola "
                "en un mercado mayorista de ODEPA. "
                "Ejemplo: papa en Lo Valledor."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "producto": {
                        "type": "string",
                        "description": "Nombre del producto en singular (ej: papa, tomate, lechuga, cebolla)",
                    },
                    "mercado": {
                        "type": "string",
                        "description": "Nombre del mercado mayorista (ej: Lo Valledor, La Vega, Talca)",
                    },
                },
                "required": ["producto", "mercado"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": (
                "Consulta el clima actual (temperatura, humedad, lluvia, viento) "
                "en una ubicacion especifica. Usa coordenadas de Traiguen "
                "(-38.23, -72.68) si el agricultor no especifica otra ubicacion."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {
                        "type": "number",
                        "description": "Latitud en grados decimales (-90 a 90)",
                    },
                    "lon": {
                        "type": "number",
                        "description": "Longitud en grados decimales (-180 a 180)",
                    },
                },
                "required": ["lat", "lon"],
            },
        },
    },
]

# ── Singleton del modelo ───────────────────────────────────────────

_model: Llama | None = None
_model_lock = threading.Lock()
_model_loaded = False
_model_error: str | None = None


def _get_model() -> Llama | None:
    """Carga el modelo Qwen2.5-3B Q4 en modo lazy y thread-safe.

    Double-checked locking: si el modelo ya está cargado, retorna
    inmediatamente sin adquirir el lock (hot path).

    El modelo se cachea en el proceso. Cada worker de uvicorn tiene
    su propia instancia (no se comparte entre workers con sqlite).

    Returns:
        Instancia de Llama lista para generar, o None si:
        - llama-cpp-python no está instalado (CI/test)
        - El modelo no existe en el path configurado
        - Ocurrió un error al cargar
    """
    global _model, _model_loaded, _model_error

    if _model_loaded:
        return _model

    with _model_lock:
        if _model_loaded:
            return _model

        _model_loaded = True

        try:
            from llama_cpp import Llama
        except ImportError:
            _model_error = "llama-cpp-python no instalado"
            logger.warning("llama-cpp-python no instalado — LLM funcionando en modo mock")
            return None

        model_path = settings.llm_model_path
        if not model_path or not os.path.isfile(model_path):
            _model_error = f"Modelo LLM no encontrado en {model_path}"
            logger.warning("Modelo LLM no encontrado en %s — LLM funcionando en modo mock", model_path)
            return None

        try:
            logger.info("Cargando modelo LLM desde %s ...", model_path)
            _model = Llama(
                model_path=model_path,
                n_ctx=_N_CTX,
                n_threads=4,
                verbose=False,
            )
            logger.info("Modelo LLM cargado — n_ctx=%d", _N_CTX)
        except Exception as exc:
            _model_error = f"Error al cargar modelo: {exc}"
            logger.exception("Error al cargar modelo LLM")
            return None

    return _model


# ── Tool dispatcher ─────────────────────────────────────────────────

# Tipos para la tabla de herramientas.
# Acepta tanto sync (get_price_for_llm) como async (get_weather).
ToolHandler = Callable[..., Any]


def _get_tool_handlers() -> dict[str, ToolHandler]:
    """Devuelve el diccionario de handlers para cada tool del whitelist.

    Los imports son lazy para evitar dependencias circulares y permitir
    que el modulo llm_service.py sea importable sin DB ni servicios.
    """
    from app.services.odepa_service import get_price_for_llm
    from app.services.weather_service import get_weather

    return {
        "get_price": get_price_for_llm,
        "get_weather": get_weather,
    }


async def _execute_tool(name: str, arguments: dict[str, object]) -> str:
    """Ejecuta una tool del whitelist y retorna el resultado como texto.

    Args:
        name: Nombre de la función (debe estar en WHITELIST_TOOLS).
        arguments: Diccionario con los argumentos parseados del JSON.

    Returns:
        Resultado textual de la tool, o mensaje de error si falla.
    """
    handlers = _get_tool_handlers()
    handler = handlers.get(name)

    if handler is None:
        logger.warning("Tool no whitelisteada: %s", name)
        return FALLBACK_TEXT

    try:
        # get_price necesita session de DB. Se la pasamos como kwarg
        # si el handler la requiere (inspected via signature).
        if name == "get_price":
            from app.core.database import SessionLocal

            session = SessionLocal()
            try:
                # Llamada síncrona en thread pool para no bloquear event loop.
                result = await asyncio.to_thread(
                    handler,
                    session=session,
                    **arguments,
                )
            finally:
                session.close()
        else:
            # get_weather es async
            if asyncio.iscoroutinefunction(handler):
                result = await handler(**arguments)
            else:
                result = await asyncio.to_thread(handler, **arguments)

        logger.info("Tool %s ejecutada — args=%s", name, arguments)
        return str(result)
    except Exception as exc:
        logger.exception("Error ejecutando tool %s: %s", name, exc)
        return "Hubo un error al consultar ese dato. ¿Probamos con otro?"


# ── Tool Calling loop ───────────────────────────────────────────────


def _parse_tool_calls(response: Any) -> list[dict[str, Any]]:
    """Extrae tool calls de una respuesta del LLM.

    Args:
        response: Respuesta completa de create_chat_completion (Any porque
                  llama-cpp retorna tipos complejos que varian por version).

    Returns:
        Lista de tool calls (cada una con name y arguments).
    """
    choices: Any = response.get("choices", [])
    if not choices:
        return []
    message: Any = choices[0].get("message", {})
    tool_calls: Any = message.get("tool_calls", [])
    if not isinstance(tool_calls, list):
        return []
    return tool_calls


def _parse_content(response: Any) -> str:
    """Extrae el texto de contenido de una respuesta del LLM.

    Args:
        response: Respuesta completa de create_chat_completion (Any porque
                  llama-cpp retorna tipos complejos que varian por version).

    Returns:
        Texto de la respuesta, o cadena vacía si no hay.
    """
    choices: Any = response.get("choices", [])
    if not choices:
        return ""
    message: Any = choices[0].get("message", {})
    content: Any = message.get("content", "")
    return str(content).strip() if content else ""


def _build_messages(user_query: str, history: list[dict[str, object]]) -> list[dict[str, object]]:
    """Construye la lista de mensajes para el LLM.

    Formato OpenAI chat completion: system + history + user.
    """
    messages: list[dict[str, object]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]
    messages.extend(history)
    messages.append({"role": "user", "content": user_query})
    return messages


async def answer(
    query_text: str,
    history: list[dict[str, object]] | None = None,
) -> str:
    """Genera una respuesta textual usando el LLM con Tool Calling.

    Flujo:
    1. Construir mensajes (system prompt + historial + query).
    2. Llamar al LLM con herramientas disponibles.
    3. Si el LLM pide una tool -> ejecutar (solo whitelist) -> devolver
       resultado al LLM -> generar respuesta final.
    4. Si el LLM responde directamente -> retornar contenido.
    5. Si no hay modelo -> fallback mock para desarrollo.

    Args:
        query_text: Texto transcrito de la consulta del agricultor.
        history: Mensajes previos del diálogo (opcional). Formato
                 [{"role": "...", "content": "..."}, ...].

    Returns:
        Texto de respuesta en español chileno, listo para TTS.
    """
    if not query_text or not query_text.strip():
        return NO_RESPONSE_TEXT

    model = _get_model()
    history = history or []

    if model is None:
        return _mock_answer(query_text)

    messages = _build_messages(query_text.strip(), history)

    try:
        # Tool Calling loop
        for _iteration in range(MAX_TOOL_ITERATIONS):
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    model.create_chat_completion,
                    messages=messages,  # type: ignore[arg-type]
                    tools=TOOLS,  # type: ignore[arg-type]
                    tool_choice="auto",
                    temperature=0.0,
                    max_tokens=256,
                ),
                timeout=_GENERATION_TIMEOUT,
            )

            tool_calls = _parse_tool_calls(response)
            if not tool_calls:
                # Sin tool calls -> respuesta final del LLM.
                content = _parse_content(response)
                if content:
                    return content
                # Si el LLM no generó contenido ni tool calls, reintentar
                # con mensaje de que responda.
                messages.append({
                    "role": "system",
                    "content": "Responde al usuario en español chileno con frases cortas.",
                })
                continue

            # El LLM quiere ejecutar herramientas.
            # Agregar respuesta del asistente (con tool calls) al historial.
            assistant_msg: dict[str, object] = {
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls,
            }
            messages.append(assistant_msg)

            for tc in tool_calls:
                fn_info: dict[str, Any] = tc.get("function", {})
                fn_name: str = fn_info.get("name", "")
                fn_args_str: str = fn_info.get("arguments", "{}")

                # Whitelist enforcement: solo get_price y get_weather.
                if fn_name not in WHITELIST_TOOLS:
                    logger.warning("Tool fuera de whitelist: %s — enviando fallback", fn_name)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", "unknown"),
                        "content": FALLBACK_TEXT,
                    })
                    continue

                # Parsear argumentos JSON.
                try:
                    fn_args: dict[str, object] = json.loads(str(fn_args_str))
                except json.JSONDecodeError:
                    logger.warning("Argumentos JSON invalidos para %s: %s", fn_name, fn_args_str)
                    fn_args = {}

                # Ejecutar tool.
                tool_result = await _execute_tool(fn_name, fn_args)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", "unknown"),
                    "content": tool_result,
                })

        # Si llegamos acá, se agotaron las iteraciones.
        logger.warning(
            "Tool Calling loop agoto %d iteraciones — query=%.100s",
            MAX_TOOL_ITERATIONS,
            query_text,
        )
        # Último intento: forzar respuesta sin tools.
        messages.append({
            "role": "system",
            "content": (
                "Genera una respuesta final en español chileno "
                "con los datos disponibles. Maximo 3 oraciones."
            ),
        })
        try:
            final_response = await asyncio.wait_for(
                asyncio.to_thread(
                    model.create_chat_completion,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=0.0,
                    max_tokens=256,
                ),
                timeout=_GENERATION_TIMEOUT,
            )
            content = _parse_content(final_response)
            if content:
                return content
        except (TimeoutError, Exception) as exc:
            logger.warning("Error en respuesta final: %s", exc)

        return FALLBACK_TEXT

    except TimeoutError:
        logger.warning("Timeout del LLM (%ss) — query=%.100s", _GENERATION_TIMEOUT, query_text)
        return "Estoy teniendo problemas para responder. ¿Podrías preguntar de nuevo más breve?"
    except Exception as exc:
        logger.exception("Error en generacion LLM: %s", exc)
        return "Tuve un problema al procesar tu consulta. ¿Probamos de nuevo?"


def _mock_answer(query_text: str) -> str:
    """Respuesta mock para desarrollo y CI sin modelo LLM.

    Detecta intenciones básicas por keyword para simular Tool Calling.
    Solo para desarrollo; en producción el modelo real debe estar cargado.
    """
    q = query_text.strip().lower()

    # Detección de keywords de clima
    clima_keywords = ["clima", "tiempo", "temperatura", "lluvia", "lloviendo",
                      "frio", "calor", "humedad", "viento", "pronóstico", "pronostico"]
    if any(kw in q for kw in clima_keywords):
        return (
            "En Traiguén ahora: 18°C, nublado, humedad 65%, viento 3.6 m/s, "
            "lluvia 0.5 mm."
        )

    # Detección de keywords de precio
    precio_keywords = ["precio", "cuánto", "cuanto", "cuesta", "vale",
                       "está", "esta", "cómo está", "como esta"]
    if any(kw in q for kw in precio_keywords):
        # Intentar extraer producto (palabra después de "la", "el", "los", "las")
        return (
            "Papa está a $1.200 el kilo en Lo Valledor, precio del 22/06/2026."
        )

    # Fuera de scope
    return FALLBACK_TEXT


def is_model_available() -> bool:
    """Indica si el modelo LLM está cargado y listo para usar."""
    return _model is not None


def get_model_error() -> str | None:
    """Retorna el mensaje de error si el modelo no se pudo cargar."""
    return _model_error


def reset_model() -> None:
    """Resetea el singleton para tests (libera memoria)."""
    global _model, _model_loaded, _model_error
    _model = None
    _model_loaded = False
    _model_error = None
