"""Motor de reglas agronómicas citadas (C1+C2).

El LLM nunca improvisa un consejo agronómico: este servicio resuelve una
consulta contra un corpus de reglas ya verificadas, cada una con su fuente y
fecha. Si no hay una regla que calce con el síntoma descrito, el sistema dice
que no tiene el dato — no rellena con una respuesta genérica ni deja que el
LLM invente. Si el corpus vence, es inválido o no existe, falla cerrado con
el mismo criterio que ``indap_credit_service``.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

import yaml

from app.core.config import settings

_CORPUS_PATH = Path(__file__).resolve().parents[2] / "corpus" / "reglas_agronomicas.yaml"

_LIMITS_TEXT = "Esto es información pública de INIA, no un diagnóstico personalizado."
_GATE_OFF_TEXT = "El motor de reglas agronómicas todavía no está habilitado."
_SAFE_FALLBACK = (
    "No tengo una regla verificada para eso. Para no darte un dato "
    f"desactualizado, prefiero no improvisarlo. {_LIMITS_TEXT}"
)
_NO_MATCH_TEXT = f"No tengo una regla verificada sobre esa consulta para tu cultivo. {_LIMITS_TEXT}"


@dataclass(frozen=True, slots=True)
class _Regla:
    """Una regla agronómica validada del snapshot."""

    regla_id: str
    cultivo: str
    sintomas: tuple[str, ...]
    fuente: str
    fuente_url: str
    fecha: str
    diagnostico: str
    siguiente_paso: str | None


@dataclass(frozen=True, slots=True)
class _CatalogoReglas:
    """Snapshot vigente del corpus de reglas."""

    verified_on: date
    review_before: date
    reglas: tuple[_Regla, ...]


@dataclass(frozen=True, slots=True)
class AgronomicRuleCitation:
    """Regla citada que puede mostrarse fuera del flujo conversacional."""

    text: str
    source: str
    source_url: str
    verified_on: date


def _normalizar(text: str) -> str:
    """Normaliza tildes y espacios para una detección determinista."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.split())


def _as_mapping(value: object) -> dict[object, object]:
    """Valida y tipa un mapping proveniente de YAML."""
    if not isinstance(value, dict):
        raise ValueError("se esperaba un mapping")
    return cast(dict[object, object], value)


def _required_text(mapping: dict[object, object], key: str) -> str:
    """Obtiene un string obligatorio desde un mapping no confiable."""
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"campo inválido: {key}")
    return value.strip()


def _required_str_list(mapping: dict[object, object], key: str) -> tuple[str, ...]:
    """Obtiene una lista de strings no vacía y normalizada."""
    value = mapping.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"campo inválido: {key}")
    items = tuple(_normalizar(str(item)) for item in cast(list[object], value))
    if not all(items):
        raise ValueError(f"campo inválido: {key}")
    return items


def _parse_regla(raw_regla: object) -> _Regla:
    """Valida una regla individual del snapshot."""
    regla = _as_mapping(raw_regla)
    siguiente_paso_raw = regla.get("siguiente_paso")
    siguiente_paso = siguiente_paso_raw.strip() if isinstance(siguiente_paso_raw, str) else None
    return _Regla(
        regla_id=_required_text(regla, "id"),
        cultivo=_normalizar(_required_text(regla, "cultivo")),
        sintomas=_required_str_list(regla, "sintomas"),
        fuente=_required_text(regla, "fuente"),
        fuente_url=_required_text(regla, "fuente_url"),
        fecha=_required_text(regla, "fecha"),
        diagnostico=_required_text(regla, "diagnostico"),
        siguiente_paso=siguiente_paso,
    )


def _load_catalog(corpus_path: Path, today: date) -> _CatalogoReglas:
    """Carga el snapshot y rechaza fuentes vencidas, futuras o inválidas."""
    raw: object = yaml.safe_load(corpus_path.read_text(encoding="utf-8"))
    root = _as_mapping(raw)
    if root.get("version") != 1:
        raise ValueError("versión de corpus no soportada")

    verified_on = date.fromisoformat(_required_text(root, "verificado_el"))
    review_before = date.fromisoformat(_required_text(root, "revisar_antes_de"))
    if verified_on > today or review_before < verified_on or today > review_before:
        raise ValueError("snapshot de reglas fuera de vigencia")

    reglas_raw = root.get("reglas")
    if not isinstance(reglas_raw, list) or not reglas_raw:
        raise ValueError("corpus de reglas sin contenido")

    reglas = tuple(_parse_regla(raw_regla) for raw_regla in cast(list[object], reglas_raw))
    ids = {regla.regla_id for regla in reglas}
    if len(ids) != len(reglas):
        raise ValueError("id de regla duplicado")

    return _CatalogoReglas(verified_on=verified_on, review_before=review_before, reglas=reglas)


def _format_regla(regla: _Regla, verified_on: date) -> str:
    """Construye una respuesta factual con fuente y límites explícitos."""
    verified = verified_on.strftime("%d/%m/%Y")
    siguiente = f" {regla.siguiente_paso}" if regla.siguiente_paso else ""
    return (
        f"{regla.diagnostico} {_LIMITS_TEXT}{siguiente} Fuente verificada el "
        f"{verified}: {regla.fuente}, {regla.fuente_url}"
    )


def _find_rule(catalog: _CatalogoReglas, sintoma: str, cultivo: str) -> _Regla | None:
    """Encuentra una regla sin relajar el filtro de cultivo."""
    candidatas = catalog.reglas
    if cultivo:
        candidatas = tuple(regla for regla in candidatas if regla.cultivo == cultivo)

    for regla in candidatas:
        if any(sintoma_regla in sintoma for sintoma_regla in regla.sintomas):
            return regla
    return None


def get_agronomic_rule_citation(
    sintoma: str,
    cultivo: str = "",
    *,
    today: date | None = None,
    corpus_path: Path | None = None,
) -> AgronomicRuleCitation | None:
    """Devuelve una regla vigente y sus metadatos para una respuesta visual.

    La ausencia de gate, de match o de un snapshot vigente se representa como
    ``None`` para que el caller pueda fallar cerrado sin inventar una cita.
    """
    if not settings.agronomic_rules_enabled or not sintoma.strip():
        return None

    effective_today = today or date.today()
    effective_path = corpus_path or _CORPUS_PATH
    try:
        catalog = _load_catalog(effective_path, effective_today)
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return None

    regla = _find_rule(catalog, _normalizar(sintoma), _normalizar(cultivo) if cultivo else "")
    if regla is None:
        return None
    return AgronomicRuleCitation(
        text=_format_regla(regla, catalog.verified_on),
        source=regla.fuente,
        source_url=regla.fuente_url,
        verified_on=catalog.verified_on,
    )


def get_agronomic_rule_for_llm(
    sintoma: str = "",
    cultivo: str = "",
    *,
    today: date | None = None,
    corpus_path: Path | None = None,
) -> str:
    """Resuelve una consulta agronómica contra el corpus de reglas citadas.

    Args:
        sintoma: Descripción del problema o pregunta del agricultor.
        cultivo: Cultivo declarado por el agricultor, si lo dio o si tiene
            una parcela registrada. Vacío busca en cualquier cultivo.
        today: Fecha inyectable para validar vigencia.
        corpus_path: Snapshot inyectable para pruebas o despliegues controlados.

    Returns:
        Diagnóstico citado, mensaje de "sin regla" o derivación segura si el
        corpus está vencido o corrupto. Nunca inventa una recomendación.
    """
    if not settings.agronomic_rules_enabled:
        return _GATE_OFF_TEXT
    if not sintoma.strip():
        return "No entendí qué problema o pregunta tienes sobre tu cultivo. ¿Podrías repetirlo?"

    normalized_sintoma = _normalizar(sintoma)
    normalized_cultivo = _normalizar(cultivo) if cultivo else ""

    effective_today = today or date.today()
    effective_path = corpus_path or _CORPUS_PATH
    try:
        catalog = _load_catalog(effective_path, effective_today)
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return _SAFE_FALLBACK

    regla = _find_rule(catalog, normalized_sintoma, normalized_cultivo)
    return _format_regla(regla, catalog.verified_on) if regla else _NO_MATCH_TEXT
