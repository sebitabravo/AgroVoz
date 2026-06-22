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
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.odepa_price import OdepaPrice

logger = logging.getLogger(__name__)

# Timeout de descarga. ODEPA publica CSVs pequeños (<1MB), 30s sobra.
_TIMEOUT_SEGUNDOS = 30

# Substrings a buscar en cada header normalizado (strip + lower).
# Si el header contiene alguno, se mapea a ese campo canónico.
_COLUMNA_PRODUCTO = ("producto",)
_COLUMNA_MERCADO = ("mercado", "lugar", "plaza", "feria")
_COLUMNA_PRECIO = ("precio",)
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
    return texto


def _resolver_columna(headers: Sequence[str], claves: tuple[str, ...]) -> str | None:
    """Devuelve el primer header que contiene alguna de las claves.

    Búsqueda case-insensitive sobre header normalizado (strip + lower).
    Permite que ODEPA cambie 'precio' por 'precio_prom_may' sin romper el parser.
    """
    for header in headers:
        norm = header.strip().lower()
        if any(clave in norm for clave in claves):
            return header
    return None


def _parsear_precio(valor: str) -> Decimal:
    """Convierte string de precio a Decimal. Lanza ValueError si inválido."""
    if not valor:
        raise ValueError("precio vacío")
    # Limpia símbolos de moneda y espacios. Mantiene separadores decimal/miles.
    limpio = valor.strip().replace("$", "").replace(" ", "")
    try:
        return Decimal(limpio)
    except InvalidOperation as exc:
        raise ValueError(f"precio inválido: {valor!r}") from exc


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

    filtro = {p.lower() for p in productos_filter} if productos_filter else None
    registros: list[OdepaCsvRecord] = []

    # num_fila empieza en 2: la línea 1 es el header del CSV.
    for num_fila, fila in enumerate(reader, start=2):
        producto = (fila.get(col_producto) or "").strip().lower()
        if not producto:
            continue
        if filtro and producto not in filtro:
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
    """
    if not registros:
        return (0, 0)

    # Detecta tuplas existentes para diferenciar inserts de updates.
    # O(N) queries, pero N es chico para MVP (papa, ~decena de mercados).
    existentes: set[tuple[str, str, datetime.date]] = set()
    for registro in registros:
        clave = (registro.producto, registro.mercado, registro.fecha)
        if clave in existentes:
            continue
        q = select(OdepaPrice.id).where(
            OdepaPrice.producto == registro.producto,
            OdepaPrice.mercado == registro.mercado,
            OdepaPrice.fecha == registro.fecha,
        )
        if session.scalars(q).first() is not None:
            existentes.add(clave)

    valores = [
        {
            "producto": r.producto,
            "mercado": r.mercado,
            "precio_kg": r.precio_kg,
            "unidad": r.unidad,
            "fecha": r.fecha,
            "fuente": "ODEPA",
        }
        for r in registros
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
        1 for r in registros if (r.producto, r.mercado, r.fecha) in existentes
    )
    insertados = len(registros) - actualizados
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

    try:
        contenido = await download_csv(settings.odepa_csv_url)
        registros = parse_csv(contenido, settings.odepa_productos_list)

        if not registros:
            logger.warning(
                "Sync ODEPA: 0 registros tras filtro productos=%s",
                settings.odepa_productos_list,
            )
            return SyncResult()

        insertados, actualizados = upsert_prices(session, registros)
        logger.info(
            "Sync ODEPA OK: %d insertados, %d actualizados (productos=%s)",
            insertados,
            actualizados,
            settings.odepa_productos_list,
        )
        return SyncResult(insertados=insertados, actualizados=actualizados)
    finally:
        if cerrar:
            session.close()
