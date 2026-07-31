"""Servicio ODEPA: descarga, parseo y carga de CSV de precios mayoristas.

Flujo: download_csv() -> parse_csv() -> upsert_prices(). El orquestador
sync_odepa() encadena los tres para uso del cron job y de los tests.

El CSV de ODEPA cambia de encabezados entre boletines, por eso el parser
resuelve columnas por palabra clave (case-insensitive) en vez de exigir
nombres exactos. Filas con datos inválidos se omiten con warning; el
batch completo solo falla si faltan columnas requeridas (schema roto).
"""

import csv
import datetime
import io
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import httpx
from sqlalchemy import func, select
from sqlalchemy import tuple_ as sa_tuple
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.formato import formatear_pesos
from app.models.odepa_price import OdepaPrice
from app.models.user_prefs import UserPrefs

logger = logging.getLogger(__name__)

# Timeout de descarga. ODEPA publica CSVs pequeños (<1MB), 30s sobra.
_TIMEOUT_SEGUNDOS = 30

# ── Mapeo comuna → mercado ODEPA más cercano (Issue #89) ──────────
# Diccionario estático: 15 mercados ODEPA, dataset chico, sin dependencia externa.
# Normalizado a lowercase para matching case-insensitive.
# Cobertura: comunas del piloto Traiguén + regiones principales.
COMUNA_TO_MERCADO: dict[str, str] = {
    # Región de La Araucanía
    "traiguén": "Vega Modelo de Temuco",
    "traiguen": "Vega Modelo de Temuco",
    "temuco": "Vega Modelo de Temuco",
    "padre las casas": "Vega Modelo de Temuco",
    "nueva imperial": "Vega Modelo de Temuco",
    "lautaro": "Vega Modelo de Temuco",
    "villarrica": "Vega Modelo de Temuco",
    "pucón": "Vega Modelo de Temuco",
    "pucon": "Vega Modelo de Temuco",
    "angol": "Vega Modelo de Temuco",
    "collipulli": "Vega Modelo de Temuco",
    "pitrufquén": "Vega Modelo de Temuco",
    "pitrufquen": "Vega Modelo de Temuco",
    # Región del Biobío
    "concepción": "Vega Monumental de Concepción",
    "concepcion": "Vega Monumental de Concepción",
    "talcahuano": "Vega Monumental de Concepción",
    "los ángeles": "Vega Monumental de Concepción",
    "los angeles": "Vega Monumental de Concepción",
    "chillán": "Vega Chillán",
    "chillan": "Vega Chillán",
    # Región de Los Lagos
    "puerto montt": "Vega de Puerto Montt",
    "osorno": "Vega de Osorno",
    "castro": "Vega de Puerto Montt",
    # Región de Valparaíso
    "valparaíso": "Vega de Valparaíso",
    "valparaiso": "Vega de Valparaíso",
    "viña del mar": "Vega de Valparaíso",
    "vina del mar": "Vega de Valparaíso",
    "quillota": "Vega de Valparaíso",
    "san antonio": "Vega de Valparaíso",
    # Región Metropolitana
    "santiago": "Mercado Mayorista Lo Valledor de Santiago",
    "maipú": "Mercado Mayorista Lo Valledor de Santiago",
    "maipu": "Mercado Mayorista Lo Valledor de Santiago",
    "puente alto": "Mercado Mayorista Lo Valledor de Santiago",
    "san bernardo": "Mercado Mayorista Lo Valledor de Santiago",
    "la florida": "Mercado Mayorista Lo Valledor de Santiago",
    # Región de O'Higgins
    "rancagua": "Vega de Rancagua",
    "san fernando": "Vega de San Fernando",
    "santa cruz": "Vega de San Fernando",
    # Región del Maule
    "talca": "Vega de Talca",
    "curicó": "Vega de Curicó",
    "curico": "Vega de Curicó",
    "linares": "Vega de Talca",
    # Región de Ñuble
    "quirihue": "Vega Chillán",
    "yungay": "Vega Chillán",
    # Región de Atacama
    "copiapó": "Vega de Copiapó",
    "copiapo": "Vega de Copiapó",
    # Región de Coquimbo
    "la serena": "Vega de La Serena",
    "coquimbo": "Vega de La Serena",
    # Región de Arica y Parinacota
    "arica": "Agrícola del Norte S.A. de Arica",
    # Región de Tarapacá
    "iquique": "Vega de Iquique",
}

# Mercado de referencia nacional (Issue #83).
_MERCADO_DEFAULT = "Mercado Mayorista Lo Valledor de Santiago"

# Substrings a buscar en cada header normalizado (strip + lower).
# COLS: cada tupla es un grupo de substrings a buscar en los headers (case-insensitive).
# El orden de las claves dentro de cada tupla define prioridad: la primera que
# matchea gana. Esto permite preferir "promedio" sobre "precio" cuando hay
# múltiples columnas de precio (min, max, promedio) como en el CSV real de ODEPA.
_COLUMNA_PRODUCTO = ("producto",)
_COLUMNA_MERCADO = ("mercado", "lugar", "plaza", "feria")
_COLUMNA_PRECIO = ("promedio", "precio")
_COLUMNA_UNIDAD = ("unidad", "medida")
_COLUMNA_FECHA = ("fecha", "día", "dia")

# Formatos de fecha que ODEPA usa históricamente en sus boletines.
_FORMATOS_FECHA = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d")


class OdepaSyncError(Exception):
    """Error durante la sincronización ODEPA (red, schema o contenido)."""


@dataclass(frozen=True)
class OdepaCsvRecord:
    """Un registro parseado del CSV, listo para upsert."""

    producto: str
    mercado: str
    precio_kg: Decimal
    unidad: str
    fecha: datetime.date


@dataclass(frozen=True)
class SyncResult:
    """Resultado de una ejecución de sync_odepa."""

    insertados: int = 0
    actualizados: int = 0

    @property
    def total(self) -> int:
        """Total de filas procesadas (insertadas + actualizadas)."""
        return self.insertados + self.actualizados


async def download_csv(url: str, timeout: float = _TIMEOUT_SEGUNDOS) -> str:
    """Descarga el CSV ODEPA como texto.

    Lanza OdepaSyncError ante errores de red o HTTP no-2xx, para que el
    job los atrape y loguee sin crashear el proceso.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise OdepaSyncError(f"ODEPA respondió HTTP {exc.response.status_code} para {url}") from exc
    except httpx.RequestError as exc:
        raise OdepaSyncError(f"Error de red al descargar CSV ODEPA: {exc}") from exc

    texto = response.text
    if not texto.strip():
        raise OdepaSyncError("CSV ODEPA vacío")

    # ODEPA publica CSVs con BOM UTF-8 (﻿). Sin stripping, el BOM se pega
    # al primer header y csv.DictReader lo interpreta como parte del nombre
    # de columna (ej. '﻿"Fecha"' en vez de 'Fecha'). El parser lo tolera
    # porque las claves se buscan con substring, pero es frágil.
    if texto.startswith("﻿"):
        texto = texto[1:]

    # ODEPA es un portal gubernamental. Si cambia la URL o hay un error
    # interno, puede devolver HTML en vez de CSV. Detectarlo temprano
    # evita que el parser intente interpretar HTML como CSV y tire
    # errores confusos como "sin columnas requeridas".
    _prefix = texto.lstrip()[:256].lower()
    if any(marcador in _prefix for marcador in ("<!doctype", "<html", "<head", "<body", "<meta", "<title")):
        raise OdepaSyncError(f"ODEPA devolvió HTML en vez de CSV. ¿Cambió la URL? ({url})")

    return texto


async def download_csv_with_fallback(timeout: float = _TIMEOUT_SEGUNDOS) -> str:
    """Descarga el CSV ODEPA con fallback a la API CKAN si la URL principal falla (#175).

    Intenta primero ``settings.odepa_csv_url`` (comportamiento actual, sin
    cambios). Si falla, intenta ``settings.odepa_fallback_urls`` — un
    endpoint CKAN ``datastore_search`` que devuelve JSON en vez de CSV —
    y reserializa los registros como CSV para reusar el mismo parser
    (misma detección de columnas por palabra clave, sin duplicar lógica).
    """
    try:
        return await download_csv(settings.odepa_csv_url, timeout=timeout)
    except OdepaSyncError as exc_primaria:
        logger.warning(
            "URL primaria ODEPA falló — error=%s fallback=ckan",
            type(exc_primaria).__name__,
        )
        try:
            return await _download_csv_via_ckan(settings.odepa_fallback_urls, timeout=timeout)
        except OdepaSyncError as exc_fallback:
            raise OdepaSyncError(
                f"Fallaron URL primaria y fallback CKAN. Primaria: {exc_primaria}. Fallback: {exc_fallback}"
            ) from exc_fallback


async def _download_csv_via_ckan(url: str, timeout: float) -> str:
    """Descarga precios vía CKAN datastore_search y los re-serializa como CSV.

    NOTA: la correspondencia de nombres de columna entre el CSV principal
    y los campos que devuelve datastore_search asume que CKAN expone el
    mismo nombre de columna del recurso original. Validar contra la API
    real antes de confiar en esto en producción — no hay forma de
    verificarlo sin acceso a datos.odepa.gob.cl en este entorno.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise OdepaSyncError(f"CKAN respondió HTTP {exc.response.status_code} para {url}") from exc
    except httpx.RequestError as exc:
        raise OdepaSyncError(f"Error de red en fallback CKAN: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise OdepaSyncError("Fallback CKAN no devolvió JSON válido") from exc

    if not isinstance(data, dict) or not data.get("success"):
        raise OdepaSyncError("Fallback CKAN respondió success=false o formato inesperado")

    # ``result`` puede venir null o como lista si CKAN cambia de forma: el
    # default de .get() solo aplica si falta la clave, no si vale None, y un
    # AttributeError aquí escaparía al OdepaSyncError que espera el caller.
    result = data.get("result")
    if not isinstance(result, dict):
        raise OdepaSyncError("Fallback CKAN devolvió result con formato inesperado")

    records = result.get("records")
    if not isinstance(records, list) or not records:
        raise OdepaSyncError("Fallback CKAN sin registros")
    if not all(isinstance(record, dict) for record in records):
        raise OdepaSyncError("Fallback CKAN devolvió registros que no son objetos")

    # Union de claves en orden de aparición, no solo las del primer registro:
    # si uno trae una columna extra, DictWriter lanzaría ValueError y el
    # fallback moriría con la excepción equivocada. Con la union, las claves
    # ausentes quedan vacías y parse_csv descarta esas filas por su cuenta.
    fieldnames: list[str] = []
    for record in records:
        fieldnames.extend(key for key in record if key not in fieldnames)
    if any(len(record) != len(fieldnames) for record in records):
        logger.warning(
            "Fallback CKAN devolvió registros con esquema heterogéneo — columnas=%d",
            len(fieldnames),
        )

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(records)
    return buffer.getvalue()


def _resolver_columna(headers: Sequence[str], claves: tuple[str, ...]) -> str | None:
    """Devuelve el primer header que contiene alguna de las claves, con prioridad.

    Itera las *claves* en orden (prioridad), y para cada clave busca entre
    los headers. La primera clave que matchea gana — esto permite preferir
    "promedio" sobre "precio" cuando hay múltiples columnas de precio.

    Búsqueda case-insensitive sobre header normalizado (strip + lower).
    """
    for clave in claves:
        for header in headers:
            if clave in header.strip().lower():
                return header
    return None


def _normalizar_precio(valor: str) -> str:
    """Normaliza un string de precio a formato anglosajón para Decimal.

    ODEPA es una institución chilena y sus CSVs pueden usar formato local:
    punto como separador de miles y coma como decimal. Decimal de Python usa
    formato anglosajón (punto = decimal), así que hay que normalizar antes.

    Reglas (aplicadas tras quitar '$' y espacios):
    - '1500'           -> '1500'      (sin cambio)
    - '800,50'         -> '800.50'    (coma = decimal)
    - '1.500'          -> '1500'      (punto = miles)
    - '1.500.000'      -> '1500000'   (puntos = miles)
    - '1.500,50'       -> '1500.50'   (punto=miles, coma=decimal)
    - '800.50'         -> '800.50'    (anglosajón: 2 decimales, no miles)
    """
    limpio = valor.replace("$", "").replace(" ", "")

    if "," in limpio:
        # Coma presente => formato chileno. El punto (si lo hay) es miles.
        return limpio.replace(".", "").replace(",", ".")

    if "." in limpio:
        # Solo punto: ambiguous. Si todos los grupos post-entero son de 3
        # dígitos, es separador de miles ('1.500', '1.500.000'). Si no,
        # se trata como decimal anglosajón ('800.50', '1.5').
        partes = limpio.split(".")
        grupos = partes[1:]
        if grupos and all(g.isdigit() and len(g) == 3 for g in grupos):
            return "".join(partes)

    return limpio


def _parsear_precio(valor: str) -> Decimal:
    """Convierte string de precio a Decimal. Lanza ValueError si inválido.

    Normaliza formato chileno (coma decimal, punto miles) antes de Decimal.
    """
    if not valor:
        raise ValueError("precio vacío")
    original = valor.strip()
    limpio = _normalizar_precio(original)
    # Validación estructural: después de normalizar solo debe haber dígitos
    # y a lo más un punto decimal. Cualquier otra cosa es ruido no parseable.
    if not limpio.replace(".", "", 1).replace("-", "", 1).isdigit():
        raise ValueError(f"precio inválido: {original!r}")
    try:
        return Decimal(limpio)
    except InvalidOperation as exc:
        raise ValueError(f"precio inválido: {original!r}") from exc


def _parsear_fecha(valor: str) -> datetime.date:
    """Convierte string de fecha a date probando formatos conocidos de ODEPA."""
    valor = valor.strip()
    for fmt in _FORMATOS_FECHA:
        try:
            return datetime.datetime.strptime(valor, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"fecha inválida: {valor!r}")


def parse_csv(
    contenido: str,
    productos_filter: Sequence[str] | None = None,
) -> list[OdepaCsvRecord]:
    """Parsea el contenido CSV a una lista de OdepaCsvRecord.

    productos_filter: si se pasa, solo se conservan esos productos (lowercase).
    Una fila con datos inválidos se omite con warning; no aborta el batch.
    Lanza OdepaSyncError si el CSV está vacío o no tiene las columnas mínimas.
    """
    if not contenido.strip():
        raise OdepaSyncError("CSV ODEPA vacío")

    reader = csv.DictReader(io.StringIO(contenido))
    headers = reader.fieldnames or []
    if not headers:
        raise OdepaSyncError("CSV ODEPA sin headers")

    col_producto = _resolver_columna(headers, _COLUMNA_PRODUCTO)
    col_mercado = _resolver_columna(headers, _COLUMNA_MERCADO)
    col_precio = _resolver_columna(headers, _COLUMNA_PRECIO)
    col_fecha = _resolver_columna(headers, _COLUMNA_FECHA)
    col_unidad = _resolver_columna(headers, _COLUMNA_UNIDAD)

    faltantes = [
        nombre
        for nombre, col in [
            ("producto", col_producto),
            ("mercado", col_mercado),
            ("precio", col_precio),
            ("fecha", col_fecha),
        ]
        if col is None
    ]
    if faltantes:
        raise OdepaSyncError(f"CSV ODEPA sin columnas requeridas: {faltantes}. Headers: {headers}")

    # mypy: tras el guard de faltantes, todas las columnas requeridas son str.
    assert col_producto is not None
    assert col_mercado is not None
    assert col_precio is not None
    assert col_fecha is not None

    # Detecta colisiones: si dos roles apuntan al mismo header, el substring
    # match de _resolver_columna produjo un falso positivo (ej. "precio_mercado"
    # matchea "precio" y "mercado" a la vez). Esto corrompería datos silenciosamente.
    roles_resueltos: dict[str, str] = {}
    for rol, col in [
        ("producto", col_producto),
        ("mercado", col_mercado),
        ("precio", col_precio),
        ("fecha", col_fecha),
    ]:
        if col in roles_resueltos:
            raise OdepaSyncError(
                f"Colisión de columnas: header '{col}' resuelto como"
                f" '{roles_resueltos[col]}' y '{rol}'. Headers: {headers}"
            )
        roles_resueltos[col] = rol

    # None = sin filtro (sincroniza todo). Secuencia vacía = no sincronizar nada.
    # Distinguir ambos es importante: settings.odepa_productos_list puede ser []
    # si ODEPA_PRODUCTOS='' o ODEPA_PRODUCTOS=',' en .env.
    filtro = None if productos_filter is None else {p.lower() for p in productos_filter}

    registros: list[OdepaCsvRecord] = []

    # num_fila empieza en 2: la línea 1 es el header del CSV.
    for num_fila, fila in enumerate(reader, start=2):
        producto = (fila.get(col_producto) or "").strip().lower()
        if not producto:
            continue
        if filtro is not None and producto not in filtro:
            continue

        mercado = (fila.get(col_mercado) or "").strip()
        if not mercado:
            logger.warning("Fila %d omitida: mercado vacío", num_fila)
            continue

        try:
            precio = _parsear_precio(fila.get(col_precio, ""))
            fecha = _parsear_fecha(fila.get(col_fecha, ""))
        except ValueError as exc:
            logger.warning(
                "Fila ODEPA omitida — row=%d error=%s",
                num_fila,
                type(exc).__name__,
            )
            continue

        unidad = (fila.get(col_unidad) or "").strip() or "kg"
        registros.append(
            OdepaCsvRecord(
                producto=producto,
                mercado=mercado,
                precio_kg=precio,
                unidad=unidad,
                fecha=fecha,
            )
        )

    return registros


def upsert_prices(session: Session, registros: Sequence[OdepaCsvRecord]) -> tuple[int, int]:
    """Hace upsert de registros en odepa_prices. Devuelve (insertados, actualizados).

    Usa INSERT ... ON CONFLICT DO UPDATE sobre (producto, mercado, fecha).
    Si la tupla existe, actualiza precio_kg y unidad: ODEPA puede corregir
    precios de una fecha ya publicada. updated_at no lleva onupdate (metadata
    append-only), así que conserva la fecha de inserción original.

    Los duplicados intra-batch (misma tupla producto/mercado/fecha) se
    deduplican antes del upsert: gana la última aparición, consistente con
    el valor `excluded` que SQLite aplica en ON CONFLICT DO UPDATE. Sin esto,
    el conteo de insertados/actualizados se inflaría, reportando operaciones
    que ON CONFLICT colapsa en una sola.

    Procesa en chunks de 200 para no exceder el limite de 999 parametros
    de SQLite: cada tupla en la deteccion usa 3 parametros.
    """
    if not registros:
        return (0, 0)

    # Deduplica por (producto, mercado, fecha). Orden de dict preserva inserción;
    # al reasignar la clave, el valor final es la última aparición del batch.
    unicos: dict[tuple[str, str, datetime.date], OdepaCsvRecord] = {}
    for r in registros:
        unicos[(r.producto, r.mercado, r.fecha)] = r
    registros_unicos = list(unicos.values())

    # SQLite max 999 parametros por query. Cada tupla en IN (VALUES ...)
    # consume 3 parametros (producto, mercado, fecha). Chunk de 200 deja
    # margen para 600 parametros en la query de deteccion + 1000 en el insert.
    _chunk = 200
    total_insertados = 0
    total_actualizados = 0

    for i in range(0, len(registros_unicos), _chunk):
        chunk = registros_unicos[i : i + _chunk]

        # Detecta tuplas existentes para diferenciar inserts de updates.
        claves = [(r.producto, r.mercado, r.fecha) for r in chunk]
        q = select(OdepaPrice.producto, OdepaPrice.mercado, OdepaPrice.fecha).where(
            sa_tuple(OdepaPrice.producto, OdepaPrice.mercado, OdepaPrice.fecha).in_(claves)
        )
        existentes = {tuple(row) for row in session.execute(q).all()}

        valores = [
            {
                "producto": r.producto,
                "mercado": r.mercado,
                "precio_kg": r.precio_kg,
                "unidad": r.unidad,
                "fecha": r.fecha,
                "fuente": "ODEPA",
            }
            for r in chunk
        ]
        stmt = sqlite_insert(OdepaPrice).values(valores)
        stmt = stmt.on_conflict_do_update(
            index_elements=["producto", "mercado", "fecha"],
            set_={
                "precio_kg": stmt.excluded.precio_kg,
                "unidad": stmt.excluded.unidad,
            },
        )
        session.execute(stmt)

        actualizados = sum(1 for r in chunk if (r.producto, r.mercado, r.fecha) in existentes)
        insertados = len(chunk) - actualizados
        total_insertados += insertados
        total_actualizados += actualizados

    session.commit()
    return (total_insertados, total_actualizados)


async def sync_odepa(session: Session | None = None) -> SyncResult:
    """Orquesta descarga + parseo + upsert usando settings del proyecto.

    Si no se pasa session, crea una propia y la cierra al terminar. En tests
    se inyecta la session fixture para aislamiento.
    """
    if session is None:
        from app.core.database import SessionLocal

        session = SessionLocal()
        cerrar = True
    else:
        cerrar = False

    _ok = False
    try:
        contenido = await download_csv_with_fallback()
        registros = parse_csv(contenido, settings.odepa_productos_list)

        if not registros:
            logger.warning(
                "Sync ODEPA: 0 registros tras filtro productos=%s",
                settings.odepa_productos_list,
            )
            _ok = True  # Sin cambios en DB, sesión limpia.
            return SyncResult()

        insertados, actualizados = upsert_prices(session, registros)
        logger.info(
            "Sync ODEPA OK: %d insertados, %d actualizados (productos=%s)",
            insertados,
            actualizados,
            settings.odepa_productos_list,
        )
        # Invalidar cache de resultados de tools: los precios cambiaron.
        from app.services.llm_service import clear_tool_result_cache
        clear_tool_result_cache()
        _ok = True
        return SyncResult(insertados=insertados, actualizados=actualizados)
    finally:
        if cerrar:
            if not _ok:
                session.rollback()
            session.close()


# ── Funciones de consulta para Tool Calling (Issue #16) ──────────


def query_latest_price(session: Session, producto: str, mercado: str) -> OdepaPrice | None:
    """Busca el precio más reciente para un producto en un mercado.

    Normaliza producto (lower, strip) y mercado (lower, strip).
    Usa match exacto case-insensitive para evitar ambigüedad:
    "Lo Valledor" no debe devolver datos de "Lo Valledor Sur".
    Lanza ValueError si producto o mercado están vacíos.
    Retorna None si no hay datos en la DB.
    """
    if not producto or not producto.strip():
        raise ValueError("producto no puede estar vacío")
    if not mercado or not mercado.strip():
        raise ValueError("mercado no puede estar vacío")

    producto_norm = producto.strip().lower()
    mercado_norm = mercado.strip().lower()

    q = (
        select(OdepaPrice)
        .where(
            func.lower(OdepaPrice.producto) == producto_norm,
            func.lower(OdepaPrice.mercado) == mercado_norm,
        )
        .order_by(OdepaPrice.fecha.desc())
        .limit(1)
    )
    return session.scalars(q).first()


def query_latest_by_product(session: Session, producto: str) -> dict[str, OdepaPrice]:
    """Precio más reciente por mercado para un producto. Una sola query.

    Alternativa a llamar query_latest_price por cada mercado (N+1).
    SQLite maneja <100 filas en una query; el diccionario se arma en Python.
    """
    if not producto or not producto.strip():
        raise ValueError("producto no puede estar vacío")

    producto_norm = producto.strip().lower()
    q = (
        select(OdepaPrice)
        .where(func.lower(OdepaPrice.producto) == producto_norm)
        .order_by(OdepaPrice.mercado, OdepaPrice.fecha.desc())
    )
    rows = session.scalars(q).all()
    # Primera fila por mercado = la más reciente (orden desc por fecha)
    seen: set[str] = set()
    result: dict[str, OdepaPrice] = {}
    for row in rows:
        if row.mercado not in seen:
            seen.add(row.mercado)
            result[row.mercado] = row
    return result


# Contenedores de peso con conversión no ambigua a kilos. Docenas, atados,
# paquetes y cajas "por unidades" quedan fuera: su peso total es ambiguo
# (ej: "$/docena de atados (12 kilos)" no aclara si son 12 kilos la docena
# o cada atado) y una conversión inventada sería peor que no darla.
_PESO_CONTENEDOR_RE = re.compile(
    r"^(?:saco|malla|caja|bandeja|bins|cuna|envase)\s*\(?\s*"
    r"(\d+(?:[.,]\d+)?)\s+kilos?\)?"
    r"(?:\s+(?:granel|empedrada|embalada|importada))?$",
    re.IGNORECASE,
)

# Contenedores a los que se les inserta "de" para el texto hablado:
# "saco 25 kilos" -> "saco de 25 kilos".
_CONTENEDOR_HABLADO_RE = re.compile(
    r"^(saco|malla|caja|bandeja|bins|cuna|envase|paquete|atado|trenza|bolsa)\s+(\d)",
    re.IGNORECASE,
)


def _normalizar_unidad(unidad: str) -> str:
    """Quita el prefijo '$/' de la unidad ODEPA: '$/saco 25 kilos' -> 'saco 25 kilos'."""
    return unidad.strip().removeprefix("$").removeprefix("/").strip()


def _es_unidad_kilo(unidad: str) -> bool:
    """True si el precio ya está expresado por kilo ('kg', '$/kilo (...)')."""
    norm = _normalizar_unidad(unidad).lower()
    return norm == "kg" or norm.startswith("kilo")


def _kilos_por_unidad(unidad: str) -> Decimal | None:
    """Kilos que contiene la unidad de venta, o None si no es convertible.

    Solo convierte contenedores simples con peso explícito (saco/malla/caja/
    bandeja/bins/cuna/envase de N kilos, con sufijos granel/empedrada/
    embalada/importada). El resto retorna None: mejor no dar equivalencia
    que darla mal.
    """
    match = _PESO_CONTENEDOR_RE.match(_normalizar_unidad(unidad))
    if match is None:
        return None
    kilos = Decimal(match.group(1).replace(",", "."))
    return kilos if kilos > 0 else None


def _unidad_hablada(unidad: str) -> str:
    """Convierte la unidad ODEPA a texto hablable para TTS.

    '$/saco 25 kilos' -> 'saco de 25 kilos'
    '$/bins (400 kilos)' -> 'bins de 400 kilos'
    '$/docena de atados' -> 'docena de atados'
    """
    texto = _normalizar_unidad(unidad).replace("(", "").replace(")", "")
    texto = re.sub(r"\s+", " ", texto).strip()
    return _CONTENEDOR_HABLADO_RE.sub(r"\1 de \2", texto)


def _formatear_pesos(precio: Decimal) -> str:
    """Formatea un Decimal como pesos hablados: 1200 -> '1.200 pesos'.

    Delega en ``app.core.formato.formatear_pesos``, que es la fuente unica.
    Antes esta version verbalizaba los decimales que trae ODEPA y decia
    "14.232 coma 14 pesos": el peso chileno no tiene centavos en circulacion,
    asi que eso no significa nada para el productor.

    Se conserva el nombre privado porque lo usan varias funciones del modulo.
    """
    return formatear_pesos(precio)


def format_price_text(record: OdepaPrice) -> str:
    """Formatea un registro OdepaPrice como texto natural en español chileno.

    ODEPA publica precios en la unidad de venta de cada mercado (saco de
    25 kilos, bandeja de 18 kilos, docena de atados...), no siempre por kilo.
    El texto respeta esa unidad y, cuando la conversión es segura, agrega
    la equivalencia aproximada por kilo.

    Por kilo:      "Papa está a 850 pesos el kilo en Vega Central, según ODEPA,
                    precio del 19/06/2026."
    Convertible:   "Papa está a 8.833 pesos por saco de 25 kilos en Lo Valledor,
                    unos 353 pesos el kilo, según ODEPA, precio del 03/07/2026."
    No convertible: "Lechuga está a 1.200 pesos por docena de atados en Lo Valledor,
                    según ODEPA, precio del 03/07/2026."

    La cita "según ODEPA" se incluye SIEMPRE en el dato retornado por esta función
    (capa determinista). El system prompt refuerza que el LLM la conserve si reformula.
    Diseño deliberado de defensa en profundidad (Issue #95):
    - Capa 1 (determinista): el hardcode en formato_price_text() garantiza la presencia.
    - Capa 2 (LLM): la instrucción del prompt previene que sea borrada en reformulaciones.
    Sin ambas capas, el LLM 3B podría descartar la fuente buscando ser "conciso".

    Sin artículo para evitar errores de género (el tomate, la papa).
    """
    precio_str = _formatear_pesos(record.precio_kg)
    fecha_str = record.fecha.strftime("%d/%m/%Y")
    producto_str = f"{record.producto[0].upper()}{record.producto[1:]}"

    if _es_unidad_kilo(record.unidad):
        return f"{producto_str} está a {precio_str} el kilo en {record.mercado}, según ODEPA, precio del {fecha_str}."

    unidad_str = _unidad_hablada(record.unidad)
    kilos = _kilos_por_unidad(record.unidad)
    equivalencia = ""
    if kilos is not None and kilos != 1:
        por_kilo = (record.precio_kg / kilos).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        equivalencia = f", unos {_formatear_pesos(por_kilo)} el kilo"

    return (
        f"{producto_str} está a {precio_str} por {unidad_str} en {record.mercado}"
        f"{equivalencia}, según ODEPA, precio del {fecha_str}."
    )


def _resolve_mercado_cercano(session: Session, phone_hash: str) -> str | None:
    """Resuelve el mercado ODEPA más cercano según la comuna registrada.

    Busca la comuna del productor en user_prefs y la mapea al mercado
    ODEPA más cercano via COMUNA_TO_MERCADO. Retorna None si:
    - phone_hash vacío o None
    - El productor no tiene comuna registrada
    - La comuna no está en el mapeo
    """
    if not phone_hash or not phone_hash.strip():
        return None

    try:
        user = session.scalar(
            select(UserPrefs).where(UserPrefs.phone_hash == phone_hash.strip())
        )
    except SQLAlchemyError:
        logger.warning("Error consultando preferencias para mercado cercano")
        return None

    if not user or not user.comuna or not user.comuna.strip():
        return None

    comuna_lower = user.comuna.strip().lower()
    mercado = COMUNA_TO_MERCADO.get(comuna_lower)
    if mercado:
        logger.info("Mercado cercano resuelto — estado=ok")
    else:
        logger.info("Mercado cercano no resuelto — estado=default")
    return mercado


def _select_registro_referencia(
    precios_por_mercado: dict[str, OdepaPrice],
) -> OdepaPrice:
    """Elige el registro de referencia cuando no se especifica mercado.

    Prioriza Lo Valledor (referencia nacional). ODEPA lo publica como
    "Mercado Mayorista Lo Valledor de Santiago", por eso el match es
    por substring case-insensitive, no por clave exacta. Entre varios
    matches (ej: seeds de demo con nombre corto) gana el dato más
    reciente. Sin match, gana el mercado con dato más reciente:
    el orden alfabético sesgaba a "Agrícola del Norte S.A. de Arica".
    """
    candidatos = [
        registro for mercado_nombre, registro in precios_por_mercado.items() if "lo valledor" in mercado_nombre.lower()
    ]
    if not candidatos:
        candidatos = list(precios_por_mercado.values())
    return max(candidatos, key=lambda registro: registro.fecha)


def _find_market_record(precios_por_mercado: dict[str, OdepaPrice], substring: str) -> OdepaPrice | None:
    """Busca registro de mercado por substring case-insensitive."""
    for nombre, registro in precios_por_mercado.items():
        if substring.lower() in nombre.lower():
            return registro
    return None


def get_price_for_llm(
    session: Session,
    producto: str,
    mercado: str = "",
    phone_hash: str | None = None,
) -> str:
    """Tool function para el LLM: consulta el precio más reciente.

    Si mercado está vacío, busca el mercado más cercano según la comuna
    registrada del productor (Issue #89). Si no hay comuna o el mercado
    cercano no tiene datos, usa Lo Valledor como referencia nacional.

    Cuando el mercado elegido NO es Lo Valledor, menciona ambos:
    "En Temuco está a X; la referencia nacional (Lo Valledor) es Y".

    Retorna texto natural en español chileno listo para TTS.
    Si no hay datos, retorna un mensaje informativo en vez de fallar.
    """
    if not mercado or not mercado.strip():
        # Sin mercado especificado: buscar en todos los mercados.
        try:
            precios_por_mercado = query_latest_by_product(session, producto)
        except ValueError:
            return "No entendí el producto. ¿Podrías repetirlo?"

        if not precios_por_mercado:
            return f"No tengo datos de precio para {producto.strip()}. ¿Podrias probar con otro producto?"

        # Issue #89: buscar mercado cercano según comuna registrada.
        mercado_cercano = _resolve_mercado_cercano(session, phone_hash or "")

        if mercado_cercano and mercado_cercano.lower() != "lo valledor":
            registro_local = _find_market_record(precios_por_mercado, mercado_cercano)

            if registro_local:
                # Buscar también el precio en Lo Valledor para comparar.
                registro_valledor = _find_market_record(precios_por_mercado, "lo valledor")

                if registro_valledor:
                    return (
                        f"En {registro_local.mercado}, "
                        f"{format_price_text(registro_local)} "
                        f"La referencia nacional (Lo Valledor) es "
                        f"{_formatear_pesos(registro_valledor.precio_kg)}."
                    )
                return format_price_text(registro_local)

        # Fallback: Lo Valledor (comportamiento original, Issue #83).
        return format_price_text(_select_registro_referencia(precios_por_mercado))

    # Mercado hablado ("vega central", "valledor"): exacto o substring.
    try:
        record = _obtener_registro_referencia(session, producto, mercado)
    except ValueError:
        return "No entendí el producto o mercado. ¿Podrías repetirlo?"

    if record is None:
        return f"No tengo datos de precio para {producto.strip()} en {mercado.strip()}."
    return format_price_text(record)


def get_price_spread_for_llm(session: Session, producto: str) -> str:
    """Tool function: muestra el rango de precios de un producto entre mercados.

    Consulta el precio más reciente en cada mercado y retorna el mínimo,
    máximo y promedio, para que el agricultor vea el spread.

    ODEPA no publica serie de \"precio a productor\" o \"precio en chacra\" —
    solo datos mayoristas. El spread entre mercados da una referencia del
    rango de negociación posible.

    Retorna texto natural en español chileno listo para TTS.
    """
    if not producto or not producto.strip():
        return "No entendí el producto. ¿Podrías repetirlo?"

    try:
        precios = query_latest_by_product(session, producto)
    except ValueError:
        return "No entendí el producto. ¿Podrías repetirlo?"

    if not precios:
        return f"No tengo datos de precio para {producto.strip()}."

    values = [r.precio_kg for r in precios.values()]
    if len(values) < 2:
        # Solo un mercado: sin spread que mostrar, retornar precio simple.
        only = next(iter(precios.values()))
        return format_price_text(only)

    minimo = min(values)
    maximo = max(values)
    promedio = Decimal(sum(values) / len(values)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    spread_pct = round(float((maximo - minimo) / promedio * 100), 1)

    mercados = list(precios.keys())
    producto_str = f"{producto.strip()[0].upper()}{producto.strip()[1:]}"

    return (
        f"{producto_str}: el precio va entre {_formatear_pesos(minimo)} "
        f"y {_formatear_pesos(maximo)} el kilo, según ODEPA. "
        f"El promedio en {len(mercados)} mercados es {_formatear_pesos(promedio)}, "
        f"con una diferencia del {spread_pct}% entre el más barato y el más caro."
    )


def _obtener_registro_referencia(session: Session, producto: str, mercado: str = "") -> OdepaPrice | None:
    """Obtiene el registro de referencia para una tool de precio.

    Encapsula el lookup compartido por las tools de precio: si mercado
    está vacío, busca en todos los mercados y elige el de referencia
    (Lo Valledor si existe, vía _select_registro_referencia); si no, busca
    el mercado específico.

    Con mercado nombrado, primero intenta match exacto y si falla usa
    substring (el productor dice "valledor" o "vega central"; ODEPA
    guarda nombres largos como "Mercado Mayorista Lo Valledor de Santiago").
    Sin ese fallback el fast-path no puede resolver mercados hablados.

    Lanza ValueError si producto está vacío (propagada de query_latest_*),
    para que el caller genere el mensaje de fallback adecuado.

    Retorna None si no hay datos para el producto/mercado.
    """
    if not mercado or not mercado.strip():
        precios_por_mercado = query_latest_by_product(session, producto)
        if not precios_por_mercado:
            return None
        return _select_registro_referencia(precios_por_mercado)

    exacto = query_latest_price(session, producto, mercado)
    if exacto is not None:
        return exacto

    precios_por_mercado = query_latest_by_product(session, producto)
    if not precios_por_mercado:
        return None
    return _find_market_record(precios_por_mercado, mercado)


def calculate_sale_value_for_llm(session: Session, producto: str, cantidad_kg: str, mercado: str = "") -> str:
    """Tool function para el LLM: calcula el valor total de venta.

    Responde "voy a vender 30 kilos de papa" con el monto total referencial
    basado en el precio mayorista ODEPA. El cálculo se hace en Python con
    Decimal (nunca por el LLM) para evitar alucinaciones aritméticas.

    Flujo:
    1. Parsea cantidad_kg a Decimal (0/negativo/no-numérico -> pide reformular).
    2. Obtiene el registro de referencia (igual que get_price_for_llm).
    3. Deriva precio por kilo:
       - Unidad kilo: directo (record.precio_kg).
       - Unidad convertible (saco/bandeja de N kilos): precio_kg / kilos.
       - Unidad NO convertible (docena de atados, caja por unidades):
         retorna el precio por unidad de venta + aviso honesto, sin
         inventar el cálculo por kilo (regresión #81).
    4. monto_total = precio_por_kilo * cantidad (Decimal, redondeado a entero).
    5. Texto natural listo para TTS.

    El precio por kilo se redondea a entero antes de multiplicar para que
    el monto cuadre con lo que se dice (353 * 30 = 10.590, no 353.33 * 30).

    El texto dice "referencia mayorista" / "según ODEPA": el precio de
    predio (lo que recibe el agricultor) es distinto y menor.
    """
    # 1. Parsear cantidad. El LLM/Whisper pueden pasar "30", "30,5", "abc".
    try:
        cantidad = Decimal(str(cantidad_kg).strip().replace(",", "."))
    except InvalidOperation:
        return "No entendí la cantidad. ¿Podrías repetir cuántos kilos vas a vender?"
    if cantidad <= 0:
        return "La cantidad tiene que ser mayor a cero. ¿Podrías repetir cuántos kilos vas a vender?"

    # 2. Obtener registro de referencia.
    try:
        record = _obtener_registro_referencia(session, producto, mercado)
    except ValueError:
        return "No entendí el producto. ¿Podrías repetirlo?"

    if record is None:
        if not mercado or not mercado.strip():
            return f"No tengo datos de precio para {producto.strip()}. ¿Podrias probar con otro producto?"
        return f"No tengo datos de precio para {producto.strip()} en {mercado.strip()}."

    producto_str = f"{record.producto[0].upper()}{record.producto[1:]}"

    # 3. Derivar precio por kilo según la unidad de venta ODEPA.
    if _es_unidad_kilo(record.unidad):
        precio_por_kilo = record.precio_kg
    else:
        kilos = _kilos_por_unidad(record.unidad)
        if kilos is None:
            # Unidad no convertible: precio por unidad de venta + aviso honesto.
            # Reusa format_price_text para consistencia con get_price_for_llm.
            return (
                f"{format_price_text(record)} "
                "No puedo calcular el total por kilo porque ODEPA publica "
                "el precio por unidad de venta, no por kilo. "
                "¿Podrías consultar el precio por kilo?"
            )
        precio_por_kilo = record.precio_kg / kilos

    # Redondear precio por kilo a entero (peso chileno no usa centavos en
    # referencia mayorista) antes de multiplicar, para que el monto cuadre.
    precio_por_kilo = precio_por_kilo.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    monto_total = (precio_por_kilo * cantidad).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    # 4. Formatear cantidad para TTS: entero "30", decimal "30 coma 5".
    if cantidad == cantidad.to_integral_value():
        cantidad_str = str(int(cantidad))
    else:
        entero, _, dec = str(cantidad).partition(".")
        dec = dec.rstrip("0") or "0"
        cantidad_str = f"{entero} coma {dec}"

    return (
        f"{producto_str} está a unos {_formatear_pesos(precio_por_kilo)} "
        f"el kilo según ODEPA. Por {cantidad_str} kilos recibirás unos "
        f"{_formatear_pesos(monto_total)} como referencia mayorista."
    )


def _formatear_variacion(actual: Decimal, antiguo: Decimal) -> str:
    """Describe la variación porcentual entre dos precios, hablada para TTS.

    "ha subido un 8 coma 7 por ciento" / "ha bajado un 11 coma 7 por ciento"
    / "se mantiene igual" (variación bajo 0,05%).
    """
    if antiguo == 0:
        return "no puedo calcular la variación"
    variacion = (actual - antiguo) / antiguo * 100
    if abs(variacion) < Decimal("0.05"):
        return "se mantiene igual"
    direccion = "subido" if variacion > 0 else "bajado"
    pct = abs(variacion).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    entero, _, dec = str(pct).partition(".")
    pct_str = entero if not dec or dec == "0" else f"{entero} coma {dec}"
    return f"ha {direccion} un {pct_str} por ciento"


def get_price_history_for_llm(session: Session, producto: str, dias: int = 7) -> str:
    """Tool function para el LLM: compara el precio actual con el histórico.

    Responde "¿a cuánto estaba la papa la semana pasada?" comparando el
    dato más reciente del mercado de referencia con el dato de hace `dias`
    días en el MISMO mercado y la MISMA unidad de venta — comparar sacos
    con mallas daría variaciones falsas.

    Retorna texto natural en español chileno listo para TTS. Si no hay
    punto histórico comparable, lo dice honesto con el precio actual.
    """
    # El LLM puede pasar dias como string o valores absurdos: normalizar.
    try:
        dias_norm = int(dias)
    except (TypeError, ValueError):
        dias_norm = 7
    dias_norm = min(max(dias_norm, 1), 90)

    try:
        precios_por_mercado = query_latest_by_product(session, producto)
    except ValueError:
        return "No entendí el producto. ¿Podrías repetirlo?"

    if not precios_por_mercado:
        return f"No tengo datos de precio para {producto.strip()}. ¿Podrias probar con otro producto?"

    actual = _select_registro_referencia(precios_por_mercado)
    fecha_limite = actual.fecha - datetime.timedelta(days=dias_norm)

    # Punto histórico: mismo mercado y misma unidad, lo más reciente
    # que tenga al menos `dias` días de distancia del dato actual.
    q = (
        select(OdepaPrice)
        .where(
            func.lower(OdepaPrice.producto) == actual.producto.lower(),
            OdepaPrice.mercado == actual.mercado,
            OdepaPrice.unidad == actual.unidad,
            OdepaPrice.fecha <= fecha_limite,
        )
        .order_by(OdepaPrice.fecha.desc())
        .limit(1)
    )
    antiguo = session.scalars(q).first()

    if antiguo is None:
        return f"{format_price_text(actual)} No tengo datos comparables de hace {dias_norm} días para ese mercado."

    dias_reales = (actual.fecha - antiguo.fecha).days
    return (
        f"{format_price_text(actual)} "
        f"Hace {dias_reales} días estaba a {_formatear_pesos(antiguo.precio_kg)}, "
        f"{_formatear_variacion(actual.precio_kg, antiguo.precio_kg)}."
    )


# Factores de conversion de unidades del agricultor a kilos para
# calculate_margin. El agricultor dice "vendi un saco", "vendi una malla",
# etc. Son factores FIJOS del dominio chileno, independientes de la
# unidad en que ODEPA publique sus precios.
_UNIDAD_AGRICULTOR_A_KILOS: dict[str, Decimal] = {
    "kilo": Decimal("1"),
    "kilos": Decimal("1"),
    "kg": Decimal("1"),
    "saco": Decimal("50"),
    "sacos": Decimal("50"),
    "malla": Decimal("25"),
    "mallas": Decimal("25"),
    "caja": Decimal("20"),
    "cajas": Decimal("20"),
    "tonelada": Decimal("1000"),
    "toneladas": Decimal("1000"),
}

_UNIDADES_VALIDAS = frozenset(_UNIDAD_AGRICULTOR_A_KILOS.keys())


def calculate_margin_for_llm(
    session: Session,
    producto: str,
    cantidad: str,
    unidad: str,
    precio_total: str,
    mercado: str = "",
    phone_hash: str = "",
) -> str:
    """Tool function para el LLM: calcula el margen de venta contra referencia ODEPA.

    Compara el precio TOTAL recibido por el agricultor contra el precio
    de referencia mayorista ODEPA para la misma cantidad. Cuando #170 está
    habilitado y consentido, agrega el total de gastos vigentes de ese producto
    y calcula el remanente de la venta. Esta función no persiste el monto de
    venta.

    Flujo:
    1. Valida que precio_total y cantidad sean numeros validos (>0).
    2. Obtiene precio de referencia ODEPA del producto (vía
       _obtener_registro_referencia, misma logica que get_price).
    3. Convierte la unidad del agricultor a kilos usando factores
       fijos del dominio chileno (saco=50kg, malla=25kg, etc).
    4. Calcula el precio de referencia total:
       precio_por_kilo_odepa * cantidad_en_kilos.
    5. Calcula diferencia absoluta y porcentual.
    6. Si existe identidad consentida, suma gastos vigentes del producto.
    7. Retorna texto en español chileno con fuente ODEPA y, cuando aplica,
       el remanente después de gastos registrados.

    Args:
        session: Sesion de SQLAlchemy (inyectada por _execute_tool).
        producto: Nombre del producto en singular (ej: "papa").
        cantidad: Cantidad vendida como string (ej: "100", "3.5").
        unidad: Unidad de medida (kilo, saco, malla, caja, tonelada).
        precio_total: Monto TOTAL recibido en pesos chilenos (ej: "250000").
        mercado: Mercado opcional (ej: "Lo Valledor").
        phone_hash: Identidad seudonimizada para consultar gastos consentidos.

    Returns:
        Texto en espanol chileno con el analisis de margen, listo para TTS.
    """
    # 1. Validar y parsear precio_total.
    try:
        precio_total_dec = Decimal(
            str(precio_total).strip()
            .replace(",", ".")
            .replace("$", "")
            .replace(" ", "")
        )
    except InvalidOperation:
        return (
            "No entendi el monto total de la venta. "
            "¿Podrias repetir cuanto recibiste en total?"
        )
    if precio_total_dec <= 0:
        return (
            "El monto total tiene que ser mayor a cero. "
            "¿Podrias repetir cuanto recibiste?"
        )

    # 2. Validar y parsear cantidad.
    try:
        cantidad_dec = Decimal(str(cantidad).strip().replace(",", "."))
    except InvalidOperation:
        return (
            "No entendi la cantidad vendida. "
            "¿Podrias repetir cuantas unidades vendiste?"
        )
    if cantidad_dec <= 0:
        return (
            "La cantidad tiene que ser mayor a cero. "
            "¿Podrias repetir cuantas unidades vendiste?"
        )

    # 3. Validar y convertir unidad a kilos.
    unidad_norm = unidad.strip().lower()
    factor = _UNIDAD_AGRICULTOR_A_KILOS.get(unidad_norm)
    if factor is None:
        unidades_habladas = ", ".join(sorted(_UNIDADES_VALIDAS))
        return (
            f"No conozco la unidad '{unidad_norm}'. "
            f"Las unidades que manejo son: {unidades_habladas}. "
            "¿Podrias repetir la unidad?"
        )

    cantidad_kilos = cantidad_dec * factor

    # 4. Obtener precio de referencia ODEPA.
    try:
        record = _obtener_registro_referencia(session, producto, mercado)
    except ValueError:
        return "No entendi el producto. ¿Podrias repetirlo?"

    if record is None:
        if not mercado or not mercado.strip():
            return (
                f"No tengo datos de precio de referencia para "
                f"{producto.strip()}. ¿Podrias probar con otro producto?"
            )
        return (
            f"No tengo datos de precio para {producto.strip()} "
            f"en {mercado.strip()}."
        )

    producto_str = f"{record.producto[0].upper()}{record.producto[1:]}"
    fecha_str = record.fecha.strftime("%d/%m/%Y")

    # 5. Derivar precio por kilo segun unidad de venta ODEPA.
    if _es_unidad_kilo(record.unidad):
        precio_por_kilo = record.precio_kg
    else:
        kilos = _kilos_por_unidad(record.unidad)
        if kilos is None:
            return (
                f"{producto_str} esta a {_formatear_pesos(record.precio_kg)} "
                f"por {_unidad_hablada(record.unidad)} en {record.mercado}. "
                "No puedo calcular la comparacion porque ODEPA publica "
                "el precio por unidad de venta, no por kilo. "
                "¿Podrias consultar en otra unidad?"
            )
        precio_por_kilo = record.precio_kg / kilos

    # Redondear precio por kilo a entero antes de calcular.
    precio_por_kilo = precio_por_kilo.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    precio_referencia_total = (
        precio_por_kilo * cantidad_kilos
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    # 6. Calcular diferencia y porcentaje.
    if precio_referencia_total == 0:
        return "No tengo datos de precio de referencia para comparar."

    diferencia = precio_total_dec - precio_referencia_total
    porcentaje = (
        (precio_total_dec / precio_referencia_total) - Decimal("1")
    ) * Decimal("100")
    porcentaje = porcentaje.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    # 7. Formatear respuesta en espanol chileno.
    if diferencia > 0:
        direccion = "sobre"
        mensaje_diferencia = (
            f"Vendiste {_formatear_pesos(abs(diferencia))} mas que la referencia"
        )
    elif diferencia < 0:
        direccion = "bajo"
        mensaje_diferencia = (
            f"Vendiste {_formatear_pesos(abs(diferencia))} menos que la referencia"
        )
    else:
        direccion = "igual a"
        mensaje_diferencia = "Vendiste exactamente al precio de referencia"

    # Formatear porcentaje para TTS.
    if porcentaje == porcentaje.to_integral_value():
        pct_str = str(int(abs(porcentaje)))
    else:
        pct_entero, _, pct_dec = str(abs(porcentaje)).partition(".")
        pct_dec = pct_dec.rstrip("0") or "0"
        pct_str = f"{pct_entero} coma {pct_dec}"

    expense_note = ""
    if phone_hash:
        from app.services.expense_service import get_active_expense_total

        expenses_total = get_active_expense_total(
            session,
            phone_hash,
            producto,
        )
        if expenses_total > 0:
            net_after_expenses = precio_total_dec - Decimal(expenses_total)
            expense_note = (
                f" Además, tienes {_formatear_pesos(Decimal(expenses_total))} "
                f"en gastos vigentes registrados para {producto.strip()}. "
                f"Al descontarlos de esta venta quedan "
                f"{_formatear_pesos(net_after_expenses)} antes de otros costos."
            )

    return (
        f"{producto_str}: segun ODEPA, el precio de referencia mayorista "
        f"es de {_formatear_pesos(precio_por_kilo)} el kilo "
        f"(precio del {fecha_str}). "
        f"Para la cantidad que vendiste, la referencia son "
        f"{_formatear_pesos(precio_referencia_total)}. "
        f"{mensaje_diferencia}, un {pct_str} por ciento {direccion} "
        f"del precio de referencia.{expense_note}"
    )


def list_products(session: Session) -> list[str]:
    """Lista todos los productos disponibles en ODEPA, ordenados A-Z.

    Útil para que el LLM sepa qué productos puede consultar y para
    autocompletar en el dashboard admin.
    """
    q = select(func.lower(OdepaPrice.producto).label("producto")).distinct().order_by(func.lower(OdepaPrice.producto))
    return list(session.scalars(q).all())


def list_mercados(session: Session, producto: str | None = None) -> list[str]:
    """Lista mercados disponibles, opcionalmente filtrados por producto.

    Sin filtro: todos los mercados. Con producto: solo mercados donde
    ese producto tiene datos.
    """
    q = select(OdepaPrice.mercado).distinct().order_by(OdepaPrice.mercado)
    if producto:
        q = q.where(func.lower(OdepaPrice.producto) == producto.strip().lower())
    return list(session.scalars(q).all())
