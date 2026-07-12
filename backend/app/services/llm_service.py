"""Servicio LLM: Qwen2.5-3B Q4 con Tool Calling y whitelist estricta.

Carga el modelo cuantizado via llama-cpp-python (CPU, 4-bit).
Singleton con lazy loading: el modelo se carga en la primera llamada,
no al importar el modulo.

Tools disponibles (whitelist):
  - get_price(producto, mercado)       -> odepa_service.get_price_for_llm()
  - get_price_history(producto, dias)  -> odepa_service.get_price_history_for_llm()
  - get_weather(lat, lon)              -> weather_service.get_weather()

Si el LLM intenta usar cualquier otra tool, se responde con texto
de fallback. Si no entiende la query, pide reformular.

System prompt ESENCIAL definido en SYSTEM_PROMPT (literal del issue #18).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings

if TYPE_CHECKING:
    from llama_cpp import Llama

logger = logging.getLogger(__name__)

# ── System prompt (literal del issue #18) ──────────────────────────

SYSTEM_PROMPT = (
    "Eres AgroVoz, un asistente de voz para pequeños agricultores chilenos.\n"
    "REGLAS ESTRICTAS:\n"
    "1. Tienes TRES herramientas. USA LA CORRECTA:\n"
    "   - get_price(producto, mercado): para consultar PRECIOS ACTUALES de productos agrícolas ODEPA.\n"
    "   - get_price_history(producto, dias): para PRECIOS PASADOS y cómo ha variado el precio.\n"
    "   - get_weather(lat, lon): para consultar CLIMA (temperatura, lluvia, viento).\n"
    "2. La consulta PUEDE ser de precio, clima, o AMBAS. Determina cual es segun:\n"
    "   - PRECIO: si menciona precio, cuánto, cuesta, vale, kilo, saco, malla, pesos,\n"
    "     luca, o cualquier producto agrícola (papa, tomate, cebolla, lechuga, etc.)\n"
    "   - PRECIO PASADO: si pregunta cuánto ESTABA, la semana pasada, ayer, hace días,\n"
    "     si subió o bajó -> get_price_history\n"
    "   - CLIMA: si menciona clima, tiempo, temperatura, lluvia, lloviendo, pronóstico,\n"
    "     frío, calor, humedad, viento\n"
    "   Ejemplos: \"a cuánto está la papa\" -> get_price\n"
    "             \"a cuánto estaba la papa la semana pasada\" -> get_price_history\n"
    "             \"cómo está el tiempo mañana\" -> get_weather\n"
    "             \"a cuánto la papa y cómo viene el clima\" -> AMBAS tools\n"
    "3. SIEMPRE intenta usar una herramienta antes de pedir reformulación.\n"
    "   Solo pide reformulación si la consulta NO menciona precio, productos, ni clima.\n"
    "4. NUNCA das recomendaciones agronómicas. Si preguntan \"¿debo regar?\",\n"
    "   responde con el pronóstico de lluvia, sin interpretar.\n"
    "5. NUNCA inventas precios ni clima. Si no tienes el dato, lo dices.\n"
    "6. Respondes en español chileno, con frases cortas y claras (máximo 3 oraciones).\n"
    "7. Los precios se dan en pesos chilenos, con la unidad de medida.\n"
    "8. Cuando reformules la respuesta de una herramienta, CONSERVA SIEMPRE la\n"
    "   mención de la fuente (ODEPA para precios, OpenMeteo para clima).\n"
    "   Nunca omitas \"según ODEPA\" o \"según OpenMeteo\" al resumir.\n"
)

# Texto de fallback cuando el LLM intenta una tool fuera del whitelist.
FALLBACK_TEXT = (
    "No tengo ese dato, pero puedo consultarte el precio en ODEPA o el clima."
)

# Texto cuando el LLM no genera respuesta.
NO_RESPONSE_TEXT = "No entendí tu consulta. ¿Podrías reformularla?"

# Tool names permitidas. Cualquier otra -> fallback.
WHITELIST_TOOLS = frozenset({"get_price", "get_price_history", "get_weather"})

# Máximo de iteraciones del Tool Calling loop (previene loops infinitos).
MAX_TOOL_ITERATIONS = 3

# Timeout de generación por llamada al LLM (segundos).
# Aumentado a 60s porque:
# - Cold start: el modelo tarda ~9s en cargarse en CPU
# - Con CPU RTF ~2x en VPS CX43, primera generacion post-carga puede tomar 30s+
# - Tool calling loop: cada iteracion necesita generar + ejecutar tool
_GENERATION_TIMEOUT = 60.0

# Contexto máximo del modelo (tokens). Qwen2.5-3B soporta hasta 32k,
# pero con 4-bit y CPU limitamos a 2048 para mantener latencia <15s.
_N_CTX = 2048

# ── Tool definitions (Qwen2.5 native XML format) ──────────────────
#
# Qwen2.5-3B-Instruct usa un formato nativo con tags XML para tool calling.
# El formato OpenAI (tools=TOOLS en create_chat_completion) NO funciona
# correctamente con llama-cpp-python 0.3.31 porque no aplica el chat
# template del modelo (tokenizer.chat_template en el GGUF).
#
# Solucion: inyectar las definiciones de tools en el system prompt usando
# el formato <tools> que Qwen2.5 entiende nativamente, y parsear las
# respuestas <tool_call> del texto generado.
# ────────────────────────────────────────────────────────────────────

# Tools como string JSON por tool para inyectar en el system prompt
# (formato nativo Qwen2.5: cada tool como JSON individual en <tools>).
_TOOLS_LINES = "\n".join([
    json.dumps(tool_def, ensure_ascii=False)
    for tool_def in [
        {
            "type": "function",
            "function": {
                "name": "get_price",
                "description": (
                    "USAR para PREGUNTAS DE PRECIO. "
                    "Cuando el agricultor pregunte por el valor de un producto agricola, "
                    "por cuanto cuesta, cuanto vale, a como esta, o mencione un producto "
                    "(papa, tomate, cebolla, lechuga, zanahoria, etc). "
                    "Ej: 'a cuanto esta la papa', 'cuanto cuesta el kilo de tomate', "
                    "'precio de la cebolla en Lo Valledor'."
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
                    "required": ["producto"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_price_history",
                "description": (
                    "USAR para PRECIOS PASADOS o VARIACION de precio. "
                    "Cuando el agricultor pregunte cuanto ESTABA un producto, "
                    "el precio de la semana pasada, de ayer, de hace unos dias, "
                    "o si el precio subio o bajo. "
                    "Ej: 'a cuanto estaba la papa la semana pasada', "
                    "'cuanto valia el tomate ayer', 'ha subido la cebolla?'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "producto": {
                            "type": "string",
                            "description": "Nombre del producto en singular (ej: papa, tomate, lechuga, cebolla)",
                        },
                        "dias": {
                            "type": "integer",
                            "description": (
                                "Cuantos dias hacia atras comparar "
                                "(7 = semana pasada, 1 = ayer, 30 = mes pasado). Default: 7."
                            ),
                        },
                    },
                    "required": ["producto"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": (
                    "USAR para PREGUNTAS DE CLIMA. "
                    "Cuando el agricultor pregunte por el clima, la temperatura, si va a "
                    "llover, el pronostico del tiempo, etc. "
                    "Usa coordenadas de Traiguen (-38.23, -72.68) si no especifica ubicacion. "
                    "Ej: 'como esta el clima', 'va a llover hoy', 'temperatura en Traiguen'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "lat": {
                            "type": "number",
                            "description": "Latitud en grados decimales (-90 a 90). Default: -38.23 para Traiguen.",
                        },
                        "lon": {
                            "type": "number",
                            "description": "Longitud en grados decimales (-180 a 180). Default: -72.68 para Traiguen.",
                        },
                    },
                    "required": ["lat", "lon"],
                },
            },
        },
    ]
])

# Seccion de tools en formato nativo Qwen2.5 para inyectar en system prompt.
# El modelo espera las definiciones dentro de <tools></tools>:
#   <tools>
#   {"type": "function", "function": {...}}
#   {"type": "function", "function": {...}}
#   </tools>
# Y las llamadas como <tool_call>{"name": "...", "arguments": {...}}</tool_call>.
_TOOLS_SECTION = f"""

# Tools

You may call one or more functions to assist with the user query.

You are provided with function signatures within <tools></tools> XML tags:
<tools>
{_TOOLS_LINES}
</tools>

For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:
<tool_call>
{{"name": <function-name>, "arguments": <args-json-object>}}
</tool_call>

When you receive a <tool_response>, use that data to answer the user in natural language."""

# Mantener TOOLS como lista de tool definitions para tests y compatibilidad.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_price",
            "description": (
                "USAR para PREGUNTAS DE PRECIO. "
                "Cuando el agricultor pregunte por el valor de un producto agricola, "
                "por cuanto cuesta, cuanto vale, a como esta, o mencione un producto "
                "(papa, tomate, cebolla, lechuga, zanahoria, etc). "
                "Ej: 'a cuanto esta la papa', 'cuanto cuesta el kilo de tomate', "
                "'precio de la cebolla en Lo Valledor'."
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
            "name": "get_price_history",
            "description": (
                "USAR para PRECIOS PASADOS o VARIACION de precio. "
                "Cuando el agricultor pregunte cuanto ESTABA un producto, "
                "el precio de la semana pasada, de ayer, de hace unos dias, "
                "o si el precio subio o bajo. "
                "Ej: 'a cuanto estaba la papa la semana pasada', "
                "'cuanto valia el tomate ayer', 'ha subido la cebolla?'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "producto": {
                        "type": "string",
                        "description": "Nombre del producto en singular (ej: papa, tomate, lechuga, cebolla)",
                    },
                    "dias": {
                        "type": "integer",
                        "description": (
                            "Cuantos dias hacia atras comparar "
                            "(7 = semana pasada, 1 = ayer, 30 = mes pasado). Default: 7."
                        ),
                    },
                },
                "required": ["producto"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": (
                "USAR para PREGUNTAS DE CLIMA. "
                "Cuando el agricultor pregunte por el clima, la temperatura, si va a "
                "llover, el pronostico del tiempo, etc. "
                "Usa coordenadas de Traiguen (-38.23, -72.68) si no especifica ubicacion. "
                "Ej: 'como esta el clima', 'va a llover hoy', 'temperatura en Traiguen'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {
                        "type": "number",
                        "description": "Latitud en grados decimales (-90 a 90). Default: -38.23 para Traiguen.",
                    },
                    "lon": {
                        "type": "number",
                        "description": "Longitud en grados decimales (-180 a 180). Default: -72.68 para Traiguen.",
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


def preload_model() -> None:
    """Pre-carga el modelo LLM en background para evitar cold start en el primer request.

    Llamar desde el ciclo de vida de FastAPI (startup) para que el modelo
    esté listo antes de que llegue la primera consulta. En VPS CX43 tarda
    ~6s cargar el GGUF de 3GB en RAM.

    No bloquea: dispara la carga en un thread daemon. Si falla, el error
    queda en _model_error y answer() usara mock en desarrollo.
    """
    threading.Thread(target=_get_model, daemon=True, name="llm-preload").start()


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

        try:
            from llama_cpp import Llama
        except ImportError:
            _model_error = "llama-cpp-python no instalado"
            _model_loaded = True  # Permanente: sin reinstalar no se arregla
            logger.warning("llama-cpp-python no instalado — LLM funcionando en modo mock")
            return None

        model_path = settings.llm_model_path
        if not model_path or not os.path.isfile(model_path):
            _model_error = f"Modelo LLM no encontrado en {model_path}"
            logger.warning(
                "Modelo LLM no encontrado en %s — LLM funcionando en modo mock. "
                "Se reintentara en el proximo request.",
                model_path,
            )
            return None  # NO setea _model_loaded — permite retry cuando el archivo llegue

        try:
            logger.info("Cargando modelo LLM desde %s ...", model_path)
            _model = Llama(
                model_path=model_path,
                n_ctx=_N_CTX,
                n_threads=4,
                verbose=False,
            )
            _model_loaded = True  # Solo en exito
            logger.info("Modelo LLM cargado — n_ctx=%d", _N_CTX)
        except Exception as exc:
            _model_error = f"Error al cargar modelo: {exc}"
            logger.exception(
                "Error al cargar modelo LLM — se reintentara en el proximo request"
            )
            return None  # NO setea _model_loaded — permite retry si fue OOM transitorio

    return _model


# ── Tool dispatcher ─────────────────────────────────────────────────

# Tipos para la tabla de herramientas.
# Acepta tanto sync (get_price_for_llm) como async (get_weather).
ToolHandler = Callable[..., object]


def _get_tool_handlers() -> dict[str, ToolHandler]:
    """Devuelve el diccionario de handlers para cada tool del whitelist.

    Los imports son lazy para evitar dependencias circulares y permitir
    que el modulo llm_service.py sea importable sin DB ni servicios.
    """
    from app.services.odepa_service import (
        get_price_for_llm,
        get_price_history_for_llm,
    )
    from app.services.weather_service import get_weather

    return {
        "get_price": get_price_for_llm,
        "get_price_history": get_price_history_for_llm,
        "get_weather": get_weather,
    }


def _filter_handler_args(
    handler: Callable[..., object],
    arguments: dict[str, object],
) -> dict[str, object]:
    """Filtra argumentos contra la firma real del handler.

    El LLM puede alucinar params extra (ej: 'unidad': 'kilo') que el handler
    no acepta, porque los handlers tienen firma estricta sin **kwargs. Sin este
    filtro, cualquier arg inventado lanza TypeError en runtime. Descartamos los
    desconocidos en vez de propagar el error: el handler solo recibe lo que sabe
    manejar.
    """
    params = inspect.signature(handler).parameters
    # Si el handler acepta **kwargs, todo es valido.
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return dict(arguments)
    return {k: v for k, v in arguments.items() if k in params}


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

    # Filtrar argumentos alucinados por el LLM contra la firma real del handler.
    # Evita TypeError cuando el LLM inventa params que el handler no acepta.
    valid_args = _filter_handler_args(handler, arguments)

    try:
        # Las tools de precio necesitan session de DB. Se la pasamos como kwarg.
        if name in ("get_price", "get_price_history"):
            from app.core.database import SessionLocal

            # Completar defaults para argumentos vacios que el LLM no especifico.
            # Si el producto esta vacio, no podemos consultar nada -> fallback.
            if not valid_args.get("producto") or not str(valid_args.get("producto", "")).strip():
                return (
                    "No entendi que producto queres consultar. "
                    "¿Podrias repetir el nombre del producto?"
                )
            # Mercado/dias son opcionales: cada handler aplica su default.

            session = SessionLocal()
            try:
                # Llamada síncrona en thread pool para no bloquear event loop.
                result = await asyncio.to_thread(
                    handler,
                    session=session,
                    **valid_args,
                )
            finally:
                session.close()
        else:
            # get_weather es async
            if asyncio.iscoroutinefunction(handler):
                result = await handler(**valid_args)
            else:
                result = await asyncio.to_thread(handler, **valid_args)

        logger.info("Tool %s ejecutada — args=%s", name, valid_args)
        return str(result)
    except (RuntimeError, ValueError, OSError, SQLAlchemyError) as exc:
        logger.exception("Error ejecutando tool %s: %s", name, exc)
        return "Hubo un error al consultar ese dato. ¿Probamos con otro?"


# ── Tool Calling loop ───────────────────────────────────────────────


def _parse_tool_calls(response: object) -> list[dict[str, object]]:
    """Extrae tool calls de una respuesta del LLM.

    Args:
        response: Respuesta completa de create_chat_completion.
                  Se acepta object porque llama-cpp retorna tipos
                  internos que no son dict[str, object] puro.

    Returns:
        Lista de tool calls (cada una con name y arguments).
    """
    if not isinstance(response, dict):
        return []
    choices = response.get("choices", [])
    if not isinstance(choices, list) or not choices:
        return []
    first = choices[0]
    if not isinstance(first, dict):
        return []
    message = first.get("message", {})
    if not isinstance(message, dict):
        return []
    tool_calls = message.get("tool_calls", [])
    if not isinstance(tool_calls, list):
        return []
    return tool_calls


def _parse_content(response: object) -> str:
    """Extrae el texto de contenido de una respuesta del LLM.

    Args:
        response: Respuesta completa de create_chat_completion.
                  Se acepta object porque llama-cpp retorna tipos
                  internos que no son dict[str, object] puro.

    Returns:
        Texto de la respuesta, o cadena vacía si no hay.
    """
    if not isinstance(response, dict):
        return ""
    choices = response.get("choices", [])
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message", {})
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    return str(content).strip() if content else ""


def _parse_text_tool_calls(content: str) -> list[dict[str, object]]:
    """Parsea tool calls desde el texto generado por Qwen2.5.

    Qwen2.5 genera tool calls en formato nativo como texto plano:
      <tool_call>
      {"name": "get_price", "arguments": {"producto": "papa", "mercado": "Lo Valledor"}}
      </tool_call>

    Esta funcion extrae TODAS las ocurrencias de <tool_call>...</tool_call>
    y devuelve una lista con formato compatible con el tool dispatcher.

    Args:
        content: Texto generado por el LLM.

    Returns:
        Lista de dicts con keys "function" -> {"name": ..., "arguments": ...}.
        Vacia si no hay tool calls en el texto.
    """
    import re

    if not content:
        return []

    # Patron: extrae <tool_call>...</tool_call> con su contenido JSON
    pattern = re.compile(
        r"<tool_call>\s*({.*?})\s*</tool_call>",
        re.DOTALL,
    )
    matches = pattern.findall(content)
    if not matches:
        return []

    tool_calls: list[dict[str, object]] = []
    for match in matches:
        try:
            parsed = json.loads(match.strip())
        except json.JSONDecodeError:
            logger.warning("JSON invalido dentro de <tool_call>: %.100s", match.strip())
            continue

        name = parsed.get("name", "")
        arguments = parsed.get("arguments", {})
        if not name or not isinstance(name, str):
            logger.warning("Tool call sin 'name' valido: %.100s", match.strip())
            continue

        tool_calls.append({
            "function": {
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            },
        })

    return tool_calls


def _strip_tool_tags(text: str) -> str:
    """Elimina tags XML de tool calling del texto de respuesta.

    Qwen2.5 puede dejar tags residuales <tool_call>, <tool_response>,
    <tools>, <|im_end|> en el texto generado. Esta funcion los limpia
    para que Piper TTS no los lea como parte de la respuesta.

    Args:
        text: Texto potencialmente sucio con tags XML.

    Returns:
        Texto limpio, o cadena vacia si solo habia tags.
    """
    import re

    cleaned = text.strip()
    # Eliminar bloques <tool_call>...</tool_call>
    cleaned = re.sub(r"<tool_call>.*?</tool_call>", "", cleaned, flags=re.DOTALL)
    # Eliminar bloques <tool_response>...</tool_response>
    cleaned = re.sub(r"<tool_response>.*?</tool_response>", "", cleaned, flags=re.DOTALL)
    # Eliminar bloques <tools>...</tools>
    cleaned = re.sub(r"<tools>.*?</tools>", "", cleaned, flags=re.DOTALL)
    # Eliminar tags individuales: <|im_start|>, <|im_end|>
    cleaned = re.sub(r"<\|im_start\|>\w*", "", cleaned)
    cleaned = re.sub(r"<\|im_end\|>", "", cleaned)
    # Limpiar espacios multiples y saltos de linea al inicio/fin
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return cleaned


# ── Fallback keyword detection (Fix #4) ──────────────────────────────
# Cuando el LLM no genera <tool_call>, detectamos keywords en la consulta
# para forzar la tool correcta. Esto cubre el ~30% de consultas donde el
# modelo Qwen2.5-3B Q4 no obedece la instruccion de "SIEMPRE usa una tool".

# Patrones que indican que el LLM respondio sin usar herramientas.
_GENERIC_RESPONSE_PATTERNS = [
    "no tengo", "no entiendo", "no conozco", "no sé", "no se",
    "reformul", "podrías repetir", "no dispongo", "sin información",
    "sin datos", "no cuento con", "no puedo responder",
    "lo siento", "disculpa", "no estoy seguro",
]

# Productos agricolas chilenos mas comunes (ODEPA). Para fallback de
# keyword detection cuando el LLM no llama get_price.
_COMMON_PRODUCTS = [
    "papa", "tomate", "cebolla", "lechuga", "zanahoria", "ajo",
    "palta", "naranja", "limón", "limon", "manzana", "pera",
    "kiwi", "uva", "durazno", "ciruela", "frutilla", "sandía",
    "sandia", "melón", "melon", "repollo", "acelga", "espinaca",
    "brocoli", "brócoli", "coliflor", "zapallo", "camote",
    "betarraga", "rabanito", "rúcula", "rucula", "cilantro",
    "perejil", "apio", "puerro", "choclo", "poroto", "arveja",
    "haba", "pepino", "pimentón", "pimenton", "ají", "aji",
    "maíz", "maiz", "trigo", "arroz",
]


def _is_generic_response(text: str) -> bool:
    """Detecta si la respuesta del LLM es generica (no uso herramientas).

    Si el LLM responde con "no tengo informacion", "no entiendo",
    "reformula", etc., es señal de que no intento usar tools.
    """
    lower = text.lower()
    return any(p in lower for p in _GENERIC_RESPONSE_PATTERNS)


def _extract_product_from_query(query: str) -> str | None:
    """Extrae el nombre de un producto agricola de la consulta por keyword.

    Busca nombres de productos en el texto. Si el agricultor dice
    "a cuanto esta el kilo de tomate", detecta "tomate".
    """
    query_lower = query.lower()
    # Ordenar por largo descendente para que "pimentón" matchee antes que "pimenton"
    # y "sandía" antes que "sandia".
    for product in sorted(_COMMON_PRODUCTS, key=len, reverse=True):
        if product in query_lower:
            return product
    return None


async def _force_keyword_tool(query_text: str) -> str | None:
    """Forza tool call por keyword detection cuando el LLM no llama tools.

    Detecta si la consulta menciona un producto agricola o el clima,
    y ejecuta la tool correspondiente directamente sin pasar por el LLM.

    Returns:
        Resultado textual de la tool, o None si no se detecta keyword.
    """
    from app.core.database import SessionLocal
    from app.services.odepa_service import (
        get_price_for_llm,
        get_price_history_for_llm,
    )

    q = query_text.strip().lower()

    # 1. Detectar productos agricolas en la consulta.
    product = _extract_product_from_query(q)
    if product:
        # Keywords de precio pasado: "estaba", "semana pasada", "ayer", etc.
        # -> historial en vez de precio actual.
        historia_kw = [
            "estaba", "semana pasada", "ayer", "hace ", "valia", "valía",
            "ha subido", "ha bajado", "subio", "subió", "bajo el precio",
            "bajó", "antes",
        ]
        es_historia = any(kw in q for kw in historia_kw)

        session = SessionLocal()
        try:
            # Llamada en thread pool: las tools de precio son sincronas
            # (query SQLite) y no deben bloquear el event loop.
            if es_historia:
                result = await asyncio.to_thread(
                    get_price_history_for_llm, session, producto=product
                )
            else:
                result = await asyncio.to_thread(
                    get_price_for_llm, session, producto=product
                )
            # Solo retornar si encontro datos reales (no "No tengo datos...").
            if not result.startswith("No tengo datos"):
                logger.info(
                    "Fallback tool forzado: %s(producto=%s) — query=%.100s",
                    "get_price_history" if es_historia else "get_price",
                    product, query_text,
                )
                return result
        except SQLAlchemyError as exc:
            # Fire-and-forget: un error de DB (database is locked, disk I/O)
            # no debe romper el pipeline. Se loguea y se cae al bloque de clima.
            logger.warning("Error DB en fallback precio: %s", exc)
        finally:
            session.close()

    # 2. Detectar keywords de clima.
    clima_kw = [
        "clima", "tiempo", "temperatura", "lluvia", "lloviendo",
        "frio", "calor", "humedad", "viento", "pronóstico", "pronostico",
        "nublado", "despejado",
    ]
    if any(kw in q for kw in clima_kw):
        from app.services.weather_service import get_weather
        try:
            # Traiguen como default si no hay coordenadas en la consulta.
            result = await get_weather(lat=-38.23, lon=-72.68)
            if result:
                logger.info(
                    "Fallback tool forzado: get_weather(lat=-38.23, lon=-72.68) — query=%.100s",
                    query_text,
                )
                return str(result)
        except (RuntimeError, ValueError, OSError) as exc:
            logger.warning("Error en fallback clima: %s", exc)

    return None


# ── Construccion de mensajes ─────────────────────────────────────────


def _build_messages(user_query: str, history: list[dict[str, object]]) -> list[dict[str, object]]:
    """Construye la lista de mensajes para el LLM.

    Incluye las definiciones de tools en formato nativo Qwen2.5 (<tools> XML)
    dentro del system prompt, para que el modelo genere <tool_call> como texto.

    Formato: system (con tools) + history + user.
    """
    system_content = SYSTEM_PROMPT + _TOOLS_SECTION
    messages: list[dict[str, object]] = [
        {"role": "system", "content": system_content},
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
    1. Construir mensajes (system prompt con tools en formato Qwen2.5 nativo).
    2. Llamar al LLM SIN tools parameter (usa <tool_call> en texto plano).
    3. Si el texto contiene <tool_call> -> parsear -> ejecutar (whitelist)
       -> devolver resultado como <tool_response> -> generar respuesta final.
    4. Si no hay <tool_call> -> retornar contenido como respuesta final.
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
        for _iteration in range(MAX_TOOL_ITERATIONS):
            # NO pasar tools/tool_choice — Qwen2.5 genera <tool_call> como texto
            # nativo cuando las definiciones estan en el system prompt.
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    model.create_chat_completion,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=0.0,
                    max_tokens=256,
                ),
                timeout=_GENERATION_TIMEOUT,
            )

            content = _parse_content(response)
            if not content:
                # Si el LLM no genero contenido, reintentar con instruccion directa.
                messages.append({
                    "role": "system",
                    "content": "Responde al usuario en español chileno con frases cortas.",
                })
                continue

            # Intentar extraer tool calls del texto (formato nativo Qwen2.5).
            tool_calls = _parse_text_tool_calls(content)
            if not tool_calls:
                # Sin tool calls -> posible respuesta final del LLM.
                cleaned = _strip_tool_tags(content)
                if cleaned:
                    # Fix #4: si el LLM respondio con texto generico
                    # ("no tengo datos", "reformula") sin llamar tools,
                    # forzar tool call por keyword detection.
                    if _iteration == 0 and _is_generic_response(cleaned):
                        forced = await _force_keyword_tool(query_text)
                        if forced:
                            # Inyectar el tool call + respuesta para que
                            # el LLM lo formatee en la siguiente iteracion.
                            messages.append({
                                "role": "assistant",
                                "content": content,
                            })
                            messages.append({
                                "role": "user",
                                "content": (
                                    "<tool_response>\n"
                                    f"{forced}\n"
                                    "</tool_response>"
                                ),
                            })
                            continue
                    return cleaned
                continue

            # El LLM quiere ejecutar herramientas.
            # Agregar mensaje del asistente con el tool call (como texto).
            messages.append({
                "role": "assistant",
                "content": content,
            })

            for tc in tool_calls:
                fn_info_raw = tc.get("function", {})
                if not isinstance(fn_info_raw, dict):
                    continue
                fn_name = str(fn_info_raw.get("name", ""))
                fn_args_str = str(fn_info_raw.get("arguments", "{}"))

                # Whitelist enforcement: solo get_price y get_weather.
                if fn_name not in WHITELIST_TOOLS:
                    logger.warning("Tool fuera de whitelist: %s — enviando fallback", fn_name)
                    messages.append({
                        "role": "user",
                        "content": f"<tool_response>\n{FALLBACK_TEXT}\n</tool_response>",
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

                # Envolver resultado en <tool_response> (formato nativo Qwen2.5).
                messages.append({
                    "role": "user",
                    "content": f"<tool_response>\n{tool_result}\n</tool_response>",
                })

        # Si llegamos aca, se agotaron las iteraciones.
        logger.warning(
            "Tool Calling loop agoto %d iteraciones — query=%.100s",
            MAX_TOOL_ITERATIONS,
            query_text,
        )
        # Ultimo intento: forzar respuesta sin tools.
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
                cleaned = _strip_tool_tags(content)
                if cleaned:
                    return cleaned
        except (TimeoutError, RuntimeError, OSError, ValueError) as exc:
            logger.warning("Error en respuesta final: %s", exc)

        return FALLBACK_TEXT

    except TimeoutError:
        logger.warning("Timeout del LLM (%ss) — query=%.100s", _GENERATION_TIMEOUT, query_text)
        return "Estoy teniendo problemas para responder. ¿Podrías preguntar de nuevo más breve?"
    except (json.JSONDecodeError, RuntimeError, OSError) as exc:
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
