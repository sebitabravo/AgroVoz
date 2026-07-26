"""Detección de keywords y fallback de tool calling.

Cuando el LLM no genera <tool_call>, detectamos keywords en la consulta
para forzar la tool correcta. Esto cubre el ~30% de consultas donde el
modelo Qwen2.5-3B Q4 no obedece la instrucción de "SIEMPRE usa una tool".

Incluye detección fast-path de saludos simples (sin pregunta real) y
fuzzy matching para tolerancia a typos en nombres de productos.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
from contextlib import suppress
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
# La cantidad va acotada (\d{1,7}) por la misma razón que _CANT_UNIDAD_RE (ReDoS).
_VENTA_KILOS_RE = re.compile(
    r"(\d{1,7})\s*(?:kilos?|kg)\s+de\s+",
    re.IGNORECASE,
)

# Cantidades y montos acotados con \d{1,N} para EVITAR ReDoS: el pipeline es
# síncrono (workers=1) y un texto transcrito con miles de dígitos disparaba
# backtracking cuadrático que congelaba el único worker (DoS con un solo
# mensaje). Los límites cubren cualquier venta real (7 dígitos de cantidad).
_CANT_UNIDAD_RE = re.compile(
    r"(\d{1,7}(?:[.,]\d{1,3})?)\s*(saco|sacos|kilo|kilos|kg|malla|mallas"
    r"|caja|cajas|tonelada|toneladas)\b",
    re.IGNORECASE,
)

# El sufijo "lucas"/"mil" se CAPTURA (grupo 2) para aplicar el multiplicador
# x1000 de la jerga chilena: "150 lucas" = 150.000 pesos. Dígitos acotados.
_MONTO_RE = re.compile(
    r"(?:a\s*\$?\s*|en\s*\$?\s*|por\s*\$?\s*|recibi\s*\$?\s*"
    r"|recib[íi]\s*\$?\s*|me\s+(?:pagaron|pago)\s*\$?\s*)"
    r"(\d{1,9}(?:[.,\s]{0,2}\d{1,9}){0,4})\s*(lucas?|pesos?|mil|\.)?",
    re.IGNORECASE,
)

# Constantes de mensajes compartidas entre odepa_service y fallback.
# Usadas para detectar cuando una herramienta no pudo resolver la consulta.
FALLBACK_SALE_MESSAGES = {
    "no_data": "No tengo datos",
    "parsing_error": "No entendí",
    "invalid_quantity": "La cantidad tiene que ser",
}

# Keywords que indican consulta sobre documentos oficiales ODEPA.
# Activan el fallback search_corpus cuando el LLM no genera tool call.
_CORPUS_KEYWORDS = [
    "boletin", "boletín", "documento", "informe",
    "publicacion", "publicación",
    "tendencia", "contexto",
    "mercado agricola", "rubro",
    "agricultura familiar", "pequeña agricultura", "pequena agricultura",
    "como funciona", "como es el mercado",
    "que dice el boletin", "que dice la odepa",
    "información general", "informacion general",
    "censo agropecuario", "caracterizacion",
]


def _detect_greeting(query: str) -> bool:
    """Detecta si la consulta es un saludo puro (sin pregunta real).

    Identifica saludos chilenos simples que NO contienen pregunta sobre
    precios o clima. Ej: "hola", "buenos días", "aló" → True.
    Ej: "hola a cómo está la papa" → False (tiene pregunta).

    Verifica (en orden):
    0. Que la consulta NO contenga un producto reconocible (exacto o fuzzy).
    1. Que la consulta NO contenga keywords de pregunta (precio, clima, etc).
    2. Que la consulta contenga un saludo conocido (palabra o frase).

    Args:
        query: Texto de la consulta.

    Returns:
        True si es un saludo puro (solo el saludo, sin pregunta).
    """
    # Saludos multi-palabra (frases completas).
    saludos_frases = [
        "buenos días", "buenos dias",
        "buenas tardes",
        "buenas noches",
        "buen día", "buen dia",
        "qué tal", "que tal",
    ]

    # Saludos de una palabra.
    saludos_palabras = {
        "hola", "hi", "ola", "aló", "alo", "hey", "holaa",
    }

    # Palabras que indican una pregunta real (no es solo saludo).
    pregunta_keywords = [
        "precio", "cuánto", "cuanto", "cuesta", "vale",
        "a cómo", "a como", "kilo", "saco", "malla", "caja",
        "clima", "tiempo", "temperatura", "lluvia", "pronóstico", "pronostico",
        "frio", "calor", "viento", "humedad",
        "vendo", "vender", "venta", "kilos", "kg",
        "semana pasada", "ayer", "hace",
    ]

    q = query.strip().lower()

    # Paso 0: Si la consulta contiene un producto reconocible (exacto o fuzzy),
    # NO es un saludo puro. Esto previene la regresión "hola papa" → precio.
    if _extract_product_from_query(q) is not None:
        return False

    # Paso 1: Si contiene keywords de pregunta, NO es saludo puro.
    for kw in pregunta_keywords:
        if kw in q:
            return False

    # Paso 2: Si contiene una frase de saludo (2+ palabras), es saludo.
    for frase in saludos_frases:
        if frase in q:
            return True

    # Paso 3: Tokenizar y buscar palabras de saludo simples.
    # Dividir por espacios, comas, puntos, etc.
    tokens = re.split(r'[\s,;.!?]+', q)
    tokens = [t for t in tokens if t]  # Filtrar vacíos.

    # Si hay solo 1-2 tokens y alguno es un saludo, es saludo puro.
    if len(tokens) <= 2:
        for token in tokens:
            if token in saludos_palabras:
                return True

    return False


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

    Busca nombres de productos en el texto usando:
    1. Substring exacto (primero, más rápido y confiable).
    2. Fuzzy match como fallback para tolerar typos ("celga" → "acelga").

    Si el agricultor dice "a cuanto está el kilo de tomate", detecta "tomate".
    Si dice "celga" (typo), fuzzy match detecta "acelga" con cutoff 0.75.

    Args:
        query: Texto de la consulta del agricultor.

    Returns:
        Nombre del producto en minúsculas, o None si no se detecta.
    """
    query_lower = query.lower()

    # 1. Substring exacto: ordenar por largo descendente para que "pimentón"
    # matchee antes que "pimenton" y "sandía" antes que "sandia".
    for product in sorted(_COMMON_PRODUCTS, key=len, reverse=True):
        if product in query_lower:
            return product

    # 2. Fuzzy match como fallback: detectar typos sin strict substring match.
    # Tokenizar la consulta en palabras y comparar cada una contra productos.
    # Cutoff 0.75 evita falsos positivos en palabras cortas.
    tokens = re.split(r'[\s,;.!?]+', query_lower)
    tokens = [t for t in tokens if t and len(t) > 2]  # Ignorar palabras muy cortas.

    for token in tokens:
        # Buscar el producto más similar usando difflib.
        matches = difflib.get_close_matches(
            token,
            _COMMON_PRODUCTS,
            n=1,  # Solo el mejor match.
            cutoff=0.75,  # Umbral para evitar falsos positivos.
        )
        if matches:
            logger.debug(
                "Fuzzy match detectado — token=%s → producto=%s query=%.100s",
                token,
                matches[0],
                query,
            )
            return matches[0]

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


# Keywords que indican una venta ya realizada (para margin).
_VENTA_REALIZADA_KW = [
    "vendí", "vendi", "vendiste", "vendio", "vendió", "vendieron",
    "ya vendí", "ya vendi", "acabo de vender", "recién vendí",
    "recien vendi", "recibí", "recibi", "me pagaron", "me pagó",
    "me pago", "recibimos", "vendimos",
]


def _parse_monto(query: str) -> str | None:
    """Extrae el monto total de venta de un texto y aplica la jerga chilena.

    "lucas" y "mil" multiplican por 1000 ("150 lucas" = 150.000). Retorna el
    monto como string de dígitos, o None si no encuentra un patrón de monto.
    Separado de _force_margin_tool para testear la conversión sin tocar la DB.
    """
    monto_match = _MONTO_RE.search(query)
    if not monto_match:
        return None

    precio_total_str = monto_match.group(1).replace(" ", "").replace(".", "")
    sufijo = (monto_match.group(2) or "").lower()
    if sufijo.startswith("luca") or sufijo == "mil":
        # Monto no entero (ej. traía coma decimal): se deja tal cual y
        # calculate_margin_for_llm lo valida/parsea aguas abajo.
        with suppress(ValueError):
            precio_total_str = str(int(precio_total_str) * 1000)
    return precio_total_str


async def _force_margin_tool(query_text: str) -> str | None:
    """Fuerza tool call calculate_margin por deteccion de venta realizada.

    Detecta "vendi X [unidad] de [producto] a [monto]" como patron para
    calculate_margin. La extraccion es heuristicamente simple: buscar
    producto, cantidad y unidad en la consulta, y el monto total si es
    detectable. Como la extraccion de montos del lenguaje natural es
    poco confiable (35-55% segun spike del issue #91), este fallback
    solo cubre patrones MUY claros. El LLM es el camino principal.

    SE EJECUTA SOLO como fallback cuando el LLM no genero tool calls.
    No reemplaza el Tool Calling del LLM.

    Args:
        query_text: Texto de la consulta del agricultor.

    Returns:
        Resultado textual de calculate_margin_for_llm, o None si no se
        puede extraer el patron completo.
    """
    from app.core.database import SessionLocal
    from app.services.odepa_service import calculate_margin_for_llm

    q = query_text.strip().lower()

    # Verificar si la consulta menciona una venta ya realizada.
    if not any(kw in q for kw in _VENTA_REALIZADA_KW):
        return None

    # Extraer producto.
    product = _extract_product_from_query(q)
    if not product:
        return None

    # Extraer cantidad y unidad con el patrón acotado a nivel módulo.
    # Cubre "N sacos/kilos/mallas/cajas/toneladas" y sus singulares.
    match = _CANT_UNIDAD_RE.search(q)
    if not match:
        return None

    cantidad = match.group(1).replace(",", ".")
    unidad = match.group(2).lower()

    # Normalizar plurales a singular para el handler.
    mapa_plural = {
        "sacos": "saco", "kilos": "kilo", "kg": "kilo",
        "mallas": "malla", "cajas": "caja", "toneladas": "tonelada",
    }
    unidad = mapa_plural.get(unidad, unidad)

    # Extraer monto total (incluye el multiplicador "lucas"/"mil" x1000).
    precio_total_str = _parse_monto(q)
    if precio_total_str is None:
        return None

    session = SessionLocal()
    try:
        result = await asyncio.to_thread(
            calculate_margin_for_llm,
            session,
            producto=product,
            cantidad=cantidad,
            unidad=unidad,
            precio_total=precio_total_str,
        )
        # Solo retornar si no es mensaje de error de parseo.
        if not any(
            result.startswith(msg)
            for msg in [
                FALLBACK_SALE_MESSAGES["no_data"],
                FALLBACK_SALE_MESSAGES["parsing_error"],
            ]
        ):
            logger.info(
                "Fallback tool forzado: calculate_margin"
                "(producto=%s, cantidad=%s, unidad=%s, precio_total=%s)"
                " — query=%.100s",
                product, cantidad, unidad, precio_total_str, query_text,
            )
            return result
    except SQLAlchemyError as exc:
        logger.warning("Error DB en fallback margin: %s", exc)
    finally:
        session.close()

    return None


async def _force_corpus_search(query_text: str) -> str | None:
    """Fuerza busqueda en corpus ODEPA por deteccion de keywords.

    Detecta keywords de documentos/boletines y ejecuta search_corpus
    directamente sin pasar por el LLM.

    Args:
        query_text: Texto de la consulta del agricultor.

    Returns:
        Resultado textual de la busqueda, o None si no se detecta keyword.
    """
    q = query_text.strip().lower()

    # Detectar keywords de corpus/boletines. El import va DENTRO del branch:
    # rag_service arrastra scikit-learn, que es pesado y opcional. Importarlo
    # arriba lo cargaba en cada consulta aunque no hubiera keywords de corpus.
    if not any(kw in q for kw in _CORPUS_KEYWORDS):
        return None

    try:
        # ImportError incluido a proposito: si falta scikit-learn (imagen sin
        # reconstruir, deploy incompleto), el corpus deja de estar disponible
        # pero el pipeline sigue respondiendo precio y clima. Sin este guard,
        # un ModuleNotFoundError tumbaba la consulta entera.
        from app.services.rag_service import search_corpus_for_llm

        result = search_corpus_for_llm(query_text)
        if result:
            logger.info(
                "Fallback tool forzado: search_corpus(query=%.100s) — query=%.100s",
                query_text,
                query_text,
            )
            return result
    except ImportError:
        logger.warning(
            "Corpus RAG no disponible (falta dependencia) — se continua sin search_corpus"
        )
    except (RuntimeError, ValueError, OSError) as exc:
        logger.warning("Error en fallback corpus: %s", exc)

    return None


async def _force_keyword_tool(
    query_text: str, phone_hash: str | None = None
) -> str | None:
    """Orquestador de fallback por keywords: venta → precio → clima.

    Cuando el LLM no genera <tool_call>, detectamos keywords en la consulta
    para forzar la tool correspondiente directamente sin pasar por el LLM.

    Orden de precedencia:
    1. Venta (N kilos de producto) -> calculate_sale_value
    2. Precio (producto agrícola, presente/pasado) -> get_price/get_price_history
    3. Clima (keywords climáticos) -> get_weather

    Args:
        query_text: Texto de la consulta del agricultor.
        phone_hash: Hash del teléfono para resolver mercado cercano (Issue #89).

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

    # 0.5 Detectar venta ya realizada -> calculate_margin (Issue #91).
    # Va antes del bloque de precio: "vendi" es señal fuerte de
    # comparacion de margen, no de precio actual. Extraccion heuristicamente
    # simple; el LLM es el camino principal para margin.
    forced = await _force_margin_tool(query_text)
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
                    get_price_for_llm, session, producto=product, phone_hash=phone_hash
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

    # 2. Detectar keywords de corpus/boletines (antes que clima).
    corpus_result = await _force_corpus_search(query_text)
    if corpus_result:
        return corpus_result

    # 3. Detectar keywords de clima.
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
