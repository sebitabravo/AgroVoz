"""Servicio LLM: Qwen2.5-3B Q4 con Tool Calling y whitelist estricta.

Carga el modelo cuantizado via llama-cpp-python (CPU, 4-bit).
Singleton con lazy loading: el modelo se carga en la primera llamada,
no al importar el modulo.

Tools disponibles (whitelist):
  - get_price(producto, mercado)              -> odepa_service.get_price_for_llm()
  - get_price_history(producto, dias)         -> odepa_service.get_price_history_for_llm()
  - calculate_sale_value(producto, kg, mercado) -> odepa_service.calculate_sale_value_for_llm()
   - get_weather(lat, lon)                      -> weather_service.get_weather()
   - get_pronostico(comuna, dias)               -> weather_service.get_pronostico()
   - get_clima_historico(comuna, metrica)       -> weather_service.get_clima_historico()
   - get_clima_historico_multianual(comuna, anos, temporada, anio, metrica)
                                                -> weather_service.get_clima_historico_multianual()

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
import re
import threading
import time
from collections.abc import Callable
from typing import cast

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.services.llm_keywords import (
    _force_keyword_tool,
    _is_generic_response,
)
from app.services.llm_worker import (
    LlmWorkerBusyError,
    LlmWorkerConfig,
    LlmWorkerCrashedError,
    LlmWorkerManager,
    LlmWorkerProtocolError,
    LlmWorkerRemoteError,
    LlmWorkerTimeoutError,
    LlmWorkerUnavailableError,
)
from app.services.prompt_builder import build_system_prompt

logger = logging.getLogger(__name__)

# ── System prompt — construido desde prompt_builder (issue #190) ────

SYSTEM_PROMPT = build_system_prompt()

# Texto de fallback cuando el LLM intenta una tool fuera del whitelist.
FALLBACK_TEXT = "No tengo ese dato, pero puedo consultarte el precio en ODEPA o el clima."

# Texto cuando el LLM no genera respuesta.
NO_RESPONSE_TEXT = "No entendí tu consulta. ¿Podrías reformularla?"

# Tool names permitidas. Cualquier otra -> fallback.
WHITELIST_TOOLS = frozenset(
    {
        "get_price",
        "get_price_history",
        "calculate_sale_value",
        "calculate_margin",
        "get_price_spread",
        "get_weather",
        "get_pronostico",
        "get_clima_historico",
        "get_clima_historico_multianual",
        "search_corpus",
        "register_expense",
        "register_parcela",
        "get_parcelas",
        "get_regla_agronomica",
        "get_link_resumen",
    }
)

# Máximo de iteraciones del Tool Calling loop (previene loops infinitos).
MAX_TOOL_ITERATIONS = 3

# Timeout de generación por llamada al LLM (segundos).
#
# Bajado de 60s a 25s. Con 60s el peor caso medido fue un pipeline de 86s
# (Whisper + timeout completo del LLM + fallback), contra un objetivo de 15s:
# el productor esperaba un minuto y medio para recibir "no te entendi". Mas
# vale cortar antes y dejar que responda el fallback por keywords, que entrega
# datos reales de ODEPA en vez de un mensaje generico.
#
# 25s sigue dando aire al cold start del modelo y a una vuelta del tool calling
# loop. Lo que hace que el caso comun entre en presupuesto no es este timeout,
# sino el fast-path deterministico y el cache de prompt.
_GENERATION_TIMEOUT = 25.0
_LLM_CIRCUIT_COOLDOWN_SECONDS = 90.0
_LLM_BUSY_TEXT = "Estoy procesando otra consulta ahora. ¿Podrías intentar de nuevo en un momento?"

# Contexto máximo del modelo (tokens). Con las 10 tools actuales, el system
# prompt completo + tools ya
# ocupa ~2771 tokens medidos con el tokenizer real de Qwen2.5 — n_ctx=1024
# y n_ctx=2048 NO alcanzan ni para el primer prompt (ValueError instantaneo
# de llama-cpp-python, no timeout). Con tool_response de search_corpus
# (peor caso, ~360 tokens) la segunda vuelta del loop necesita ~3171
# tokens, asi que 3072 tampoco alcanza. 4096 es el minimo medido que no
# revienta.
# RIESGO DE LATENCIA SIN VALIDAR EN VPS (gate del Issue #100, overrideado):
# medido en Apple M3 con Metal (mejor caso posible, no representativo del
# VPS CX43 sin GPU): 1a llamada ~36s + 2a llamada ~10s = ~46s solo LLM,
# vs target <15s E2E total (que ademas incluye Whisper + TTS). Validar en
# el VPS real antes del piloto de Traiguen; puede requerir comprimir el
# prompt (menos tools/texto) en vez de, o ademas de, subir n_ctx.
_N_CTX = 4096

# Hilos para inferencia: un hilo por nucleo REAL disponible, nunca mas.
# El valor anterior era max(cpu_count, 4), que en el piso soportado de 1 vCPU
# lanzaba 4 hilos sobre 1 core: los hilos se pelean el mismo core y la
# inferencia va mas lenta que con 1 solo. El tope de 8 evita thrash de
# scheduler en maquinas grandes sin beneficio real para un 3B en CPU.
_N_THREADS: int = min(os.cpu_count() or 4, 8)

# Tamano de lote para prompt eval. El prompt fijo (system + 10 tools) ronda los
# 2700 tokens y se evalua en lotes: un batch mas grande procesa mas tokens por
# pasada y reduce el overhead por lote, que es donde se va el tiempo cuando hay
# poca CPU. Ver _preload_prompt_cache() para el otro lado del problema.
_N_BATCH = 512

# Capacidad del cache de prompt en RAM (384 MB). Acotado a proposito: el piso
# soportado son 6 GB y el GGUF ya ocupa ~2 GB. Alcanza de sobra para el prefijo
# fijo, que es el unico que se repite.
_PROMPT_CACHE_BYTES = 384 * 1024 * 1024

# ── Cache de resultados de tools ──────────────────────────────────
# Los precios ODEPA se actualizan una vez al dia (cron 06:00 AM).
# Cachear resultados de get_price/get_price_history/get_weather evita
# llamadas redundantes al LLM para la misma consulta repetida.
# TTL corto (30 min) como safety net; el sync de ODEPA limpia el cache.
# Clave: hash de (tool_name, frozenset(params.items())).


class ToolResultCache:
    """Cache en memoria de resultados de tool calls con TTL.

    Evita llamadas redundantes al LLM cuando el mismo producto+mercado
    se consulta repetidamente. Los precios ODEPA se actualizan una vez
    al dia, asi que cachear por 30 min es seguro.
    """

    def __init__(self, ttl_seconds: int = 1800) -> None:
        self._cache: dict[int, tuple[float, str]] = {}
        self._ttl = ttl_seconds

    def _key(self, tool_name: str, params: dict[str, object]) -> int:
        return hash((tool_name, frozenset(params.items())))

    def get(self, tool_name: str, **params: object) -> str | None:
        """Retorna resultado cacheado si existe y no expiro."""
        import time as _t

        entry = self._cache.get(self._key(tool_name, params))
        if entry is None:
            return None
        stored_at, result = entry
        if _t.monotonic() - stored_at > self._ttl:
            del self._cache[self._key(tool_name, params)]
            return None
        if tool_name in WHITELIST_TOOLS:
            logger.debug("Cache hit — tool=%s", tool_name)
        else:
            logger.debug("Cache hit")
        return result

    def set(self, tool_name: str, result: str, **params: object) -> None:
        """Guarda resultado en cache con timestamp."""
        import time as _t

        self._cache[self._key(tool_name, params)] = (_t.monotonic(), result)

    def clear(self) -> None:
        """Limpia todo el cache (llamado tras sync de ODEPA)."""
        self._cache.clear()
        logger.info("ToolResultCache limpiado — sync ODEPA")


# Singleton del cache, compartido entre requests.
_tool_cache = ToolResultCache()


def clear_tool_result_cache() -> None:
    """Limpia el cache de resultados de tools. Llamado tras sync de ODEPA."""
    _tool_cache.clear()


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

# Mantener TOOLS como lista de tool definitions (fuente única de verdad).
# _TOOLS_SECTION se genera desde esta lista para inyectar en system prompt.
# Tipado explicito (list[dict[str, object]]) para que sea compatible con
# openrouter_service.chat_completion_with_tools(tools=...).
TOOLS: list[dict[str, object]] = [
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
            "name": "get_price_spread",
            "description": (
                "USAR para COMPARAR PRECIOS entre mercados: rango, diferencia "
                "o variacion de precio de un producto. Muestra minimo, maximo "
                "y promedio. Ej: 'cuanto varia la papa entre mercados'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "producto": {
                        "type": "string",
                        "description": "Producto en singular (ej: papa, tomate)",
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
    {
        "type": "function",
        "function": {
            "name": "get_pronostico",
            "description": (
                "PRONOSTICO: clima de MANANA o proximos dias. "
                "NO para clima de ahora (get_weather) ni pasado (get_clima_historico). "
                "Ej: 'va a llover manana', 'va a helar', 'como viene el tiempo'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "comuna": {
                        "type": "string",
                        "description": (
                            "Nombre de la comuna chilena (ej: Traiguen, Temuco, Santiago). "
                            "Usar Traiguen si no se especifica ubicacion."
                        ),
                    },
                    "dias": {
                        "type": "integer",
                        "description": (
                            "Cuantos dias de pronostico entregar, de 1 a 3. "
                            "Usar 1 si preguntan solo por manana, 2 por defecto."
                        ),
                    },
                },
                "required": ["comuna"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_clima_historico",
            "description": "Clima histórico de un año: temperatura, lluvia y heladas. No recomienda.",
            "parameters": {
                "type": "object",
                "properties": {
                    "comuna": {
                        "type": "string",
                        "description": "Comuna chilena. Default: Traiguén.",
                    },
                    "metrica": {
                        "type": "string",
                        "description": "Métrica opcional: temperatura, lluvia o heladas.",
                    },
                },
                "required": ["comuna"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_clima_historico_multianual",
            "description": "Compara clima histórico por año o temporada (OpenMeteo): temperatura, lluvia y heladas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "comuna": {
                        "type": "string",
                        "description": "Comuna.",
                    },
                    "anos": {
                        "type": "integer",
                        "description": "Años (1-5).",
                    },
                    "temporada": {
                        "type": "string",
                        "description": "Temporada opcional.",
                    },
                    "anio": {
                        "type": "integer",
                        "description": "Año final opcional.",
                    },
                    "metrica": {
                        "type": "string",
                        "description": "Métrica opcional.",
                    },
                },
                "required": ["comuna"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_sale_value",
            "description": (
                "USAR para CALCULAR CUANTO RECIBIRA el agricultor por una venta. "
                "Cuando el agricultor mencione una cantidad de kilos a vender "
                "(voy a vender 30 kilos de papa, a cuanto recibo por 50 kilos, "
                "cuanto me pagan por 100 kilos de tomate). "
                "EL CALCULO LO HACE LA HERRAMIENTA: nunca lo hagas tu. "
                "Ej: 'voy a vender 30 kilos de papa', 'a cuanto recibo por 50 kilos de tomate'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "producto": {
                        "type": "string",
                        "description": "Nombre del producto en singular (ej: papa, tomate, lechuga, cebolla)",
                    },
                    "cantidad_kg": {
                        "type": "string",
                        "description": (
                            "Cantidad de kilos a vender como string (ej: '30', '50', '100.5'). "
                            "La herramienta valida y convierte a Decimal."
                        ),
                    },
                    "mercado": {
                        "type": "string",
                        "description": "Nombre del mercado mayorista (ej: Lo Valledor, La Vega, Talca). Opcional.",
                    },
                },
                "required": ["producto", "cantidad_kg"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_margin",
            "description": (
                "USAR para CALCULAR MARGEN de una venta YA REALIZADA. "
                "Cuando el agricultor diga que ya VENDIO o ya RECIBIO dinero por "
                "su cosecha (vendi, vendiste, acabo de vender, recibi por, me pagaron). "
                "Pide EXPLICITAMENTE: producto, cantidad, unidad (kilo/saco/malla/caja/tonelada) "
                "y monto total recibido. "
                "NO usar para calcular cuanto recibira (usa calculate_sale_value). "
                "Ej: 'vendi 3 sacos de papa a 150 lucas', "
                "'me pagaron 250 mil por 4 mallas de tomate', "
                "'recibi 100 lucas por 2 cajas de cebolla'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "producto": {
                        "type": "string",
                        "description": "Nombre del producto en singular (ej: papa, tomate, lechuga, cebolla)",
                    },
                    "cantidad": {
                        "type": "string",
                        "description": (
                            "Cantidad vendida como string (ej: '3', '100', '2.5'). "
                            "La herramienta valida y convierte a Decimal."
                        ),
                    },
                    "unidad": {
                        "type": "string",
                        "description": (
                            "Unidad de medida: kilo, saco (50kg), malla (25kg), "
                            "caja (20kg), tonelada (1000kg). "
                            "Ej: 'saco', 'malla', 'caja', 'kilo', 'tonelada'."
                        ),
                    },
                    "precio_total": {
                        "type": "string",
                        "description": (
                            "Monto TOTAL recibido en pesos chilenos como string "
                            "(ej: '150000', '250000', '100000'). "
                            "La herramienta valida y convierte a Decimal."
                        ),
                    },
                    "mercado": {
                        "type": "string",
                        "description": "Nombre del mercado mayorista (ej: Lo Valledor, La Vega, Talca). Opcional.",
                    },
                },
                "required": ["producto", "cantidad", "unidad", "precio_total"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_corpus",
            "description": (
                "USAR para BUSCAR en documentos oficiales ODEPA. "
                "Cuando el agricultor pregunte por informacion de boletines, "
                "contexto del mercado agricola, tendencias de precios, "
                "definiciones del rubro o datos de los documentos oficiales. "
                "NO usar para precios actuales (usa get_price). "
                "CITA la fuente y fecha que devuelve la herramienta. "
                "Ej: 'que dice el boletin de la papa', "
                "'cual es la tendencia del mercado', "
                "'informacion sobre la papa en Chile'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "La consulta o pregunta del agricultor "
                            "para buscar en los documentos oficiales. "
                            "Ej: 'precio de la papa en ferias', "
                            "'produccion de papa en Chile', "
                            "'mercado mayorista papa'."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_expense",
            "description": (
                "USAR para REGISTRAR GASTOS del agricultor. "
                "Cuando reporte que GASTO, COMPRO o PAGO dinero en insumos, "
                "semillas, fertilizantes o transporte. "
                "Ej: 'gaste 50 lucas en semilla de papa', 'pague 100 lucas de flete'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "producto": {
                        "type": "string",
                        "description": "Producto o cultivo (ej: papa, tomate, general)",
                    },
                    "concepto": {
                        "type": "string",
                        "description": "En que gasto (ej: semilla, abono, flete)",
                    },
                    "monto": {
                        "type": "string",
                        "description": "Monto en pesos chilenos (ej: 50000, 50 lucas)",
                    },
                    "fecha": {
                        "type": "string",
                        "description": (
                            "Fecha del gasto: YYYY-MM-DD, DD/MM/YYYY, hoy o ayer. "
                            "Si no se menciona, omitir para usar hoy."
                        ),
                    },
                },
                "required": ["producto", "concepto", "monto"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_parcela",
            "description": (
                "USAR para REGISTRAR UNA PARCELA del agricultor. "
                "Cuando diga que TIENE, SIEMBRA o CULTIVA un terreno con un cultivo, "
                "superficie y comuna. "
                "Ej: 'tengo dos hectareas de papa en Traiguen', "
                "'sembre trigo en cinco hectareas en Victoria'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cultivo": {
                        "type": "string",
                        "description": "Cultivo de la parcela (ej: papa, trigo, avena)",
                    },
                    "superficie_ha": {
                        "type": "string",
                        "description": "Superficie en hectareas (ej: 2, 2.5)",
                    },
                    "comuna": {
                        "type": "string",
                        "description": "Comuna donde esta la parcela (ej: Traiguen)",
                    },
                },
                "required": ["cultivo", "superficie_ha", "comuna"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_parcelas",
            "description": (
                "USAR para CONSULTAR LAS PARCELAS ya registradas del agricultor. "
                "Cuando pregunte que parcelas tiene, cuantas hectareas declaro, "
                "o pida un resumen de sus terrenos. "
                "Ej: 'que parcelas tengo registradas', 'cuantas hectareas de papa tengo'."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_regla_agronomica",
            "description": (
                "USAR cuando el agricultor describa un SINTOMA o PROBLEMA de su cultivo, "
                "o pregunte CUANDO sembrar, cosechar o rotar. "
                "NUNCA improvises la respuesta: esta tool resuelve contra reglas ya citadas "
                "de INIA. Si no hay una regla que calce, dice que no tiene el dato. "
                "Ej: 'mis papas tienen manchas en las hojas', 'cuando siembro la papa', "
                "'que cultivo va antes de la papa'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sintoma": {
                        "type": "string",
                        "description": "Descripcion del problema o pregunta del agricultor",
                    },
                    "cultivo": {
                        "type": "string",
                        "description": "Cultivo mencionado (ej: papa). Opcional si no lo dijo.",
                    },
                },
                "required": ["sintoma"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_link_resumen",
            "description": (
                "USAR cuando el agricultor pida VER, MANDAR o ENVIAR un resumen, panel o link "
                "con sus datos (parcelas, alertas, comuna). "
                "Ej: 'mandame mi resumen', 'quiero ver mis datos', 'dame el link del panel'."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

# Subconjuntos de tools por tipo de consulta (TipoConsulta en schemas/variables).
# Las 10 definiciones juntas pesan ~2000 tokens y se re-inyectan en cada consulta:
# es el grueso del prompt y, con poca CPU, el grueso de la latencia. El pipeline
# ya clasifica la consulta ANTES de llamar al LLM (_extract_variables), asi que
# mandamos solo las tools del dominio consultado.
#
# "ambos" y "desconocido" reciben las 10: si no sabemos qué pregunta, recortar
# tools le sacaria capacidad al modelo. Solo recortamos cuando hay certeza.
#
# search_corpus va en ambos subconjuntos: responde dudas de contexto agricola
# que pueden aparecer junto a una consulta de precio o de clima.
_TOOLS_PRECIO = frozenset(
    {
        "get_price",
        "get_price_history",
        "get_price_spread",
        "calculate_sale_value",
        "calculate_margin",
        "register_expense",
        "search_corpus",
    }
)
_TOOLS_CLIMA = frozenset(
    {
        "get_weather",
        "get_pronostico",
        "get_clima_historico",
        "get_clima_historico_multianual",
        "search_corpus",
    }
)


# Tools apagadas por feature gate: no se anuncian. Ofrecer una tool que el
# servicio va a rechazar gasta prefijo en cada request y quema un round-trip
# completo del LLM, que en 1 vCPU es el cuello (#170).
_GATED_TOOLS: dict[str, Callable[[], bool]] = {
    "register_expense": lambda: settings.expense_tracking_enabled,
    "register_parcela": lambda: settings.parcela_tracking_enabled,
    "get_parcelas": lambda: settings.parcela_tracking_enabled,
    "get_regla_agronomica": lambda: settings.agronomic_rules_enabled,
    "get_link_resumen": lambda: settings.farmer_panel_enabled,
}


def _tool_is_offered(nombre: str) -> bool:
    """Indica si la tool puede anunciarse según su feature gate."""
    gate = _GATED_TOOLS.get(nombre)
    return gate is None or gate()


def _tool_names(definiciones: list[dict[str, object]]) -> list[str]:
    """Extrae los nombres de una lista de tool definitions."""
    return [str(cast("dict[str, object]", d["function"])["name"]) for d in definiciones]


def _offered_tools(nombres: frozenset[str] | None = None) -> list[dict[str, object]]:
    """Filtra ``TOOLS`` por subconjunto de intent y por feature gate."""
    return [
        tool_def
        for tool_def in TOOLS
        if (nombres is None or str(cast("dict[str, object]", tool_def["function"])["name"]) in nombres)
        and _tool_is_offered(str(cast("dict[str, object]", tool_def["function"])["name"]))
    ]


def _render_tools_section(nombres: frozenset[str] | None = None) -> str:
    """Arma la sección <tools> del system prompt en formato nativo Qwen2.5.

    Args:
        nombres: Tools a incluir. None = todas las habilitadas (fuente única:
            ``TOOLS``, menos las apagadas por feature gate).

    Returns:
        Bloque de texto listo para concatenar al system prompt.
    """
    definiciones = _offered_tools(nombres)
    lineas = "\n".join(json.dumps(tool_def, ensure_ascii=False) for tool_def in definiciones)
    return f"""

# Tools

You may call one or more functions to assist with the user query.

You are provided with function signatures within <tools></tools> XML tags:
<tools>
{lineas}
</tools>

For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:
<tool_call>
{{"name": <function-name>, "arguments": <args-json-object>}}
</tool_call>

When you receive a <tool_response>, use that data to answer the user in natural language."""


_TOOLS_SECTION = _render_tools_section()

# Precomputado por tipo: son strings constantes, asi el prefijo del prompt es
# byte a byte estable y el cache KV puede reusarlo entre consultas del mismo
# tipo. Generarlo por request romperia el cache.
_TOOLS_SECTION_POR_TIPO: dict[str, str] = {
    "precio": _render_tools_section(_TOOLS_PRECIO),
    "clima": _render_tools_section(_TOOLS_CLIMA),
    "ambos": _TOOLS_SECTION,
    "desconocido": _TOOLS_SECTION,
}

# ── Singleton del worker aislado ───────────────────────────────────

_worker_manager: LlmWorkerManager | None = None
# Aliases legacy consumidos por monitor_service. Apuntan al manager, nunca a
# llama.cpp, y se mantienen hasta que ese monitor migre a is_model_available().
_model: LlmWorkerManager | None = None
_model_loaded = False
_model_lock = threading.Lock()
_model_error: str | None = None
_llm_circuit_lock = threading.Lock()
_llm_circuit_open_until = 0.0


class LlmGuardError(RuntimeError):
    """Error base de guardas de ejecución del LLM."""


class LlmBusyError(LlmGuardError):
    """El LLM ya está ejecutando otra inferencia."""


class LlmCircuitOpenError(LlmGuardError):
    """Circuit breaker activo: se evita usar LLM temporalmente."""


def preload_model() -> None:
    """Inicia en background el proceso que carga el modelo LLM.

    Llamar desde el ciclo de vida de FastAPI (startup) para que el modelo
    esté listo antes de que llegue la primera consulta. En VPS CX43 tarda
    ~6s cargar el GGUF de 3GB en RAM.

    FastAPI nunca importa ni ejecuta llama.cpp: el thread solo espera el
    handshake del proceso ``spawn``. Si falla, ``answer`` conserva su fallback.
    """
    threading.Thread(target=_get_model, daemon=True, name="llm-preload").start()


def _worker_config(model_path: str) -> LlmWorkerConfig:
    """Construye la configuración fija enviada al proceso hijo."""
    return LlmWorkerConfig(
        model_path=model_path,
        n_ctx=_N_CTX,
        n_threads=_N_THREADS,
        n_batch=_N_BATCH,
        prompt_cache_bytes=_PROMPT_CACHE_BYTES,
        request_timeout_seconds=_GENERATION_TIMEOUT,
    )


def _get_model() -> LlmWorkerManager | None:
    """Obtiene un worker saludable sin cargar llama.cpp en FastAPI."""
    global _worker_manager, _model, _model_loaded, _model_error

    with _model_lock:
        model_path = settings.llm_model_path
        if not model_path or not os.path.isfile(model_path):
            if _worker_manager is not None:
                _worker_manager.stop()
            _worker_manager = None
            _model = None
            _model_loaded = False
            _model_error = "model_not_found"
            logger.warning("Modelo LLM no encontrado — LLM en modo mock; se reintentara en el proximo request")
            return None

        if _worker_manager is None:
            _worker_manager = LlmWorkerManager(_worker_config(model_path))
        if not _worker_manager.is_healthy() and _is_llm_circuit_open():
            # No recargar ~2 GB para una request que el circuit breaker
            # rechazará inmediatamente; el primer request post-cooldown reinicia.
            return _worker_manager
        if _worker_manager.start():
            _model = _worker_manager
            _model_loaded = True
            _model_error = None
            return _worker_manager

        _model = None
        _model_loaded = False
        _model_error = _worker_manager.health().last_error_code or "worker_unavailable"
        logger.warning(
            "LLM worker no disponible — error=%s; se reintentara",
            _model_error,
        )
        return None


def _mark_worker_failure(
    manager: LlmWorkerManager,
    error_code: str,
) -> None:
    """Actualiza aliases de salud sin exponer mensajes del proceso hijo."""
    global _model, _model_loaded, _model_error
    with _model_lock:
        if _worker_manager is not manager or manager.is_healthy():
            return
        _model = None
        _model_loaded = False
        _model_error = error_code


def _is_llm_circuit_open() -> bool:
    """Indica si el circuit breaker del LLM está activo."""
    with _llm_circuit_lock:
        return time.monotonic() < _llm_circuit_open_until


def _open_llm_circuit(reason: str) -> None:
    """Abre el circuit breaker del LLM por una ventana de enfriamiento."""
    global _llm_circuit_open_until
    with _llm_circuit_lock:
        _llm_circuit_open_until = time.monotonic() + _LLM_CIRCUIT_COOLDOWN_SECONDS
    safe_reason = (
        reason
        if reason
        in {
            "timeout",
            "RuntimeError",
            "OSError",
            "ValueError",
            "worker_crashed",
            "worker_protocol_error",
            "worker_unavailable",
            "worker_remote_error",
        }
        else "unknown"
    )
    logger.warning(
        "Circuit breaker LLM abierto por %ss — motivo=%s",
        int(_LLM_CIRCUIT_COOLDOWN_SECONDS),
        safe_reason,
    )


def _close_llm_circuit() -> None:
    """Cierra el circuit breaker del LLM."""
    global _llm_circuit_open_until
    with _llm_circuit_lock:
        _llm_circuit_open_until = 0.0


async def _run_llm_completion(
    model: LlmWorkerManager,
    messages: list[dict[str, object]],
    max_tokens: int = 128,
) -> object:
    """Delega inferencia al hijo y traduce fallas al contrato existente."""
    if _is_llm_circuit_open():
        raise LlmCircuitOpenError("llm_circuit_open")

    try:
        response = await asyncio.to_thread(
            model.complete,
            messages,
            max_tokens,
            _GENERATION_TIMEOUT,
        )
    except LlmWorkerBusyError:
        raise LlmBusyError("llm_busy") from None
    except LlmWorkerTimeoutError:
        _mark_worker_failure(model, "worker_timeout")
        _open_llm_circuit("timeout")
        raise TimeoutError("llm_worker_timeout") from None
    except LlmWorkerCrashedError:
        _mark_worker_failure(model, "worker_crashed")
        _open_llm_circuit("worker_crashed")
        raise RuntimeError("llm_worker_crashed") from None
    except LlmWorkerProtocolError:
        _mark_worker_failure(model, "worker_protocol_error")
        _open_llm_circuit("worker_protocol_error")
        raise RuntimeError("llm_worker_protocol_error") from None
    except LlmWorkerUnavailableError:
        _mark_worker_failure(model, "worker_unavailable")
        _open_llm_circuit("worker_unavailable")
        raise RuntimeError("llm_worker_unavailable") from None
    except LlmWorkerRemoteError:
        _open_llm_circuit("worker_remote_error")
        raise RuntimeError("llm_worker_remote_error") from None

    _close_llm_circuit()
    return response


# ── Tool dispatcher ─────────────────────────────────────────────────

# Tipos para la tabla de herramientas.
# Acepta tanto sync (get_price_for_llm) como async (get_weather).
ToolHandler = Callable[..., object]


def _get_tool_handlers() -> dict[str, ToolHandler]:
    """Devuelve el diccionario de handlers para cada tool del whitelist.

    Los imports son lazy para evitar dependencias circulares y permitir
    que el modulo llm_service.py sea importable sin DB ni servicios.
    """
    from app.services.agronomic_rules_service import get_agronomic_rule_for_llm
    from app.services.expense_service import register_expense_for_llm
    from app.services.odepa_service import (
        calculate_margin_for_llm,
        calculate_sale_value_for_llm,
        get_price_for_llm,
        get_price_history_for_llm,
        get_price_spread_for_llm,
    )
    from app.services.panel_service import get_panel_link_for_llm
    from app.services.parcela_service import get_parcelas_for_llm, register_parcela_for_llm
    from app.services.rag_service import search_corpus_for_llm
    from app.services.weather_service import (
        get_clima_historico,
        get_clima_historico_multianual,
        get_pronostico,
        get_weather,
    )

    return {
        "get_price": get_price_for_llm,
        "get_price_spread": get_price_spread_for_llm,
        "get_price_history": get_price_history_for_llm,
        "calculate_sale_value": calculate_sale_value_for_llm,
        "calculate_margin": calculate_margin_for_llm,
        "get_weather": get_weather,
        "get_pronostico": get_pronostico,
        "get_clima_historico": get_clima_historico,
        "get_clima_historico_multianual": get_clima_historico_multianual,
        "search_corpus": search_corpus_for_llm,
        "register_expense": register_expense_for_llm,
        "register_parcela": register_parcela_for_llm,
        "get_parcelas": get_parcelas_for_llm,
        "get_regla_agronomica": get_agronomic_rule_for_llm,
        "get_link_resumen": get_panel_link_for_llm,
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


async def _execute_tool(name: str, arguments: dict[str, object], phone_hash: str | None = None) -> str:
    """Ejecuta una tool del whitelist y retorna el resultado como texto.

    Args:
        name: Nombre de la función (debe estar en WHITELIST_TOOLS).
        arguments: Diccionario con los argumentos parseados del JSON.
        phone_hash: Hash del teléfono para resolver mercado cercano (Issue #89).

    Returns:
        Resultado textual de la tool, o mensaje de error si falla.
    """
    handlers = _get_tool_handlers()
    handler = handlers.get(name)

    if handler is None:
        logger.warning("Tool rechazada — codigo=not_whitelisted")
        return FALLBACK_TEXT

    # Filtrar argumentos alucinados por el LLM contra la firma real del handler.
    # Evita TypeError cuando el LLM inventa params que el handler no acepta.
    # Inyectar phone_hash: tools de precio (Issue #89: mercado cercano) y
    # register_expense (Issue #170: scoping de gastos por agricultor).
    if (
        name
        in (
            "get_price",
            "get_price_history",
            "calculate_margin",
            "register_expense",
            "register_parcela",
            "get_parcelas",
            "get_link_resumen",
        )
        and phone_hash
    ):
        arguments = {**arguments, "phone_hash": phone_hash}
    valid_args = _filter_handler_args(handler, arguments)

    if (
        name in ("get_clima_historico", "get_clima_historico_multianual")
        and not str(valid_args.get("comuna", "")).strip()
    ):
        return "No entendí la comuna. ¿Podrías repetir dónde quieres consultar?"

    # Cache de resultados: evita llamadas redundantes al LLM + DB para
    # la misma consulta repetida (precios ODEPA solo cambian 1 vez al dia).
    cacheable = frozenset(
        {
            "get_price",
            "get_price_history",
            "get_weather",
            "get_clima_historico",
            "get_clima_historico_multianual",
        }
    )
    # search_corpus es tan rapido (<2ms) que no necesita cache.
    if name in cacheable:
        cached = _tool_cache.get(name, **valid_args)
        if cached is not None:
            return cached

    try:
        # Las tools de precio necesitan session de DB. Se la pasamos como kwarg.
        if name in (
            "get_price",
            "get_price_history",
            "calculate_sale_value",
            "calculate_margin",
            "register_expense",
            "register_parcela",
            "get_parcelas",
        ):
            from app.core.database import SessionLocal

            # register_parcela/get_parcelas no tienen "producto": tienen su
            # propia validación de campos requeridos más abajo.
            needs_producto = name not in ("register_parcela", "get_parcelas")
            # Completar defaults para argumentos vacios que el LLM no especifico.
            # Si el producto esta vacio, no podemos consultar nada -> fallback.
            if needs_producto and (not valid_args.get("producto") or not str(valid_args.get("producto", "")).strip()):
                return "No entendi que producto queres consultar. ¿Podrias repetir el nombre del producto?"
            # calculate_sale_value requiere cantidad_kg; sin ella no hay calculo.
            if name == "calculate_sale_value" and not str(valid_args.get("cantidad_kg", "")).strip():
                return "No entendi cuantos kilos vas a vender. ¿Podrias repetir la cantidad?"
            # calculate_margin requiere cantidad, unidad y precio_total.
            if name == "calculate_margin":
                if not str(valid_args.get("cantidad", "")).strip():
                    return "No entendi cuantos vendiste. ¿Podrias repetir la cantidad?"
                if not str(valid_args.get("unidad", "")).strip():
                    return (
                        "No entendi la unidad de medida. "
                        "¿Podrias repetir si son kilos, sacos, mallas, cajas o toneladas?"
                    )
                if not str(valid_args.get("precio_total", "")).strip():
                    return "No entendi el monto total que recibiste. ¿Podrias repetir cuanto te pagaron en total?"
            if name == "register_expense":
                if not str(valid_args.get("concepto", "")).strip():
                    return "No entendí en qué gastaste. ¿Podrías repetir el concepto?"
                if not str(valid_args.get("monto", "")).strip():
                    return "No entendí el monto gastado. ¿Podrías repetir cuánto fue?"
            if name == "register_parcela":
                if not str(valid_args.get("cultivo", "")).strip():
                    return "No entendí qué cultivo tiene la parcela. ¿Podrías repetirlo?"
                if not str(valid_args.get("superficie_ha", "")).strip():
                    return "No entendí la superficie de la parcela. ¿Podrías repetir cuántas hectáreas son?"
                if not str(valid_args.get("comuna", "")).strip():
                    return "No entendí en qué comuna está la parcela. ¿Podrías repetirla?"
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

        if name in WHITELIST_TOOLS:
            logger.info("Tool ejecutada — tool=%s", name)
        else:
            logger.info("Tool ejecutada")
        # Cachear resultado para evitar futuras llamadas al LLM.
        if name in cacheable:
            _tool_cache.set(name, str(result), **valid_args)
        return str(result)
    except (RuntimeError, ValueError, OSError, SQLAlchemyError) as exc:
        if name in WHITELIST_TOOLS:
            logger.error(
                "Error ejecutando tool — tool=%s error=%s",
                name,
                type(exc).__name__,
            )
        else:
            logger.error("Error ejecutando tool — error=%s", type(exc).__name__)
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
            logger.warning("Tool call descartada — error=JSONDecodeError")
            continue

        name = parsed.get("name", "")
        arguments = parsed.get("arguments", {})
        if not name or not isinstance(name, str):
            logger.warning("Tool call descartada — codigo=invalid_tool_name")
            continue

        tool_calls.append(
            {
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        )

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


# ── Construccion de mensajes ─────────────────────────────────────────


def _build_messages(
    user_query: str,
    history: list[dict[str, object]],
    cultivos: list[str] | None = None,
    system_tip: str | None = None,
    consulta_tipo: str | None = None,
) -> list[dict[str, object]]:
    """Construye la lista de mensajes para el LLM.

    Incluye las definiciones de tools en formato nativo Qwen2.5 (<tools> XML)
    dentro del system prompt, para que el modelo genere <tool_call> como texto.

    Si el agricultor tiene cultivos de interés registrados, se agrega una
    línea personalizada al system prompt para que el LLM pueda asumir un
    producto cuando el usuario no lo especifique (issue #125).

    Si se pasa system_tip, se agrega como instrucción adicional al system
    prompt. Usado por pipeline_service para sugerir calculate_margin cuando
    se detectan keywords de venta realizada (issue #91).

    Formato: system (con tools + personalización + tip) + history + user.

    Args:
        user_query: Texto de la consulta del agricultor.
        history: Mensajes previos del diálogo.
        cultivos: Lista opcional de cultivos de interés del agricultor.
        system_tip: Instrucción adicional opcional para el system prompt.
        consulta_tipo: Tipo detectado por el pipeline ("precio", "clima",
                       "ambos", "desconocido"). Recorta el bloque de tools al
                       dominio consultado. None o desconocido = las 10 tools.
    """
    # El bloque de tools va inmediatamente despues del system prompt para que el
    # prefijo quede estable y reusable por el cache KV. Todo lo variable
    # (cultivos, tip) se agrega DESPUES, nunca en el medio.
    system_content = SYSTEM_PROMPT + _TOOLS_SECTION_POR_TIPO.get(consulta_tipo or "desconocido", _TOOLS_SECTION)

    # Personalización por cultivos de interés (issue #125).
    # Si el agricultor tiene cultivos registrados, se lo indicamos al LLM
    # para que pueda asumir el producto cuando no se especifique explícitamente.
    if cultivos:
        cultivos_str = ", ".join(cultivos)
        system_content += f"\n\nEl agricultor cultiva: {cultivos_str}. Si no especifica producto, asume uno de estos."

    # Tip adicional para el LLM (issue #91: sugerir calculate_margin).
    if system_tip:
        system_content += f"\n\n{system_tip}"

    messages: list[dict[str, object]] = [
        {"role": "system", "content": system_content},
    ]
    messages.extend(history)
    messages.append({"role": "user", "content": user_query})
    return messages


async def answer(
    query_text: str,
    history: list[dict[str, object]] | None = None,
    phone_hash: str | None = None,
    cultivos: list[str] | None = None,
    system_tip: str | None = None,
    consulta_tipo: str | None = None,
) -> str:
    """Genera una respuesta textual usando el LLM con Tool Calling.

    Flujo:
    1. Construir mensajes (system prompt con tools en formato Qwen2.5 nativo).
    2. Llamar al LLM SIN tools parameter (usa <tool_call> en texto plano).
    3. Si el texto contiene <tool_call> -> parsear -> ejecutar (whitelist)
       -> devolver resultado como tool_response -> generar respuesta final.
    4. Si no hay tool_call -> retornar contenido como respuesta final.
    5. Si no hay modelo -> fallback mock para desarrollo.

    Args:
        query_text: Texto transcrito de la consulta del agricultor.
        history: Mensajes previos del diálogo (opcional). Formato
                 [{"role": "...", "content": "..."}, ...].
        phone_hash: Hash del teléfono para resolver mercado cercano (Issue #89).
        cultivos: Lista opcional de cultivos de interés del agricultor para
                  personalizar el contexto del LLM (Issue #125).
        system_tip: Instrucción adicional opcional para el system prompt.
                    Usado por pipeline_service para sugerir calculate_margin
                    cuando se detectan keywords de venta realizada (Issue #91).
        consulta_tipo: Tipo detectado por el pipeline. Recorta el bloque de
                       tools al dominio consultado para bajar el costo de
                       prompt eval, que domina la latencia con poca CPU.

    Returns:
        Texto de respuesta en español chileno, listo para TTS.
    """
    if not query_text or not query_text.strip():
        return NO_RESPONSE_TEXT

    # La carga/reinicialización del hijo puede esperar hasta el timeout de
    # startup. Nunca bloquear el event loop del webhook mientras ocurre.
    model = await asyncio.to_thread(_get_model)
    history = history or []

    if model is None:
        return _mock_answer(query_text)

    messages = _build_messages(
        query_text.strip(),
        history,
        cultivos=cultivos,
        system_tip=system_tip,
        consulta_tipo=consulta_tipo,
    )

    try:
        for _iteration in range(MAX_TOOL_ITERATIONS):
            # NO pasar tools/tool_choice — Qwen2.5 genera <tool_call> como texto
            # nativo cuando las definiciones estan en el system prompt.
            response = await _run_llm_completion(model, messages, max_tokens=128)

            content = _parse_content(response)
            if not content:
                # Si el LLM no genero contenido, reintentar con instruccion directa.
                messages.append(
                    {
                        "role": "system",
                        "content": "Responde al usuario en español chileno con frases cortas.",
                    }
                )
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
                        forced = await _force_keyword_tool(query_text, phone_hash=phone_hash)
                        if forced:
                            # Inyectar el tool call + respuesta para que
                            # el LLM lo formatee en la siguiente iteracion.
                            messages.append(
                                {
                                    "role": "assistant",
                                    "content": content,
                                }
                            )
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (f"<tool_response>\n{forced}\n</tool_response>"),
                                }
                            )
                            continue
                    return cleaned
                continue

            # El LLM quiere ejecutar herramientas.
            # Agregar mensaje del asistente con el tool call (como texto).
            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                }
            )

            for tc in tool_calls:
                fn_info_raw = tc.get("function", {})
                if not isinstance(fn_info_raw, dict):
                    continue
                fn_name = str(fn_info_raw.get("name", ""))
                fn_args_str = str(fn_info_raw.get("arguments", "{}"))

                # Whitelist enforcement: solo tools permitidas.
                if fn_name not in WHITELIST_TOOLS:
                    logger.warning("Tool rechazada — codigo=not_whitelisted; enviando fallback")
                    messages.append(
                        {
                            "role": "user",
                            "content": f"<tool_response>\n{FALLBACK_TEXT}\n</tool_response>",
                        }
                    )
                    continue

                # Parsear argumentos JSON.
                try:
                    fn_args: dict[str, object] = json.loads(str(fn_args_str))
                except json.JSONDecodeError:
                    logger.warning(
                        "Argumentos de tool descartados — tool=%s error=JSONDecodeError",
                        fn_name,
                    )
                    fn_args = {}

                # Ejecutar tool.
                tool_result = await _execute_tool(fn_name, fn_args, phone_hash=phone_hash)

                # Envolver resultado en <tool_response> (formato nativo Qwen2.5).
                messages.append(
                    {
                        "role": "user",
                        "content": f"<tool_response>\n{tool_result}\n</tool_response>",
                    }
                )

        # Si llegamos aca, se agotaron las iteraciones.
        logger.warning(
            "Tool Calling loop agoto %d iteraciones",
            MAX_TOOL_ITERATIONS,
        )
        # Ultimo intento: forzar respuesta sin tools.
        messages.append(
            {
                "role": "system",
                "content": (
                    "Genera una respuesta final en español chileno con los datos disponibles. Maximo 3 oraciones."
                ),
            }
        )
        try:
            final_response = await _run_llm_completion(model, messages, max_tokens=128)
            content = _parse_content(final_response)
            if content:
                cleaned = _strip_tool_tags(content)
                if cleaned:
                    return cleaned
        except (TimeoutError, RuntimeError, OSError, ValueError, LlmGuardError) as exc:
            logger.warning(
                "Error en respuesta final — error=%s",
                type(exc).__name__,
            )

        return FALLBACK_TEXT

    except TimeoutError:
        logger.warning("Timeout del LLM — timeout_seconds=%s", _GENERATION_TIMEOUT)
        forced = await _force_keyword_tool(query_text, phone_hash=phone_hash)
        if forced:
            return forced
        return "Estoy teniendo problemas para responder. ¿Podrías preguntar de nuevo más breve?"
    except LlmGuardError as exc:
        logger.warning(
            "LLM no disponible temporalmente — error=%s",
            type(exc).__name__,
        )
        forced = await _force_keyword_tool(query_text, phone_hash=phone_hash)
        if forced:
            return forced
        return _LLM_BUSY_TEXT
    except (json.JSONDecodeError, RuntimeError, OSError, ValueError) as exc:
        # ValueError: llama-cpp-python la lanza cuando el prompt (system+tools+
        # historial+query, o el tool_response inyectado) excede n_ctx. Con
        # n_ctx=4096 no ocurre en el flujo normal, pero un tool_response
        # inusualmente largo o un audio muy extenso transcrito podrian
        # seguir gatillandola.
        logger.error("Error en generacion LLM — error=%s", type(exc).__name__)
        forced = await _force_keyword_tool(query_text, phone_hash=phone_hash)
        if forced:
            return forced
        return "Tuve un problema al procesar tu consulta. ¿Probamos de nuevo?"


# System prompt reducido para OpenRouter: las tools van en el parametro
# nativo `tools=` (no como texto <tools> en el system prompt, que es un
# formato especifico para que Qwen2.5 genere <tool_call> via llama-cpp-python).
_OPENROUTER_SYSTEM_PROMPT = (
    "Eres AgroVoz, un asistente de voz para pequeños agricultores chilenos. "
    "Responde en español chileno, maximo 3 oraciones cortas. "
    "SIEMPRE usa una herramienta antes de responder. "
    "NUNCA inventes precios ni clima. NUNCA des recomendaciones agronomicas. "
    "NUNCA evalues elegibilidad financiera ni recomiendes creditos, programas, "
    "montos o tasas. NUNCA pidas RUT, ingresos ni deudas. "
    "Conserva la fuente (ODEPA para precios, OpenMeteo para clima) al citar datos."
)


async def answer_via_openrouter(
    query_text: str,
    phone_hash: str | None = None,
    cultivos: list[str] | None = None,
) -> str | None:
    """Segunda capa de fallback: responde via OpenRouter (tool calling nativo).

    Se usa SOLO cuando el LLM local (Qwen2.5 via llama-cpp-python) no esta
    disponible o fallo generando. Reusa TOOLS/WHITELIST_TOOLS/_execute_tool
    del modelo local — el unico cambio es el transporte (HTTP remoto en vez
    de llama-cpp local) y el formato de tool calls (tool_calls nativo OpenAI
    en vez de <tool_call> como texto plano).

    Nunca lanza excepcion: retorna None si OpenRouter no esta configurado
    (OPENROUTER_API_KEY vacia) o si falla por cualquier motivo (red, timeout,
    respuesta invalida, rate limit). El caller (pipeline_service) decide el
    siguiente escalon (fallback determinista de keywords).

    Riesgos conocidos (evaluados explicitamente, no accidentales):
    - El catalogo de "openrouter/free" rota sin aviso: el modelo real detras
      del alias puede cambiar entre llamadas.
    - Los modelos gratuitos de OpenRouter exigen, para poder usarse, aceptar
      que el contenido puede usarse para entrenar o publicarse (configurar
      en el dashboard de OpenRouter, no en este codigo). La consulta del
      agricultor sale del VPS hacia un tercero no auditado.
    - Rate limit del tier gratis: 20 req/min, 50-1000 req/dia segun creditos
      cargados. Es un fallback ocasional, no el camino principal.
    """
    from app.services import openrouter_service

    if not openrouter_service.is_configured():
        return None

    system_content = _OPENROUTER_SYSTEM_PROMPT
    if cultivos:
        system_content += f" El agricultor cultiva: {', '.join(cultivos)}."

    messages: list[dict[str, object]] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": query_text},
    ]

    try:
        for _iteration in range(MAX_TOOL_ITERATIONS):
            # Mismo criterio que el prompt local: no ofrecer tools apagadas.
            response = await openrouter_service.chat_completion_with_tools(messages, _offered_tools())
            message = response["choices"][0]["message"]
            tool_calls = message.get("tool_calls")

            if not tool_calls:
                content = message.get("content")
                return content.strip() if content else None

            messages.append(message)
            for call in tool_calls:
                name = call["function"]["name"]
                try:
                    arguments = json.loads(call["function"]["arguments"])
                except json.JSONDecodeError:
                    arguments = {}

                result = (
                    await _execute_tool(name, arguments, phone_hash=phone_hash)
                    if name in WHITELIST_TOOLS
                    else FALLBACK_TEXT
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": result,
                    }
                )

        logger.warning("OpenRouter Tool Calling loop agoto %d iteraciones", MAX_TOOL_ITERATIONS)
        return None
    except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
        logger.warning("Fallback OpenRouter fallo — error=%s", type(exc).__name__)
        return None


def _mock_answer(query_text: str) -> str:
    """Respuesta mock para desarrollo y CI sin modelo LLM.

    Detecta intenciones básicas por keyword para simular Tool Calling.
    Solo para desarrollo; en producción el modelo real debe estar cargado.
    """
    q = query_text.strip().lower()

    historico_keywords = [
        "histórico",
        "historico",
        "invierno",
        "otoño",
        "otono",
        "primavera",
        "verano",
        "helada",
        "llovió",
        "llovio",
    ]
    if any(kw in q for kw in historico_keywords):
        return (
            "Modo de prueba: resumen histórico simulado de Traiguén, con "
            "420 milímetros de lluvia y 14 días de helada, según OpenMeteo."
        )

    # Detección de keywords de clima
    clima_keywords = [
        "clima",
        "tiempo",
        "temperatura",
        "lluvia",
        "lloviendo",
        "frio",
        "calor",
        "humedad",
        "viento",
        "pronóstico",
        "pronostico",
    ]
    if any(kw in q for kw in clima_keywords):
        return (
            "Modo de prueba: clima simulado en Traiguén, 18 grados, nublado, "
            "humedad 65 por ciento, viento 3 coma 6 metros por segundo y "
            "lluvia 0 coma 5 milímetros. No es una consulta real a OpenMeteo."
        )

    # Detección de keywords de precio
    precio_keywords = ["precio", "cuánto", "cuanto", "cuesta", "vale", "está", "esta", "cómo está", "como esta"]
    if any(kw in q for kw in precio_keywords):
        return (
            "Modo de prueba: precio simulado de papa, 1.200 pesos el kilo "
            "en Lo Valledor. No es una consulta real a ODEPA."
        )

    # Fuera de scope
    return FALLBACK_TEXT


def is_model_available() -> bool:
    """Indica si el proceso LLM está vivo y listo para inferencia."""
    with _model_lock:
        return _worker_manager is not None and _worker_manager.is_healthy()


def get_model_error() -> str | None:
    """Retorna un código seguro del último error del worker."""
    with _model_lock:
        if _model_error is not None:
            return _model_error
        if _worker_manager is None:
            return None
        return _worker_manager.health().last_error_code


def reset_model() -> None:
    """Detiene y descarta el worker para liberar su memoria."""
    global _worker_manager, _model, _model_loaded, _model_error
    global _llm_circuit_open_until
    with _model_lock:
        if _worker_manager is not None:
            # Cerrar antes de liberar el lock evita solapar dos modelos de ~2 GB.
            _worker_manager.stop()
        _worker_manager = None
        _model = None
        _model_loaded = False
        _model_error = None
    with _llm_circuit_lock:
        _llm_circuit_open_until = 0.0
