"""Pipeline de comandos de alerta proactiva: parsing y ejecución.

Issue #88 — extrae del pipeline de voz la detección de comandos de alerta
(precio, clima, cancelar) y orquesta la creación o cancelación de alertas.

Reglas de negocio:
- Regex: avisame cuando <producto> <condicion> <precio>
- Regex: avisame si viene <helada|lluvia>
- Regex: cancelar alertas
"""

from __future__ import annotations

import logging
import re
from decimal import InvalidOperation

from app.core.database import SessionLocal

logger = logging.getLogger(__name__)

# Palabras de accion del agricultor a condicion de comparacion.
# Data-driven: el regex se genera DESDE este dict.
# Imposible desincronizar: solo acciones en el dict pueden matchear.
_CONDICION_POR_ACCION: dict[str, str] = {
    "pase de": ">",
    "suba a": ">",
    "suba de": ">",
    "baje a": "<",
    "baje de": "<",
    "esté sobre": ">",
    "este sobre": ">",
    "esté encima de": ">",
    "este encima de": ">",
    "esté bajo": "<",
    "este bajo": "<",
    "esté debajo de": "<",
    "este debajo de": "<",
}

# Regex para comandos de alerta de precio. Se genera desde _CONDICION_POR_ACCION
# para evitar desincronización.
_acciones_pattern = "|".join(re.escape(a) for a in _CONDICION_POR_ACCION)
_ALERTA_PRECIO_RE = re.compile(
    rf"avis[aá]me\s+(?:cuando|si)\s+(?:la|el|los|las)?\s*(\w+)\s+"
    rf"({_acciones_pattern})"
    rf"\s+(\d[\d.]*)\s*(?:pesos)?",
    re.IGNORECASE,
)

# Regex para comandos de alerta climatica.
_ALERTA_CLIMA_RE = re.compile(
    r"avis[aá]me\s+si\s+viene\s+(helada|lluvia)",
    re.IGNORECASE,
)

# Regex para cancelación de alertas.
_ALERTA_CANCELAR_RE = re.compile(
    r"cancelar\s+(?:mis\s+)?alertas?",
    re.IGNORECASE,
)


async def detect_and_handle_alert_command(
    transcribed_text: str,
    phone_hash: str,
    wa_chat_id: str | None,
) -> tuple[str | None, str | None]:
    """Detecta comandos de alerta proactiva y ejecuta la accion.

    Soporta:
    - "avisame cuando la papa pase de 10000 pesos" -> alerta precio > 10000.
    - "avisame si viene helada" -> alerta clima helada.
    - "avisame si viene lluvia" -> alerta clima lluvia extrema.
    - "cancelar alertas" -> desactiva alertas activas.

    Args:
        transcribed_text: Texto transcrito por Whisper.
        phone_hash: Hash anonimizado del numero.
        wa_chat_id: Chat ID real de WhatsApp para enviar avisos.

    Returns:
        Tupla (texto_respuesta, intent). Si no es comando de alerta,
        retorna (None, None).
    """
    if not transcribed_text or not transcribed_text.strip():
        return None, None

    q = transcribed_text.strip().lower()

    # Cancelar alertas.
    if _ALERTA_CANCELAR_RE.search(q):
        return await _handle_cancel(phone_hash)

    # Alerta climatica.
    clima_match = _ALERTA_CLIMA_RE.search(q)
    if clima_match:
        tipo_umbral = clima_match.group(1).lower()
        umbral_clima = "helada" if tipo_umbral == "helada" else "lluvia_extrema"
        return await _handle_clima(phone_hash, wa_chat_id, umbral_clima)

    # Alerta de precio.
    precio_match = _ALERTA_PRECIO_RE.search(q)
    if precio_match:
        return await _handle_precio(transcribed_text, precio_match, phone_hash, wa_chat_id)

    return None, None


async def _handle_cancel(phone_hash: str) -> tuple[str, str]:
    """Maneja el comando "cancelar alertas"."""
    from app.services.alert_service import cancelar_alertas

    session = SessionLocal()
    try:
        count = await cancelar_alertas(session, phone_hash)
        msg = "He cancelado tus alertas." if count else "No tienes alertas activas."
        return msg, "alerta"
    finally:
        session.close()


async def _handle_clima(
    phone_hash: str,
    wa_chat_id: str | None,
    umbral_clima: str,
) -> tuple[str, str]:
    """Maneja alerta climatica."""
    from app.services.alert_service import AlertServiceError, create_clima_alert

    session = SessionLocal()
    try:
        mensaje = await create_clima_alert(session, phone_hash, wa_chat_id, umbral_clima)
        return mensaje, "alerta"
    except AlertServiceError as exc:
        return str(exc), "alerta"
    finally:
        session.close()


async def _handle_precio(
    transcribed_text: str,
    precio_match: re.Match,  # type: ignore
    phone_hash: str,
    wa_chat_id: str | None,
) -> tuple[str | None, str | None]:
    """Maneja alerta de precio."""
    from decimal import Decimal

    from app.services.alert_service import AlertServiceError, create_price_alert
    from app.services.pipeline_service import AgroVozPipeline

    producto_raw = precio_match.group(1).lower()
    accion = precio_match.group(2).lower()
    umbral_str = precio_match.group(3)

    condicion = _CONDICION_POR_ACCION.get(accion)
    if not condicion:
        return None, None

    producto = AgroVozPipeline._extract_producto(transcribed_text)
    if not producto:
        producto = producto_raw

    try:
        umbral = Decimal(umbral_str)
    except InvalidOperation:
        return "No entendi el precio. Repite el numero.", "alerta"

    session = SessionLocal()
    try:
        mensaje = await create_price_alert(session, phone_hash, wa_chat_id, producto, condicion, umbral)
        return mensaje, "alerta"
    except AlertServiceError as exc:
        return str(exc), "alerta"
    finally:
        session.close()
