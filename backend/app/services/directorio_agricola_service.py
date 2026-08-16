"""Consulta determinista del directorio agrícola cargado en SQLite."""

from __future__ import annotations

import calendar
import datetime
import unicodedata
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.directorio_agricola import DirectorioAgricola

_TIPOS = frozenset({"indap", "prodesal", "cooperativa"})
_TIPO_ALIASES = {
    "agencia": "indap",
    "agencias": "indap",
    "oficina": "indap",
    "oficinas": "indap",
    "cooperativas": "cooperativa",
    "coop": "cooperativa",
}
_MAX_RESULTADOS = 10
_MAX_SOURCE_AGE_DAYS = 180
_MAX_SNAPSHOT_AGE_DAYS = 90


def _normalizar(texto: str) -> str:
    """Normaliza tildes y espacios para comparar comunas."""
    decomposed = unicodedata.normalize("NFKD", texto.casefold())
    sin_tildes = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(sin_tildes.split())


def _normalizar_tipo(tipo: str | None) -> str | None:
    """Valida el filtro de tipo y acepta sinónimos hablados."""
    if tipo is None or not tipo.strip():
        return None
    normalizado = _normalizar(tipo)
    normalizado = _TIPO_ALIASES.get(normalizado, normalizado)
    if normalizado not in _TIPOS:
        return None
    return normalizado


def _campo_contacto(etiqueta: str, valor: str | None) -> str:
    """Formatea un contacto sin presentar datos que la fuente no publica."""
    if valor is None or not valor.strip():
        return f"{etiqueta}: no publicado en la fuente oficial."
    return f"{etiqueta}: {valor}."


def _parse_fecha_fuente(valor: str | None) -> datetime.date | None:
    """Convierte fechas ISO completas o con precisión de mes/año."""
    if valor is None or not valor.strip():
        return None

    texto = valor.strip()
    try:
        if len(texto) == 4:
            return datetime.date(int(texto), 12, 31)
        if len(texto) == 7:
            anio, mes = (int(parte) for parte in texto.split("-"))
            return datetime.date(anio, mes, calendar.monthrange(anio, mes)[1])
        return datetime.date.fromisoformat(texto)
    except ValueError:
        return None


def _estado_vigencia(
    sede: DirectorioAgricola,
    hoy: datetime.date,
) -> tuple[str, str]:
    """Evalúa la vigencia sin convertir una fecha ausente en una suposición."""
    if sede.verificado_el > hoy:
        return "no_confirmada", "el snapshot tiene una fecha de verificación futura"
    if (hoy - sede.verificado_el).days > _MAX_SNAPSHOT_AGE_DAYS:
        return "vencida", "el snapshot de datos supera 90 días sin revisión"

    fecha_fuente = _parse_fecha_fuente(sede.fecha_fuente)
    if fecha_fuente is None:
        return "no_confirmada", "la fuente oficial no publica una fecha de actualización válida"
    if fecha_fuente > hoy:
        return "no_confirmada", "la fuente declara una fecha futura no verificable"
    if (hoy - fecha_fuente).days > _MAX_SOURCE_AGE_DAYS:
        return "vencida", "la fecha declarada por la fuente supera 180 días"
    return "vigente", "la fuente y el snapshot están dentro de la ventana de revisión"


def _format_source(sede: DirectorioAgricola) -> str:
    """Construye la cita trazable del registro."""
    fecha = sede.fecha_fuente or "fecha no informada"
    verificada = sede.verificado_el.strftime("%d/%m/%Y")
    return (
        f"Fuente: {sede.fuente}, actualización declarada {fecha}, "
        f"verificada el {verificada}: {sede.fuente_url}"
    )


def _format_sede(sede: DirectorioAgricola, hoy: datetime.date) -> str:
    """Convierte una sede en texto corto para lectura y voz."""
    estado, detalle = _estado_vigencia(sede, hoy)
    lineas = [f"- {sede.nombre}"]
    if estado == "vigente":
        horario = sede.horario or "no publicado en la fuente oficial"
        lineas.extend(
            (
                _campo_contacto("  Dirección", sede.direccion),
                _campo_contacto("  Teléfono oficial", sede.telefono),
                f"  Horario: {horario}.",
            )
        )
    else:
        lineas.append(
            f"  Vigencia: {estado.replace('_', ' ')}; {detalle}. "
            "No entrego dirección ni teléfono como contacto vigente."
        )
    lineas.append(f"  {_format_source(sede)}")
    return "\n".join(lineas)


def _filter_by_comuna(sedes: Iterable[DirectorioAgricola], comuna: str) -> list[DirectorioAgricola]:
    """Filtra sedes usando comparación tolerante a tildes."""
    comuna_normalizada = _normalizar(comuna)
    return [sede for sede in sedes if _normalizar(sede.comuna) == comuna_normalizada]


def _no_resultado(comuna: str, tipo: str | None) -> str:
    """Explica un resultado vacío sin inventar una sede cercana."""
    filtro = f" de tipo {tipo}" if tipo else ""
    return (
        f"No encontré una sede agrícola{filtro} con datos oficiales para {comuna}. "
        "Puedo buscar INDAP, PRODESAL o cooperativas por otra comuna."
    )


def get_directorio_agricola(
    session: Session,
    comuna: str,
    tipo: str | None = None,
    *,
    hoy: datetime.date | None = None,
) -> str:
    """Devuelve contactos oficiales de servicios agrícolas por comuna.

    Args:
        session: Sesión SQLAlchemy de corta duración.
        comuna: Comuna que se desea consultar; se compara sin tildes.
        tipo: ``indap``, ``prodesal`` o ``cooperativa``. Es opcional.
        hoy: Fecha de evaluación opcional para pruebas deterministas.

    Returns:
        Texto breve con dirección, teléfono, horario y fuente de cada registro.
    """
    comuna_limpia = comuna.strip()
    if not comuna_limpia:
        return "Necesito el nombre de una comuna para buscar el directorio agrícola."

    tipo_normalizado = _normalizar_tipo(tipo)
    if tipo and tipo.strip() and tipo_normalizado is None:
        return "El tipo debe ser INDAP, PRODESAL o cooperativa."

    stmt = select(DirectorioAgricola).order_by(
        DirectorioAgricola.tipo,
        DirectorioAgricola.nombre,
    )
    if tipo_normalizado is not None:
        stmt = stmt.where(DirectorioAgricola.tipo == tipo_normalizado)
    sedes = _filter_by_comuna(session.scalars(stmt).all(), comuna_limpia)
    if not sedes:
        return _no_resultado(comuna_limpia, tipo_normalizado)

    mostradas = sedes[:_MAX_RESULTADOS]
    fecha_evaluacion = hoy or datetime.date.today()
    encabezado = f"Directorio agrícola de {mostradas[0].comuna}:"
    respuesta = [
        encabezado,
        *(_format_sede(sede, fecha_evaluacion) for sede in mostradas),
    ]
    if len(sedes) > _MAX_RESULTADOS:
        respuesta.append(f"Hay {len(sedes)} registros; muestra limitada a {_MAX_RESULTADOS}.")
    return "\n".join(respuesta)


__all__ = ["get_directorio_agricola"]
