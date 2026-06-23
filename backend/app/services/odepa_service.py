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
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import func, select
from sqlalchemy import tuple_ as sa_tuple
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.odepa_price import OdepaPrice

logger = logging.getLogger(__name__)

# Timeout de descarga. ODEPA publica CSVs pequeños (<1MB), 30s sobra.
_TIMEOUT_SEGUNDOS = 30

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
        raise OdepaSyncError(
            f"ODEPA respondió HTTP {exc.response.status_code} para {url}"
        ) from exc
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
    if any(
        marcador in _prefix
        for marcador in ("<!doctype", "<html", "<head", "<body", "<meta", "<title")
    ):
        raise OdepaSyncError(
            f"ODEPA devolvió HTML en vez de CSV. ¿Cambió la URL? ({url})"
        )

    return texto


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
        raise OdepaSyncError(
            f"CSV ODEPA sin columnas requeridas: {faltantes}. Headers: {headers}"
        )

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
            logger.warning("Fila %d omitida: %s", num_fila, exc)
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


def upsert_prices(
    session: Session, registros: Sequence[OdepaCsvRecord]
) -> tuple[int, int]:
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
    """
    if not registros:
        return (0, 0)

    # Deduplica por (producto, mercado, fecha). Orden de dict preserva inserción;
    # al reasignar la clave, el valor final es la última aparición del batch.
    unicos: dict[tuple[str, str, datetime.date], OdepaCsvRecord] = {}
    for r in registros:
        unicos[(r.producto, r.mercado, r.fecha)] = r
    registros_unicos = list(unicos.values())

    # Detecta tuplas existentes en 1 sola query para diferenciar inserts de updates.
    # tuple_().in_() evita N SELECTs individuales al crecer el catálogo de productos.
    claves = [(r.producto, r.mercado, r.fecha) for r in registros_unicos]
    q = select(
        OdepaPrice.producto, OdepaPrice.mercado, OdepaPrice.fecha
    ).where(
        sa_tuple(
            OdepaPrice.producto, OdepaPrice.mercado, OdepaPrice.fecha
        ).in_(claves)
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
        for r in registros_unicos
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
    session.commit()

    actualizados = sum(
        1 for r in registros_unicos if (r.producto, r.mercado, r.fecha) in existentes
    )
    insertados = len(registros_unicos) - actualizados
    return (insertados, actualizados)


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
        contenido = await download_csv(settings.odepa_csv_url)
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
        _ok = True
        return SyncResult(insertados=insertados, actualizados=actualizados)
    finally:
        if cerrar:
            if not _ok:
                session.rollback()
            session.close()


# ── Funciones de consulta para Tool Calling (Issue #16) ──────────


def query_latest_price(
    session: Session, producto: str, mercado: str
) -> OdepaPrice | None:
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


def query_latest_by_product(
    session: Session, producto: str
) -> dict[str, OdepaPrice]:
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


def format_price_text(record: OdepaPrice) -> str:
    """Formatea un registro OdepaPrice como texto natural en español chileno.

    Formato: "Papa está a 1.200 pesos el kilo en Lo Valledor, precio del 20/06/2026."
    Sin artículo para evitar errores de género (el tomate, la papa).
    Usa punto como separador de miles (convención chilena).
    Muestra decimales solo si el precio tiene fracción significativa.
    Usa "pesos" en vez de "$" para que el LLM no hable de "dólares" al leer el
    resultado de la tool antes de pasarlo a TTS.
    """
    precio = record.precio_kg
    if precio == precio.to_integral_value():
        parte_entera = f"{int(precio):,}".replace(",", ".")
        precio_str = f"{parte_entera} pesos"
    else:
        entero, dec = str(precio).split(".")
        parte_entera = f"{int(entero):,}".replace(",", ".")
        dec = dec.ljust(2, "0")[:2]
        precio_str = f"{parte_entera} coma {dec} pesos"

    fecha_str = record.fecha.strftime("%d/%m/%Y")
    return (
        f"{record.producto[0].upper()}{record.producto[1:]} está a {precio_str} "
        f"el kilo en {record.mercado}, precio del {fecha_str}."
    )


def get_price_for_llm(session: Session, producto: str, mercado: str) -> str:
    """Tool function para el LLM: consulta el precio más reciente.

    Retorna texto natural en español chileno listo para TTS.
    Si no hay datos, retorna un mensaje informativo en vez de fallar.
    El LLM usará esta función vía Tool Calling (Issue #18).
    """
    try:
        record = query_latest_price(session, producto, mercado)
    except ValueError:
        return "No entendí el producto o mercado. ¿Podrías repetirlo?"

    if record is None:
        return (
            f"No tengo datos de precio para {producto.strip()} "
            f"en {mercado.strip()}."
        )
    return format_price_text(record)


def list_products(session: Session) -> list[str]:
    """Lista todos los productos disponibles en ODEPA, ordenados A-Z.

    Útil para que el LLM sepa qué productos puede consultar y para
    autocompletar en el dashboard admin.
    """
    q = (
        select(func.lower(OdepaPrice.producto).label("producto"))
        .distinct()
        .order_by(func.lower(OdepaPrice.producto))
    )
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
