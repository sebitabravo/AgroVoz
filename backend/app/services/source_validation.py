"""Validación determinista de procedencia para el corpus agronómico."""

from __future__ import annotations

from datetime import date
from urllib.parse import urlsplit

_INIA_DOMAIN = "inia.cl"


def validate_source_url(value: str) -> str:
    """Rechaza URLs no HTTPS, credenciales, puertos no estándar o no INIA."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("fuente_url contiene un puerto inválido") from exc
    hostname = parsed.hostname
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or port not in {None, 443}
    ):
        raise ValueError("fuente_url debe usar HTTPS, sin credenciales ni puerto no estándar")

    normalized_host = hostname.casefold().rstrip(".")
    if normalized_host != _INIA_DOMAIN and not normalized_host.endswith(f".{_INIA_DOMAIN}"):
        raise ValueError("fuente_url fuera de la allowlist INIA")
    return value


def parse_source_date(value: str, *, today: date) -> date:
    """Parsea una fecha ISO y rechaza fuentes fechadas en el futuro."""
    try:
        source_date = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("fecha de fuente inválida; se espera YYYY-MM-DD") from exc
    if source_date > today:
        raise ValueError("fecha de fuente futura")
    return source_date
