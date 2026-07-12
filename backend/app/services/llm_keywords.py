"""Detección de keywords y fallback de tool calling.

Cuando el LLM no genera <tool_call>, detectamos keywords en la consulta
para forzar la tool correcta. Esto cubre el ~30% de consultas donde el
modelo Qwen2.5-3B Q4 no obedece la instrucción de "SIEMPRE usa una tool".
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Patrones que indican que el LLM respondió sin usar herramientas.
_GENERIC_RESPONSE_PATTERNS = [
    "no tengo", "no entiendo", "no conozco", "no sé", "no se",
    "reformul", "podrías repetir", "no dispongo", "sin información",
    "sin datos", "no cuento con", "no puedo responder",
    "lo siento", "disculpa", "no estoy seguro",
]

# Productos agrícolas chilenos más comunes (ODEPA). Para fallback de
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

# Regex determinista para detección de venta (Issue #104): captura "N kilos de
# <producto>" sin depender del LLM. El "de" tras kilos reduce falsos positivos
# ("tengo 30 kilos" sin intención de venta no matchea). El producto se valida
# aparte contra _COMMON_PRODUCTS via _extract_product_from_query.
_VENTA_KILOS_RE = re.compile(
    r"(\d+)\s*(?:kilos?|kg)\s+de\s+",
    re.IGNORECASE,
)

# Constantes de mensajes compartidas entre odepa_service y fallback.
# Usadas para detectar cuando una herramienta no pudo resolver la consulta.
FALLBACK_SALE_MESSAGES = {
    "no_data": "No tengo datos",
    "parsing_error": "No entendí",
    "invalid_quantity": "La cantidad tiene que ser",
}


def _is_generic_response(text: str) -> bool:
    """Detecta si la respuesta del LLM es genérica (no usó herramientas).

    Si el LLM responde con "no tengo información", "no entiendo",
    "reformula", etc., es señal de que no intentó usar tools.

    Args:
        text: Texto de respuesta del LLM.

    Returns:
        True si la respuesta parece genérica/sin tools.
    """
    lower = text.lower()
    return any(p in lower for p in _GENERIC_RESPONSE_PATTERNS)


def _extract_product_from_query(query: str) -> str | None:
    """Extrae el nombre de un producto agrícola de la consulta por keyword.

    Busca nombres de productos en el texto. Si el agricultor dice
    "a cuanto está el kilo de tomate", detecta "tomate".

    Args:
        query: Texto de la consulta del agricultor.

    Returns:
        Nombre del producto en minúsculas, o None si no se detecta.
    """
    query_lower = query.lower()
    # Ordenar por largo descendente para que "pimentón" matchee antes que "pimenton"
    # y "sandía" antes que "sandia".
    for product in sorted(_COMMON_PRODUCTS, key=len, reverse=True):
        if product in query_lower:
            return product
    return None


async def _force_sale_value_tool(query_text: str) -> str | None:
    """Fuerza tool call calculate_sale_value por detección de "N kilos de producto".

    Detecta patrones como "voy a vender 30 kilos de papa" y ejecuta
    calculate_sale_value directamente sin pasar por el LLM.

    Args:
        query_text: Texto de la consulta.

    Returns:
        Resultado textual de la tool, o None si no se detecta "N kilos" o falla.
    """
    from app.core.database import SessionLocal
    from app.services.odepa_service import calculate_sale_value_for_llm

    q = query_text.strip().lower()

    # Detectar "N kilos de producto" -> calculate_sale_value (Issue #104).
    venta_match = _VENTA_KILOS_RE.search(q)
    if not venta_match:
        return None

    product = _extract_product_from_query(q)
    if not product:
        return None

    cantidad = venta_match.group(1)
    session = SessionLocal()
    try:
        result = await asyncio.to_thread(
            calculate_sale_value_for_llm,
            session,
            producto=product,
            cantidad_kg=cantidad,
        )
        # Retornar salvo que sea un mensaje de error conocido
        # (en ese caso cae al bloque de precio/clima).
        if not any(
            result.startswith(msg)
            for msg in [
                FALLBACK_SALE_MESSAGES["no_data"],
                FALLBACK_SALE_MESSAGES["parsing_error"],
                FALLBACK_SALE_MESSAGES["invalid_quantity"],
            ]
        ):
            logger.info(
                "Fallback tool forzado: calculate_sale_value"
                "(producto=%s, cantidad=%s) — query=%.100s",
                product,
                cantidad,
                query_text,
            )
            return result
    except SQLAlchemyError as exc:
        # Fire-and-forget: un error de DB no rompe el pipeline.
        logger.warning("Error DB en fallback venta: %s", exc)
    finally:
        session.close()

    return None


async def _force_keyword_tool(query_text: str) -> str | None:
    """Orquestador de fallback por keywords: venta → precio → clima.

    Cuando el LLM no genera <tool_call>, detectamos keywords en la consulta
    para forzar la tool correspondiente directamente sin pasar por el LLM.

    Orden de precedencia:
    1. Venta (N kilos de producto) -> calculate_sale_value
    2. Precio (producto agrícola, presente/pasado) -> get_price/get_price_history
    3. Clima (keywords climáticos) -> get_weather

    Args:
        query_text: Texto de la consulta del agricultor.

    Returns:
        Resultado textual de la tool, o None si no se detecta keyword.
    """
    from app.core.database import SessionLocal
    from app.services.odepa_service import (
        get_price_for_llm,
        get_price_history_for_llm,
    )

    # 0. Detectar "N kilos de producto" -> calculate_sale_value (Issue #104).
    # Va antes que el bloque de precio: la cantidad de kilos es señal
    # fuerte de cálculo de venta y el LLM no debe hacer la multiplicación.
    forced = await _force_sale_value_tool(query_text)
    if forced:
        return forced

    q = query_text.strip().lower()

    # 1. Detectar productos agrícolas en la consulta.
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
            # Llamada en thread pool: las tools de precio son síncronas
            # (query SQLite) y no deben bloquear el event loop.
            if es_historia:
                result = await asyncio.to_thread(
                    get_price_history_for_llm, session, producto=product
                )
            else:
                result = await asyncio.to_thread(
                    get_price_for_llm, session, producto=product
                )
            # Solo retornar si encontró datos reales (no "No tengo datos...").
            if not result.startswith(FALLBACK_SALE_MESSAGES["no_data"]):
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
            # Traiguén como default si no hay coordenadas en la consulta.
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
