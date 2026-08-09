"""Detección de keywords y fallback de tool calling.

Cuando el LLM no genera <tool_call>, detectamos keywords en la consulta
para forzar la tool correcta. Esto cubre el ~30% de consultas donde el
modelo Qwen2.5-3B Q4 no obedece la instrucción de "SIEMPRE usa una tool".

Incluye detección fast-path de saludos simples (sin pregunta real) y
fuzzy matching para tolerancia a typos en nombres de productos.
"""

from __future__ import annotations

import asyncio
import datetime
import difflib
import logging
import re
import unicodedata
from contextlib import suppress
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Patrones que indican que el LLM respondió sin usar herramientas.
_GENERIC_RESPONSE_PATTERNS = [
    "no tengo",
    "no entiendo",
    "no conozco",
    "no sé",
    "no se",
    "reformul",
    "podrías repetir",
    "no dispongo",
    "sin información",
    "sin datos",
    "no cuento con",
    "no puedo responder",
    "lo siento",
    "disculpa",
    "no estoy seguro",
]

# Productos agrícolas chilenos más comunes (ODEPA). Para fallback de
# keyword detection cuando el LLM no llama get_price.
_COMMON_PRODUCTS = [
    "papa",
    "tomate",
    "cebolla",
    "lechuga",
    "zanahoria",
    "ajo",
    "palta",
    "naranja",
    "limón",
    "limon",
    "plátano",
    "platano",
    "manzana",
    "pera",
    "kiwi",
    "uva",
    "durazno",
    "ciruela",
    "frutilla",
    "sandía",
    "sandia",
    "melón",
    "melon",
    "repollo",
    "acelga",
    "espinaca",
    "brocoli",
    "brócoli",
    "coliflor",
    "zapallo italiano",
    "zapallo de guarda",
    "zapallo",
    "camote",
    "betarraga",
    "rabanito",
    "rúcula",
    "rucula",
    "cilantro",
    "perejil",
    "apio",
    "puerro",
    "choclo",
    "poroto granado",
    "poroto verde",
    "poroto",
    "arveja verde",
    "arveja",
    "haba",
    "pepino",
    "pimentón",
    "pimenton",
    "ají",
    "aji",
    "maíz choclero",
    "maiz choclero",
    "maíz",
    "maiz",
    "lenteja",
    "trigo",
    "arroz",
]


def _contains_product_keyword(query: str, product: str) -> bool:
    """Comprueba el producto como palabra, no dentro de otra palabra."""
    return re.search(rf"(?<!\w){re.escape(product)}(?!\w)", query) is not None

# Regex determinista para detección de venta (Issue #104): captura "N kilos"
# con producto cercano. El "de" es opcional: "50 kilos de papa" y
# "cuanto vale 50 kilos papa" deben ambos disparar calculate_sale_value.
# El producto se valida aparte contra _COMMON_PRODUCTS via
# _extract_product_from_query. La cantidad va acotada (\d{1,7}) por la
# misma razón que _CANT_UNIDAD_RE (ReDoS).
_VENTA_KILOS_RE = re.compile(
    r"(\d{1,7})\s*(?:kilos?|kg)(?:\s+de)?\s+",
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

# Marcas temporales compartidas por los fallbacks de precio y clima.
_PRECIO_HISTORIA_KW = (
    "estaba",
    "semana pasada",
    "ayer",
    "hace ",
    "valia",
    "valía",
    "ha subido",
    "ha bajado",
    "subio",
    "subió",
    "bajo el precio",
    "bajó",
    "antes",
)
_CLIMA_FUTURO_KW = (
    "mañana",
    "manana",
    "pronóstico",
    "pronostico",
    "proximos",
    "próximos",
    "va a ",
    "vendra",
    "vendrá",
    "semana que viene",
)

_CLIMA_HISTORICO_KW = (
    "histórico",
    "historico",
    "año pasado",
    "ano pasado",
    "año anterior",
    "ano anterior",
    "anos anteriores",
    "años anteriores",
    "invierno",
    "otoño",
    "otono",
    "primavera",
    "verano",
    "heladas",
    "llovió",
    "llovio",
)

_HISTORICAL_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

# Keywords que indican consulta sobre documentos oficiales ODEPA.
# Activan el fallback search_corpus cuando el LLM no genera tool call.
_CORPUS_KEYWORDS = [
    "boletin",
    "boletín",
    "documento",
    "informe",
    "publicacion",
    "publicación",
    "tendencia",
    "contexto",
    "mercado agricola",
    "rubro",
    "agricultura familiar",
    "pequeña agricultura",
    "pequena agricultura",
    "como funciona",
    "como es el mercado",
    "que dice el boletin",
    "que dice la odepa",
    "información general",
    "informacion general",
    "censo agropecuario",
    "caracterizacion",
]

# Keywords del directorio. Se resuelven antes que precio/clima cuando el LLM
# no genera una llamada, para que una consulta de oficina siga siendo útil en
# el piso de hardware sin pagar otra inferencia.
_DIRECTORIO_KEYWORDS = (
    "indap",
    "prodesal",
    "cooperativa",
    "cooperativas",
    "oficina de area",
    "oficina de área",
    "agencia de area",
    "agencia de área",
    "directorio agricola",
    "directorio agrícola",
)

_DIRECTORIO_COMUNAS = (
    "padre las casas",
    "teodoro schmidt",
    "nueva imperial",
    "puerto saavedra",
    "curacautin",
    "curacautín",
    "curarrehue",
    "pitrufquen",
    "pitrufquén",
    "collipulli",
    "villarrica",
    "lonquimay",
    "traiguen",
    "traiguén",
    "galvarino",
    "lautaro",
    "loncoche",
    "puren",
    "purén",
    "temuco",
    "tolten",
    "toltén",
    "vilcun",
    "vilcún",
    "angol",
    "carahue",
    "cunco",
    "pucon",
    "pucón",
    "hualpin",
    "hualpín",
    "lumaco",
    "cholchol",
    "ercilla",
    "melipeuco",
    "freire",
    "gorbea",
    "renaico",
    "los sauces",
    "victoria",
)


def _normalizar_sin_tildes(texto: str) -> str:
    """Normaliza texto para detectar comunas en transcripciones."""
    decomposed = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _extract_directorio_comuna(query: str) -> str | None:
    """Extrae una comuna conocida para el fallback del directorio."""
    normalized = _normalizar_sin_tildes(query)
    for comuna in sorted(_DIRECTORIO_COMUNAS, key=len, reverse=True):
        if _normalizar_sin_tildes(comuna) in normalized:
            return comuna
    return None


def _extract_directorio_tipo(query: str) -> str | None:
    """Determina el tipo de sede mencionado por el agricultor."""
    normalized = _normalizar_sin_tildes(query)
    if "cooperativ" in normalized:
        return "cooperativa"
    if "prodesal" in normalized:
        return "prodesal"
    return "indap"


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
        "buenos días",
        "buenos dias",
        "buenas tardes",
        "buenas noches",
        "buen día",
        "buen dia",
        "qué tal",
        "que tal",
    ]

    # Saludos de una palabra.
    saludos_palabras = {
        "hola",
        "hi",
        "ola",
        "aló",
        "alo",
        "hey",
        "holaa",
    }

    # Palabras que indican una pregunta real (no es solo saludo).
    pregunta_keywords = [
        "precio",
        "cuánto",
        "cuanto",
        "cuesta",
        "vale",
        "a cómo",
        "a como",
        "kilo",
        "saco",
        "malla",
        "caja",
        "clima",
        "tiempo",
        "temperatura",
        "lluvia",
        "pronóstico",
        "pronostico",
        "frio",
        "calor",
        "viento",
        "humedad",
        "vendo",
        "vender",
        "venta",
        "kilos",
        "kg",
        "semana pasada",
        "ayer",
        "hace",
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
    tokens = re.split(r"[\s,;.!?]+", q)
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


# Palabras comunes del castellano que NUNCA deben resolverse a un producto por
# similitud. El fuzzy match existe para tolerar typos ("celga" -> "acelga"), no
# para adivinar productos donde no se nombro ninguno.
#
# El caso que motivo la lista: "¿va a llover mañana?" respondia el precio de la
# MANZANA. "mañana" contra "manzana" da 0.769 y "manana" (como suele transcribir
# Whisper, sin tilde) da 0.923 — mas alto que typos legitimos como "celga"
# (0.909). Subir el cutoff no arregla el falso positivo sin romper los typos
# reales, asi que la unica salida limpia es excluir estas palabras del fuzzy.
#
# Solo afecta al paso fuzzy: si el productor escribe "manzana" de verdad, el
# substring exacto la detecta antes de llegar aca.
_PALABRAS_NO_PRODUCTO = frozenset(
    {
        "manana",
        "mañana",
        "semana",
        "ventana",
        "campana",
        "montana",
        "montaña",
        "temprano",
        "cuanto",
        "cuánto",
        "para",
        "habla",
        "ahora",
        "tarde",
        "noche",
    }
)

# Alias hablados → substring para buscar el mercado ODEPA.
# Ordenados del más largo al más corto para que "lo valledor" gane a "valledor"
# y "vega central" no se confunda con un "vega" suelto.
# El substring se pasa a get_price_for_llm / _find_market_record.
_MERCADO_ALIASES: tuple[tuple[str, str], ...] = (
    ("estación central", "vega central"),
    ("estacion central", "vega central"),
    ("la araucanía", "Vega Modelo de Temuco"),
    ("la araucania", "Vega Modelo de Temuco"),
    ("novena región", "Vega Modelo de Temuco"),
    ("novena region", "Vega Modelo de Temuco"),
    ("la novena", "Vega Modelo de Temuco"),
    ("décima región", "Vega de Puerto Montt"),
    ("decima region", "Vega de Puerto Montt"),
    ("la décima", "Vega de Puerto Montt"),
    ("la decima", "Vega de Puerto Montt"),
    ("octava región", "Vega Monumental de Concepción"),
    ("octava region", "Vega Monumental de Concepción"),
    ("la octava", "Vega Monumental de Concepción"),
    ("vega central", "vega central"),
    ("concepción", "vega monumental"),
    ("concepcion", "vega monumental"),
    ("conce", "vega monumental"),
    ("lo valledor", "valledor"),
    ("vega modelo", "vega modelo"),
    ("puerto montt", "puerto montt"),
    ("la serena", "la serena"),
    ("la calera", "la calera"),
    ("valledor", "valledor"),
    ("femacal", "femacal"),
    ("mapocho", "mapocho"),
    ("macroferia", "macroferia"),
    ("lagunitas", "lagunitas"),
    ("palmera", "palmera"),
    ("limarí", "limar"),
    ("limari", "limar"),
    ("chillán", "chillán"),
    ("chillan", "chillan"),
    ("temuco", "temuco"),
    ("talca", "talca"),
    ("arica", "arica"),
)


def _extract_mercado_from_query(query: str) -> str | None:
    """Extrae un mercado ODEPA nombrado en la consulta.

    Si el productor dice "papa en la Vega Central", retorna el substring
    "vega central" para que get_price_for_llm lo resuelva por match exacto
    o por substring contra el nombre completo en la base.

    Args:
        query: Texto de la consulta del agricultor.

    Returns:
        Substring de mercado, o None si no nombra uno específico.
    """
    q = query.strip().lower()
    for alias, substring in _MERCADO_ALIASES:
        if alias in q:
            return substring

    # Las comunas no son mercados en sí mismas, pero el catálogo de ODEPA sí
    # tiene un mercado de referencia cercano para las comunas conocidas.
    # Importar aquí evita cargar SQLAlchemy al importar el módulo de keywords.
    from app.services.odepa_service import COMUNA_TO_MERCADO

    for comuna, mercado in sorted(COMUNA_TO_MERCADO.items(), key=lambda item: len(item[0]), reverse=True):
        if comuna in q:
            return mercado
    return None


def _extract_comuna_from_query(query: str) -> str | None:
    """Extrae una comuna conocida de la consulta climática.

    Reutiliza el diccionario de weather_service. Preferir el match más largo
    ("padre las casas" antes que un falso positivo parcial).

    Args:
        query: Texto de la consulta.

    Returns:
        Nombre canónico de comuna, o None si no aparece ninguna.
    """
    from app.services.weather_service import extraer_comuna_de_consulta

    return extraer_comuna_de_consulta(query)


def _extract_historico_request(query: str) -> tuple[int, str | None, int | None, str | None]:
    """Extrae rango, temporada, año y métrica para el fallback climático."""
    q = query.strip().lower()
    temporada: str | None = None
    for candidate in ("invierno", "otoño", "otono", "primavera", "verano"):
        if candidate in q:
            temporada = candidate
            break

    year_match = _HISTORICAL_YEAR_RE.search(q)
    anio = int(year_match.group()) if year_match is not None else None
    current_markers = ("este año", "este ano", "año actual", "ano actual")
    previous_markers = ("año pasado", "ano pasado", "año anterior", "ano anterior", "el anterior")
    if any(marker in q for marker in current_markers):
        anio = datetime.date.today().year
        anos = 2 if any(marker in q for marker in previous_markers) else 1
    else:
        anos = 1 if anio is not None or any(marker in q for marker in previous_markers) else 3

    metrica: str | None = None
    if "helad" in q:
        metrica = "heladas"
    elif "lluv" in q or "llov" in q or "precipit" in q:
        metrica = "lluvia"
    elif "temperatura" in q:
        metrica = "temperatura"
    return anos, temporada, anio, metrica


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

    # 1. Coincidencia exacta por límites de palabra. Un substring simple
    # confundía "papaya" con "papa" y cultivos compuestos con el genérico.
    for product in sorted(_COMMON_PRODUCTS, key=len, reverse=True):
        if _contains_product_keyword(query_lower, product):
            return product

    # 2. Fuzzy match como fallback: detectar typos sin strict substring match.
    # Tokenizar la consulta en palabras y comparar cada una contra productos.
    # Cutoff 0.75 evita falsos positivos en palabras cortas.
    tokens = re.split(r"[\s,;.!?¿¡]+", query_lower)
    # Se ignoran palabras muy cortas y las del castellano comun: sin este filtro
    # "¿va a llover mañana?" resolvia a "manzana" y devolvia un precio.
    tokens = [t for t in tokens if t and len(t) > 2 and t not in _PALABRAS_NO_PRODUCTO]

    for token in tokens:
        # Restringir a una edición de longitud: "papaya" no es un typo de
        # "papa", pero "pap" y "celga" siguen tolerados.
        candidates = [
            product for product in _COMMON_PRODUCTS
            if " " not in product and abs(len(product) - len(token)) <= 1
        ]
        matches = difflib.get_close_matches(
            token,
            candidates,
            n=1,  # Solo el mejor match.
            cutoff=0.75,  # Umbral para evitar falsos positivos.
        )
        if matches:
            logger.debug("Fuzzy match detectado — dominio=producto estado=found")
            return matches[0]

    return None


def _extract_product_mentions(query: str) -> list[str]:
    """Extrae productos en orden, conservando repeticiones por zona.

    La extracción simple retorna un solo producto porque es suficiente para la
    consulta habitual. Este helper se usa únicamente cuando aparecen varias
    cláusulas unidas por "y", por ejemplo "papa en Temuco y tomate en Chillán".
    """
    query_lower = query.lower()
    candidates = sorted(_COMMON_PRODUCTS, key=len, reverse=True)
    matches: list[tuple[int, int, str]] = []
    for product in candidates:
        start = query_lower.find(product)
        if start < 0:
            continue
        end = start + len(product)
        if not _contains_product_keyword(query_lower, product):
            continue
        if any(start < previous_end and end > previous_start for previous_start, previous_end, _ in matches):
            continue
        matches.append((start, end, product))
    matches.sort(key=lambda match: match[0])
    return [product for _, _, product in matches]


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
    mercado = _extract_mercado_from_query(q) or ""
    session = SessionLocal()
    try:
        result = await asyncio.to_thread(
            calculate_sale_value_for_llm,
            session,
            producto=product,
            cantidad_kg=cantidad,
            mercado=mercado,
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
            logger.info("Fallback tool forzado — tool=calculate_sale_value")
            return result
    except SQLAlchemyError as exc:
        # Fire-and-forget: un error de DB no rompe el pipeline.
        logger.warning(
            "Error DB en fallback venta — error=%s",
            type(exc).__name__,
        )
    finally:
        session.close()

    return None


# Keywords que indican una venta ya realizada (para margin).
_VENTA_REALIZADA_KW = [
    "vendí",
    "vendi",
    "vendiste",
    "vendio",
    "vendió",
    "vendieron",
    "ya vendí",
    "ya vendi",
    "acabo de vender",
    "recién vendí",
    "recien vendi",
    "recibí",
    "recibi",
    "me pagaron",
    "me pagó",
    "me pago",
    "recibimos",
    "vendimos",
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
        "sacos": "saco",
        "kilos": "kilo",
        "kg": "kilo",
        "mallas": "malla",
        "cajas": "caja",
        "toneladas": "tonelada",
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
            logger.info("Fallback tool forzado — tool=calculate_margin")
            return result
    except SQLAlchemyError as exc:
        logger.warning(
            "Error DB en fallback margen — error=%s",
            type(exc).__name__,
        )
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
            logger.info("Fallback tool forzado — tool=search_corpus")
            return result
    except ImportError:
        logger.warning("Corpus RAG no disponible (falta dependencia) — se continua sin search_corpus")
    except (RuntimeError, ValueError, OSError) as exc:
        logger.warning(
            "Error en fallback corpus — error=%s",
            type(exc).__name__,
        )

    return None


async def _compound_price_block(
    query_text: str,
    phone_hash: str | None,
) -> tuple[str | None, str]:
    """Obtiene el bloque ODEPA de una consulta compuesta."""
    from app.core.database import SessionLocal
    from app.services.odepa_service import (
        get_price_for_llm,
        get_price_history_for_llm,
    )

    product = _extract_product_from_query(query_text)
    if product is None:
        return None, "Necesito que me indiques qué producto quieres consultar."

    session = None
    try:
        session = SessionLocal()
        mercado = _extract_mercado_from_query(query_text) or ""
        if any(kw in query_text for kw in _PRECIO_HISTORIA_KW):
            result = await asyncio.to_thread(
                get_price_history_for_llm,
                session,
                producto=product,
            )
        else:
            result = await asyncio.to_thread(
                get_price_for_llm,
                session,
                producto=product,
                mercado=mercado,
                phone_hash=phone_hash,
            )
        if result.startswith(FALLBACK_SALE_MESSAGES["no_data"]):
            return None, result
        return result, ""
    except (SQLAlchemyError, TimeoutError, RuntimeError, OSError, ValueError) as exc:
        logger.warning(
            "Error en precio de consulta compuesta — error=%s",
            type(exc).__name__,
        )
        return None, "No pude obtener el precio en este momento."
    finally:
        if session is not None:
            session.close()


async def _compound_weather_block(
    query_text: str,
    phone_hash: str | None,
) -> tuple[str | None, str]:
    """Obtiene el bloque OpenMeteo de una consulta compuesta."""
    from app.services.weather_service import (
        get_clima_historico_multianual,
        get_pronostico,
        get_weather,
        resolver_comuna,
    )

    comuna = _extract_comuna_from_query(query_text)
    try:
        if any(kw in query_text for kw in _CLIMA_HISTORICO_KW):
            anos, temporada, anio, metrica = _extract_historico_request(query_text)
            result = await get_clima_historico_multianual(
                comuna or "Traiguén",
                anos=anos,
                temporada=temporada,
                anio=anio,
                metrica=metrica,
            )
        elif any(kw in query_text for kw in _CLIMA_FUTURO_KW):
            if phone_hash:
                try:
                    result = await get_pronostico(comuna, dias=2, phone_hash=phone_hash)
                except TypeError as exc:
                    # Mantiene compatibilidad con handlers sustituidos en
                    # integraciones/tests antiguas que aún no aceptan phone_hash.
                    if "phone_hash" not in str(exc):
                        raise
                    result = await get_pronostico(comuna, dias=2)
            else:
                result = await get_pronostico(comuna, dias=2)
        else:
            if comuna is not None:
                coords = resolver_comuna(comuna) or (-38.23, -72.68)
                if phone_hash:
                    result = await get_weather(
                        lat=coords[0],
                        lon=coords[1],
                        phone_hash=phone_hash,
                    )
                else:
                    result = await get_weather(lat=coords[0], lon=coords[1])
            else:
                result = await get_weather(phone_hash=phone_hash) if phone_hash else await get_weather()
        if result:
            return str(result), ""
        return None, "OpenMeteo no entregó datos para esa consulta."
    except (TimeoutError, RuntimeError, OSError, ValueError) as exc:
        logger.warning(
            "Error en clima de consulta compuesta — error=%s",
            type(exc).__name__,
        )
        return None, "No pude obtener el clima en este momento."


async def _force_compound_keyword_tools(
    query_text: str,
    phone_hash: str | None = None,
) -> str:
    """Responde precio y clima sin LLM, conservando resultados parciales.

    ODEPA y OpenMeteo se consultan de forma independiente. Así, una fuente
    caída no oculta el dato válido de la otra y el agricultor recibe un aviso
    explícito por el bloque que no se pudo completar.
    """
    q = query_text.strip().lower()
    price_result, weather_result = await asyncio.gather(
        _compound_price_block(q, phone_hash),
        _compound_weather_block(q, phone_hash),
    )
    price_data, price_notice = price_result
    weather_data, weather_notice = weather_result

    price_text = price_data or price_notice
    weather_text = weather_data or weather_notice
    blocks = (
        f"ODEPA — Precio:\n{price_text}",
        f"OpenMeteo — Clima:\n{weather_text}",
    )
    if price_data is None and weather_data is None:
        return "No pude completar ninguno de los dos datos solicitados.\n\n" + "\n\n".join(blocks)
    return "\n\n".join(blocks)


async def _force_multi_price_tool(
    query_text: str,
    phone_hash: str | None = None,
) -> str | None:
    """Resuelve precios de varias cláusulas sin depender del LLM.

    El fast-path anterior solo podía tomar el primer producto y el último
    mercado de una pregunta compuesta. Separar cláusulas cortas mantiene el
    flujo determinista para "papa en Temuco y tomate en Chillán" y para
    "papa en Temuco y en Puerto Montt".
    """
    q = query_text.strip().lower()
    clauses = re.split(r"\s+y\s+", q)
    if len(clauses) < 2:
        return None

    from app.core.database import SessionLocal
    from app.services.odepa_service import get_price_for_llm

    requests: list[tuple[str, str]] = []
    previous_product: str | None = None
    for clause in clauses:
        exact_products = _extract_product_mentions(clause)
        clause_product = exact_products[0] if exact_products else None
        market = _extract_mercado_from_query(clause) or ""
        if clause_product is None and not market:
            # No inventar una segunda consulta para una cláusula conversacional
            # como "papa y clima"; requiere producto o zona explícitos.
            continue
        product = clause_product or previous_product
        if product is None:
            continue
        previous_product = product
        requests.append((product, market))

    if len(requests) < 2:
        return None

    session = SessionLocal()
    try:
        responses: list[str] = []
        for product, market in requests:
            response = await asyncio.to_thread(
                get_price_for_llm,
                session,
                producto=product,
                mercado=market,
                phone_hash=phone_hash,
            )
            responses.append(response)
        logger.info("Fallback multi-zona forzado — consultas=%d", len(responses))
        return "\n\n".join(responses)
    except (SQLAlchemyError, OSError, RuntimeError, ValueError) as exc:
        logger.warning(
            "Error DB en fallback multi-zona — error=%s",
            type(exc).__name__,
        )
        return None
    finally:
        session.close()


async def _force_directorio_tool(query_text: str) -> str | None:
    """Resuelve el directorio por keywords cuando el LLM no llama la tool."""
    q = query_text.strip().lower()
    if not any(keyword in q for keyword in _DIRECTORIO_KEYWORDS):
        return None

    comuna = _extract_directorio_comuna(q)
    if comuna is None:
        return None

    from app.core.database import SessionLocal
    from app.services.directorio_agricola_service import get_directorio_agricola

    tipo = _extract_directorio_tipo(q)
    session = SessionLocal()
    try:
        result = await asyncio.to_thread(get_directorio_agricola, session, comuna, tipo)
        logger.info("Fallback tool forzado — tool=get_directorio_agricola")
        return result
    except (SQLAlchemyError, RuntimeError, ValueError, OSError) as exc:
        logger.warning(
            "Error en fallback directorio — error=%s",
            type(exc).__name__,
        )
        return None
    finally:
        session.close()


async def _force_keyword_tool(query_text: str, phone_hash: str | None = None) -> str | None:
    """Orquestador de fallback por keywords: directorio → venta → precio → clima.

    Cuando el LLM no genera <tool_call>, detectamos keywords en la consulta
    para forzar la tool correspondiente directamente sin pasar por el LLM.

    Orden de precedencia:
    1. Directorio (sede y comuna) -> get_directorio_agricola
    2. Venta (N kilos de producto) -> calculate_sale_value
    3. Precio (producto agrícola, presente/pasado) -> get_price/get_price_history
    4. Clima (keywords climáticos) -> get_weather

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

    # 0. Directorio agrícola: dirección y teléfono solo desde el snapshot local.
    forced = await _force_directorio_tool(query_text)
    if forced:
        return forced

    # 0.5. Detectar "N kilos de producto" -> calculate_sale_value (Issue #104).
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

    forced = await _force_multi_price_tool(query_text, phone_hash=phone_hash)
    if forced:
        return forced

    q = query_text.strip().lower()

    # 1. Detectar productos agrícolas en la consulta.
    product = _extract_product_from_query(q)
    if product:
        # Keywords de precio pasado: "estaba", "semana pasada", "ayer", etc.
        # -> historial en vez de precio actual.
        es_historia = any(kw in q for kw in _PRECIO_HISTORIA_KW)

        session = SessionLocal()
        try:
            # Llamada en thread pool: las tools de precio son síncronas
            # (query SQLite) y no deben bloquear el event loop.
            mercado = _extract_mercado_from_query(q) or ""
            if es_historia:
                result = await asyncio.to_thread(get_price_history_for_llm, session, producto=product)
            else:
                result = await asyncio.to_thread(
                    get_price_for_llm,
                    session,
                    producto=product,
                    mercado=mercado,
                    phone_hash=phone_hash,
                )
            # Solo retornar si encontró datos reales (no "No tengo datos...").
            if not result.startswith(FALLBACK_SALE_MESSAGES["no_data"]):
                logger.info(
                    "Fallback tool forzado — tool=%s",
                    "get_price_history" if es_historia else "get_price",
                )
                return result
        except SQLAlchemyError as exc:
            # Fire-and-forget: un error de DB (database is locked, disk I/O)
            # no debe romper el pipeline. Se loguea y se cae al bloque de clima.
            logger.warning(
                "Error DB en fallback precio — error=%s",
                type(exc).__name__,
            )
        finally:
            session.close()

    # 2. Detectar keywords de corpus/boletines (antes que clima).
    corpus_result = await _force_corpus_search(query_text)
    if corpus_result:
        return corpus_result

    # 3. Detectar keywords de clima.
    # Se incluyen las formas VERBALES ademas de los sustantivos: la manera mas
    # natural de preguntar es "¿va a llover?", y sin el verbo la consulta no
    # matcheaba ninguna keyword y terminaba en el LLM (lento y con timeout).
    clima_kw = [
        "clima",
        "tiempo",
        "temperatura",
        "lluvia",
        "lloviendo",
        "llover",
        "llueve",
        "lloverá",
        "llovera",
        "frio",
        "frío",
        "calor",
        "humedad",
        "viento",
        "pronóstico",
        "pronostico",
        "nublado",
        "despejado",
        "helada",
        "helar",
        "granizo",
        "nieve",
        "histórico",
        "historico",
        "invierno",
        "otoño",
        "otono",
        "primavera",
        "verano",
        "llovió",
        "llovio",
        "año pasado",
        "ano pasado",
    ]
    if any(kw in q for kw in clima_kw):
        from app.services.weather_service import (
            get_clima_historico_multianual,
            get_pronostico,
            get_weather,
        )

        # "¿va a llover MANANA?" pide pronostico, no el clima de ahora.
        # Antes solo existia get_weather (clima actual) y la respuesta no
        # contestaba la pregunta: decia como esta ahora, no como estara.
        es_futuro = any(kw in q for kw in _CLIMA_FUTURO_KW)

        try:
            comuna = _extract_comuna_from_query(q)
            if any(kw in q for kw in _CLIMA_HISTORICO_KW):
                anos, temporada, anio, metrica = _extract_historico_request(q)
                result = await get_clima_historico_multianual(
                    comuna or "Traiguén",
                    anos=anos,
                    temporada=temporada,
                    anio=anio,
                    metrica=metrica,
                )
                herramienta = "get_clima_historico_multianual"
            elif es_futuro:
                if phone_hash:
                    try:
                        result = await get_pronostico(comuna, dias=2, phone_hash=phone_hash)
                    except TypeError as exc:
                        if "phone_hash" not in str(exc):
                            raise
                        result = await get_pronostico(comuna, dias=2)
                else:
                    result = await get_pronostico(comuna, dias=2)
                herramienta = "get_pronostico"
            else:
                if comuna is not None:
                    from app.services.weather_service import resolver_comuna

                    coords = resolver_comuna(comuna)
                    if coords is None:
                        # Traiguén: default del piloto si la comuna no está mapeada.
                        lat, lon = -38.23, -72.68
                    else:
                        lat, lon = coords
                    if phone_hash:
                        result = await get_weather(lat=lat, lon=lon, phone_hash=phone_hash)
                    else:
                        result = await get_weather(lat=lat, lon=lon)
                else:
                    result = await get_weather(phone_hash=phone_hash) if phone_hash else await get_weather()
                herramienta = "get_weather"
            if result:
                logger.info(
                    "Fallback tool forzado — tool=%s",
                    herramienta,
                )
                return str(result)
        except (RuntimeError, ValueError, OSError) as exc:
            logger.warning(
                "Error en fallback clima — error=%s",
                type(exc).__name__,
            )

    return None
