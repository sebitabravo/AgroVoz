"""Derivación determinista y segura a financiamiento agrícola de INDAP.

Este servicio no calcula cuotas, no evalúa antecedentes personales y no
recomienda instrumentos. Solo expone información pública previamente
verificada y falla de forma cerrada cuando esa fuente vence o es inválida.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import yaml

logger = logging.getLogger(__name__)

_CORPUS_PATH = Path(__file__).resolve().parents[2] / "corpus" / "indap_creditos.yaml"
_INDAP_ROOT_URL = "https://www.indap.gob.cl/"
_ALLOWED_INDAP_HOSTS = frozenset({"indap.gob.cl", "www.indap.gob.cl"})
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_URL_TRAILING_PUNCTUATION = ".,;:!?)]}"
_MAX_SOURCE_LINKS = 4

_CREDIT_MARKERS = (
    "credito",
    "creditos",
    "financiamiento",
    "financiar",
    "prestamo",
    "prestamos",
    "pedir prestado",
    "capital de trabajo",
    "programa de desarrollo de inversiones",
    "pdi",
)
_OUT_OF_SCOPE_MARKERS = (
    "tarjeta de credito",
    "hipotecario",
    "credito hipotecario",
    "prestamo hipotecario",
    "credito automotriz",
    "credito de consumo",
    "avance en efectivo",
)
_ADVICE_MARKERS = (
    "califico",
    "soy elegible",
    "puedo postular",
    "me conviene",
    "me recomiendas",
    "me recomienda",
    "cuanto deberia pedir",
    "que monto",
    "cuanto pido",
    "que tasa",
)
_UNSAFE_SOURCE_MARKERS = (
    "te conviene",
    "deberias",
    "deberías",
    "te recomendamos",
    "calificas",
    "eres elegible",
    "envia tu rut",
    "envía tu rut",
    "tus ingresos",
    "tus deudas",
    "monto maximo",
    "monto máximo",
    "tasa de interes",
    "tasa de interés",
)
_UNSAFE_AMOUNT_RE = re.compile(
    r"(?:[$%]|\b(?:uf|utm)\b|\b\d[\d.,]*\s*pesos?\b|"
    r"\bpor\s+ciento\b|\binter[eé]s\b)",
    re.IGNORECASE,
)

_LIMITS_TEXT = (
    "INDAP debe revisar los requisitos de cada solicitud. "
    "AgroVoz no puede decir si calificas ni recomendar un programa o monto."
)
_SAFE_FALLBACK = (
    "Para no entregarte información financiera desactualizada, no puedo "
    "detallar programas en este momento. Consulta directamente a INDAP en "
    f"{_INDAP_ROOT_URL} o en tu Agencia de Área. {_LIMITS_TEXT}"
)
_OUT_OF_SCOPE_RESPONSE = (
    "AgroVoz no entrega información sobre tarjetas, hipotecarios ni créditos "
    "de consumo. Solo deriva a información pública de financiamiento agrícola "
    f"de INDAP. {_LIMITS_TEXT}"
)


@dataclass(frozen=True, slots=True)
class _CreditDocument:
    """Documento INDAP validado para construir una derivación."""

    document_id: str
    title: str
    source_url: str
    text: str


@dataclass(frozen=True, slots=True)
class _CreditCatalog:
    """Snapshot vigente del corpus crediticio."""

    verified_on: date
    review_before: date
    documents: dict[str, _CreditDocument]


def _normalizar(text: str) -> str:
    """Normaliza tildes y espacios para una detección determinista."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.split())


def _required_text(mapping: dict[object, object], key: str) -> str:
    """Obtiene un string obligatorio desde un mapping no confiable."""
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"campo inválido: {key}")
    return value.strip()


def _as_mapping(value: object) -> dict[object, object]:
    """Valida y tipa un mapping proveniente de YAML."""
    if not isinstance(value, dict):
        raise ValueError("se esperaba un mapping")
    return cast(dict[object, object], value)


def _validate_indap_url(url: str) -> None:
    """Acepta exclusivamente enlaces HTTPS del dominio oficial de INDAP."""
    if not is_official_indap_url(url):
        raise ValueError("fuente fuera del dominio oficial de INDAP")


def is_official_indap_url(url: str) -> bool:
    """Indica si una URL HTTPS pertenece exactamente al dominio de INDAP."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme.casefold() != "https":
        return False
    if parsed.hostname is None or parsed.hostname.casefold() not in _ALLOWED_INDAP_HOSTS:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    return port in (None, 443)


def extract_official_indap_links(text: str) -> tuple[str, ...]:
    """Extrae, valida y desduplica solo enlaces oficiales presentes en texto."""
    links: list[str] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(text):
        candidate = match.group(0).rstrip(_URL_TRAILING_PUNCTUATION)
        if not candidate or not is_official_indap_url(candidate):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        links.append(candidate)
        if len(links) == _MAX_SOURCE_LINKS:
            break
    return tuple(links)


def build_indap_sources_message(text: str) -> str | None:
    """Construye un complemento solo con links allowlisted, sin texto libre."""
    links = extract_official_indap_links(text)
    if not links:
        return None
    return "Fuentes oficiales de INDAP:\n" + "\n".join(f"• {link}" for link in links)


def _validate_safe_summary(text: str) -> None:
    """Rechaza cifras financieras, asesoría o solicitudes de datos personales."""
    normalized = _normalizar(text)
    if len(text) > 800:
        raise ValueError("resumen INDAP demasiado extenso")
    if _UNSAFE_AMOUNT_RE.search(text) or any(marker in normalized for marker in _UNSAFE_SOURCE_MARKERS):
        raise ValueError("resumen INDAP contiene contenido financiero no permitido")


def _parse_document(raw_document: object) -> _CreditDocument:
    """Valida un documento individual del snapshot."""
    document = _as_mapping(raw_document)
    chunks_raw = document.get("chunks")
    if not isinstance(chunks_raw, list) or len(chunks_raw) != 1:
        raise ValueError("cada documento debe tener un único resumen")
    chunk = _as_mapping(cast(list[object], chunks_raw)[0])
    source_url = _required_text(document, "fuente_url")
    _validate_indap_url(source_url)
    text = _required_text(chunk, "texto")
    _validate_safe_summary(text)
    return _CreditDocument(
        document_id=_required_text(document, "id"),
        title=_required_text(document, "titulo"),
        source_url=source_url,
        text=text,
    )


def _load_catalog(corpus_path: Path, today: date) -> _CreditCatalog:
    """Carga el snapshot y rechaza fuentes vencidas, futuras o inválidas."""
    raw: object = yaml.safe_load(corpus_path.read_text(encoding="utf-8"))
    root = _as_mapping(raw)
    if root.get("version") != 1:
        raise ValueError("versión de corpus no soportada")

    verified_on = date.fromisoformat(_required_text(root, "verificado_el"))
    review_before = date.fromisoformat(_required_text(root, "revisar_antes_de"))
    if verified_on > today or review_before < verified_on or today > review_before:
        raise ValueError("snapshot INDAP fuera de vigencia")

    documents_raw = root.get("documentos")
    if not isinstance(documents_raw, list) or not documents_raw:
        raise ValueError("corpus INDAP sin documentos")

    documents: dict[str, _CreditDocument] = {}
    for raw_document in cast(list[object], documents_raw):
        document = _parse_document(raw_document)
        if document.document_id in documents:
            raise ValueError("id de documento duplicado")
        documents[document.document_id] = document

    required_ids = {
        "credito_corto_plazo",
        "credito_largo_plazo",
        "programa_desarrollo_inversiones",
        "acreditacion_indap",
    }
    if not required_ids.issubset(documents):
        raise ValueError("corpus INDAP incompleto")

    return _CreditCatalog(
        verified_on=verified_on,
        review_before=review_before,
        documents=documents,
    )


def _format_source(document: _CreditDocument, verified_on: date) -> str:
    """Construye una respuesta factual con fuente y límites explícitos."""
    verified = verified_on.strftime("%d/%m/%Y")
    return (
        f"{document.text} {_LIMITS_TEXT} Fuente oficial verificada el "
        f"{verified}: {document.title}, {document.source_url}"
    )


def _select_document(query: str, catalog: _CreditCatalog) -> _CreditDocument | None:
    """Selecciona solo cuando el agricultor nombra el instrumento explícito."""
    if "pdi" in query or "programa de desarrollo de inversiones" in query:
        return catalog.documents["programa_desarrollo_inversiones"]
    if "corto plazo" in query:
        return catalog.documents["credito_corto_plazo"]
    if "largo plazo" in query:
        return catalog.documents["credito_largo_plazo"]
    return None


def _format_overview(catalog: _CreditCatalog) -> str:
    """Deriva a ambas categorías sin escoger una por el agricultor."""
    short_term = catalog.documents["credito_corto_plazo"]
    long_term = catalog.documents["credito_largo_plazo"]
    verified = catalog.verified_on.strftime("%d/%m/%Y")
    return (
        "INDAP publica información oficial sobre créditos agrícolas de corto "
        f"y largo plazo. {_LIMITS_TEXT} Fuentes verificadas el {verified}: "
        f"{short_term.source_url} y {long_term.source_url}"
    )


def get_indap_credit_referral(
    query_text: str,
    *,
    today: date | None = None,
    corpus_path: Path | None = None,
) -> str | None:
    """Responde una intención financiera sin LLM ni datos personales.

    Args:
        query_text: Consulta escrita o transcrita del agricultor.
        today: Fecha inyectable para validar vigencia.
        corpus_path: Snapshot inyectable para pruebas o despliegues controlados.

    Returns:
        Derivación informativa, respuesta de fuera de alcance o ``None`` cuando
        la consulta no trata sobre financiamiento.
    """
    normalized = _normalizar(query_text)
    if any(marker in normalized for marker in _OUT_OF_SCOPE_MARKERS):
        return _OUT_OF_SCOPE_RESPONSE
    if not any(marker in normalized for marker in _CREDIT_MARKERS):
        return None

    effective_today = today or date.today()
    effective_path = corpus_path or _CORPUS_PATH
    try:
        catalog = _load_catalog(effective_path, effective_today)
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        # No se registra la consulta ni la excepción: podría contener PII o
        # contenido de un archivo manipulado. El código estable basta para operar.
        logger.warning("Corpus INDAP inválido o vencido — derivación segura activada")
        return _SAFE_FALLBACK

    if any(marker in normalized for marker in _ADVICE_MARKERS):
        accreditation = catalog.documents["acreditacion_indap"]
        return (
            f"{_LIMITS_TEXT} La acreditación tampoco garantiza acceso a un "
            "instrumento. Consulta directamente a INDAP o a tu Agencia de Área. "
            f"Fuente oficial: {accreditation.source_url}"
        )

    selected = _select_document(normalized, catalog)
    if selected is not None:
        return _format_source(selected, catalog.verified_on)
    return _format_overview(catalog)
