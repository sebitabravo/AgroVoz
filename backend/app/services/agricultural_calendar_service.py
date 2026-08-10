"""Calendario agrícola determinista con fuentes públicas de INIA.

Este servicio no calcula fechas ni extrapola una ventana entre comunas. Solo
responde con las fichas que fueron transcritas y verificadas en el snapshot
local; si el cultivo o la comuna no están cubiertos, falla cerrado.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

import yaml

from app.core.config import settings
from app.services.source_validation import parse_source_date, validate_source_url

_CORPUS_PATH = Path(__file__).resolve().parents[2] / "corpus" / "calendario_agricola.yaml"

_LIMITS_TEXT = "Esto es información pública de INIA, no una recomendación personalizada."
_GATE_OFF_TEXT = "El motor de reglas agronómicas todavía no está habilitado."
_SAFE_FALLBACK = (
    "No puedo detallar un calendario porque la fuente local no está disponible "
    f"o vigente. {_LIMITS_TEXT}"
)
_NO_MATCH_TEXT = (
    "No tengo una ventana de siembra o cosecha publicada por INIA para ese "
    f"cultivo y comuna. {_LIMITS_TEXT}"
)
_MISSING_INPUT_TEXT = "Necesito el cultivo y la comuna para buscar un calendario publicado por INIA."

# La primera cobertura verificable del piloto corresponde a Traiguén, comuna
# ubicada en el secano interior. No se infieren otras comunas a partir de esta
# equivalencia: se agregan solo después de verificar su fila en una fuente INIA.
_COMUNA_ZONA: dict[str, str] = {"traiguen": "secano interior"}
_ZONAS_DIRECTAS = frozenset({"secano interior"})


@dataclass(frozen=True, slots=True)
class _Calendario:
    """Una ventana de calendario validada desde el snapshot de INIA."""

    regla_id: str
    cultivo: str
    alias: tuple[str, ...]
    zona: str
    siembra: str
    cosecha: str
    fuente: str
    fuente_url: str
    fecha: date


@dataclass(frozen=True, slots=True)
class _CatalogoCalendario:
    """Snapshot vigente del calendario agrícola."""

    verified_on: date
    review_before: date
    reglas: tuple[_Calendario, ...]


def _normalizar(text: str) -> str:
    """Normaliza tildes y espacios para resolver entradas de voz."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.split())


def _as_mapping(value: object) -> dict[object, object]:
    """Valida y tipa un mapping proveniente de YAML."""
    if not isinstance(value, dict):
        raise ValueError("se esperaba un mapping")
    return cast(dict[object, object], value)


def _required_text(mapping: dict[object, object], key: str) -> str:
    """Obtiene un texto obligatorio desde un mapping no confiable."""
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"campo inválido: {key}")
    return value.strip()


def _required_str_list(mapping: dict[object, object], key: str) -> tuple[str, ...]:
    """Obtiene una lista de textos no vacía y normalizada."""
    value = mapping.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"campo inválido: {key}")
    items = tuple(_normalizar(str(item)) for item in cast(list[object], value))
    if not all(items):
        raise ValueError(f"campo inválido: {key}")
    # Alias con y sin tilde (por ejemplo, maíz/maiz) representan la misma
    # entrada después de normalizar y no deben invalidar todo el snapshot.
    return tuple(dict.fromkeys(items))


def _parse_calendario(raw_calendario: object, *, today: date) -> _Calendario:
    """Valida una entrada individual del snapshot."""
    calendario = _as_mapping(raw_calendario)
    zona = _normalizar(_required_text(calendario, "zona"))
    if zona not in _ZONAS_DIRECTAS and zona != "todas":
        raise ValueError("zona de calendario no soportada")
    fuente_url = validate_source_url(_required_text(calendario, "fuente_url"))
    fecha = parse_source_date(_required_text(calendario, "fecha"), today=today)
    return _Calendario(
        regla_id=_required_text(calendario, "id"),
        cultivo=_required_text(calendario, "cultivo"),
        alias=_required_str_list(calendario, "alias"),
        zona=zona,
        siembra=_required_text(calendario, "siembra"),
        cosecha=_required_text(calendario, "cosecha"),
        fuente=_required_text(calendario, "fuente"),
        fuente_url=fuente_url,
        fecha=fecha,
    )


def _load_catalog(corpus_path: Path, today: date) -> _CatalogoCalendario:
    """Carga el snapshot y rechaza fuentes ausentes, inválidas o vencidas."""
    raw: object = yaml.safe_load(corpus_path.read_text(encoding="utf-8"))
    root = _as_mapping(raw)
    if root.get("version") != 1:
        raise ValueError("versión de corpus no soportada")

    verified_on = date.fromisoformat(_required_text(root, "verificado_el"))
    review_before = date.fromisoformat(_required_text(root, "revisar_antes_de"))
    if verified_on > today or review_before < verified_on or today > review_before:
        raise ValueError("snapshot de calendario fuera de vigencia")

    reglas_raw = root.get("reglas")
    if not isinstance(reglas_raw, list) or not reglas_raw:
        raise ValueError("corpus de calendario sin contenido")

    reglas = tuple(
        _parse_calendario(raw_regla, today=today) for raw_regla in cast(list[object], reglas_raw)
    )
    ids = {regla.regla_id for regla in reglas}
    if len(ids) != len(reglas):
        raise ValueError("id de calendario duplicado")
    if any(regla.fecha > today for regla in reglas):
        raise ValueError("fecha de fuente futura")

    return _CatalogoCalendario(verified_on=verified_on, review_before=review_before, reglas=reglas)


def _resolver_zona(comuna: str) -> str | None:
    """Resuelve únicamente comunas o zonas con cobertura explícita."""
    normalized = _normalizar(comuna)
    if normalized in _COMUNA_ZONA:
        return _COMUNA_ZONA[normalized]
    if normalized in _ZONAS_DIRECTAS:
        return normalized
    return None


def _format_calendario(calendario: _Calendario, zona: str, verified_on: date) -> str:
    """Construye una respuesta factual con la fuente y las fechas citadas."""
    source_date = calendario.fecha.strftime("%d/%m/%Y")
    verified_date = verified_on.strftime("%d/%m/%Y")
    return (
        f"Según {calendario.fuente}, el calendario publicado para "
        f"{calendario.cultivo} en {zona} indica siembra o plantación "
        f"{calendario.siembra} y cosecha {calendario.cosecha}. {_LIMITS_TEXT} "
        f"Fuente publicada el {source_date}: {calendario.fuente}, "
        f"{calendario.fuente_url}. Snapshot verificado el {verified_date}."
    )


def get_calendario_agricola(
    producto: str = "",
    comuna: str = "",
    *,
    today: date | None = None,
    corpus_path: Path | None = None,
) -> str:
    """Resuelve una ventana de siembra y cosecha contra reglas citadas.

    Args:
        producto: Cultivo consultado, incluyendo un alias declarado en el corpus.
        comuna: Comuna del agricultor o una zona explícitamente cubierta.
        today: Fecha inyectable para validar la vigencia del snapshot.
        corpus_path: Snapshot inyectable para pruebas o despliegues controlados.

    Returns:
        Ventana citada, mensaje de cobertura inexistente o fallback seguro si el
        snapshot está vencido, corrupto o no existe.
    """
    if not settings.agronomic_rules_enabled:
        return _GATE_OFF_TEXT
    if not producto.strip() or not comuna.strip():
        return _MISSING_INPUT_TEXT

    zona = _resolver_zona(comuna)
    if zona is None:
        return _NO_MATCH_TEXT

    effective_today = today or date.today()
    effective_path = corpus_path or _CORPUS_PATH
    try:
        catalog = _load_catalog(effective_path, effective_today)
    except (OSError, TypeError, UnicodeError, ValueError, yaml.YAMLError):
        return _SAFE_FALLBACK

    normalized_producto = _normalizar(producto)
    for calendario in catalog.reglas:
        if normalized_producto in calendario.alias and calendario.zona in {zona, "todas"}:
            return _format_calendario(calendario, zona, catalog.verified_on)

    return _NO_MATCH_TEXT
