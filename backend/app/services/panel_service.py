"""Panel PWA del agricultor: link firmado, sin login ni contraseña (C3).

Complemento opcional a WhatsApp — nunca lo reemplaza ni lo exige. El
agricultor pide un resumen y recibe un link de un solo uso, firmado con HMAC
y de duración corta. No es una cuenta ni una sesión persistente: vencido el
link, hay que pedir uno nuevo por WhatsApp. Coherente con el hard constraint
"sin autenticación de usuarios" — no se crea ningún sistema de login.

El resumen solo expone lo que el productor ya consintió explícitamente
(parcelas, alertas): revocar un consentimiento lo saca del resumen de
inmediato, sin cambios en este servicio.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.phone_hash import validate_phone_hash
from app.models.alert import Alert
from app.models.odepa_price import OdepaPrice
from app.models.parcela import Parcela
from app.models.user_prefs import UserPrefs
from app.services.odepa_service import COMUNA_TO_MERCADO

logger = logging.getLogger(__name__)

_TOKEN_SEPARATOR = "."
_PANEL_PRICE_DAYS = 28
_PANEL_MERCADO_DEFAULT = "Mercado Mayorista Lo Valledor de Santiago"


class PanelLinkError(RuntimeError):
    """El link no pudo generarse o el secreto no está configurado de forma segura."""


def _utcnow_naive() -> datetime.datetime:
    """Entrega UTC sin tzinfo, mismo formato que usa parcela_service en SQLite."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _get_link_secret() -> str:
    """Obtiene la clave dedicada o rechaza una operación no firmable."""
    secret = settings.panel_link_secret.get_secret_value()
    if len(secret.strip()) < 32:
        raise PanelLinkError("panel_link_secret no está configurada de forma segura")
    return secret


def _sign(phone_hash: str, expires_at: int) -> str:
    """Firma phone_hash + expiración con la clave dedicada del panel."""
    secret = _get_link_secret()
    message = f"{phone_hash}{_TOKEN_SEPARATOR}{expires_at}"
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_panel_token(phone_hash: str, *, now: int | None = None) -> str:
    """Genera un token firmado y de duración corta para el panel.

    Args:
        phone_hash: Identidad HMAC-SHA256 del productor.
        now: Timestamp Unix inyectable para tests.

    Returns:
        Token ``{phone_hash}.{expires_at}.{firma}``.

    Raises:
        ValueError: Si phone_hash no tiene formato válido.
        PanelLinkError: Si la clave de firma no está configurada de forma segura.
    """
    if not validate_phone_hash(phone_hash):
        raise ValueError("phone_hash inválido")

    effective_now = now if now is not None else int(time.time())
    expires_at = effective_now + settings.panel_link_ttl_hours * 3600
    signature = _sign(phone_hash, expires_at)
    return f"{phone_hash}{_TOKEN_SEPARATOR}{expires_at}{_TOKEN_SEPARATOR}{signature}"


def verify_panel_token(token: str, *, now: int | None = None) -> str | None:
    """Verifica un token del panel y retorna el phone_hash si es válido.

    Returns:
        El phone_hash si el token es válido y no ha vencido, o ``None`` si es
        inválido, está corrupto o venció. Nunca lanza sobre entrada no confiable.
    """
    parts = token.split(_TOKEN_SEPARATOR)
    if len(parts) != 3:
        return None
    phone_hash, expires_at_raw, signature = parts
    if not validate_phone_hash(phone_hash):
        return None
    try:
        expires_at = int(expires_at_raw)
    except ValueError:
        return None

    try:
        expected_signature = _sign(phone_hash, expires_at)
    except PanelLinkError:
        return None
    if not hmac.compare_digest(expected_signature, signature):
        return None

    effective_now = now if now is not None else int(time.time())
    if effective_now > expires_at:
        return None
    return phone_hash


def get_panel_link_for_llm(phone_hash: str = "") -> str:
    """Genera el mensaje con el link del panel para enviar por WhatsApp.

    Returns:
        Mensaje con el link, o explicación de por qué no se generó.
    """
    if not settings.farmer_panel_enabled:
        return "El panel web todavía no está disponible."
    if not settings.panel_base_url:
        return "El panel web todavía no está disponible."
    if not validate_phone_hash(phone_hash):
        return "No pude generar tu link de forma segura."

    try:
        token = generate_panel_token(phone_hash)
    except PanelLinkError:
        logger.error("No se pudo firmar el link del panel — clave insegura")
        return "Tuve un problema generando tu link. ¿Probamos de nuevo?"

    link = f"{settings.panel_base_url.rstrip('/')}/{token}"
    horas = settings.panel_link_ttl_hours
    return f"Aquí está tu resumen: {link} El link vence en {horas} horas."


@dataclass(frozen=True, slots=True)
class PanelSummary:
    """Resumen del panel: solo lo que el productor ya consintió."""

    comuna: str | None
    cultivos: list[str]
    parcelas: list[dict[str, Any]]
    alertas: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class PanelPricePoint:
    """Punto crudo de precio ODEPA para una fecha determinada."""

    fecha: datetime.date
    precio: Decimal


@dataclass(frozen=True, slots=True)
class PanelPriceSeries:
    """Serie de un cultivo, mercado y unidad de venta constantes."""

    cultivo: str
    mercado: str
    unidad: str
    fuente: str
    precios: list[PanelPricePoint]


@dataclass(frozen=True, slots=True)
class PanelPriceHistory:
    """Historial acotado de precios para los cultivos del productor."""

    desde: datetime.date
    hasta: datetime.date
    cultivos: list[PanelPriceSeries]


def _panel_today() -> datetime.date:
    """Devuelve la fecha de referencia; se puede reemplazar en tests."""
    return datetime.date.today()


def _parse_cultivos(raw_cultivos: str | None) -> list[str]:
    """Deserializa cultivos guardados en SQLite sin romper el resumen."""
    if not raw_cultivos:
        return []
    try:
        parsed = json.loads(raw_cultivos)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _normalizar_cultivos(cultivos: list[str]) -> list[str]:
    """Normaliza y deduplica cultivos antes de consultar SQLite."""
    vistos: set[str] = set()
    normalizados: list[str] = []
    for cultivo in cultivos:
        cultivo_norm = cultivo.strip().lower()
        if cultivo_norm and cultivo_norm not in vistos:
            vistos.add(cultivo_norm)
            normalizados.append(cultivo_norm)
    return normalizados


def _mercado_para_comuna(comuna: str | None) -> str:
    """Resuelve el mercado ODEPA de referencia sin consultar servicios externos."""
    comuna_norm = (comuna or "").strip().lower()
    return COMUNA_TO_MERCADO.get(comuna_norm, _PANEL_MERCADO_DEFAULT)


def _query_panel_price_rows(
    session: Session,
    cultivos: list[str],
    mercado: str,
    desde: datetime.date,
    hasta: datetime.date,
) -> list[OdepaPrice]:
    """Obtiene una sola ventana de datos para evitar una query por cultivo."""
    if not cultivos:
        return []
    query = (
        select(OdepaPrice)
        .where(
            func.lower(OdepaPrice.producto).in_(cultivos),
            func.lower(OdepaPrice.mercado) == mercado.lower(),
            OdepaPrice.fecha.between(desde, hasta),
        )
        .order_by(OdepaPrice.producto, OdepaPrice.fecha.desc(), OdepaPrice.id.desc())
    )
    return list(session.scalars(query).all())


def _build_panel_price_series(
    cultivo: str,
    rows: list[OdepaPrice],
    mercado: str,
) -> PanelPriceSeries | None:
    """Construye una serie sin mezclar unidades de venta distintas."""
    crop_rows = [row for row in rows if row.producto.strip().lower() == cultivo]
    if not crop_rows:
        return None

    # Si ODEPA cambia la unidad dentro de la ventana, se conserva la unidad
    # más reciente para no dibujar una variación que mezcle sacos y kilos.
    latest = crop_rows[0]
    compatible_rows = [row for row in crop_rows if row.unidad == latest.unidad]
    precios = [PanelPricePoint(fecha=row.fecha, precio=row.precio_kg) for row in reversed(compatible_rows)]
    return PanelPriceSeries(
        cultivo=latest.producto,
        mercado=mercado,
        unidad=latest.unidad,
        fuente=latest.fuente,
        precios=precios,
    )


def get_panel_summary(session: Session, phone_hash: str) -> PanelSummary | None:
    """Arma el resumen del panel respetando cada consentimiento por separado.

    Returns:
        ``None`` si el phone_hash no tiene preferencias registradas.
    """
    if not settings.farmer_panel_enabled:
        return None

    prefs = session.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
    if prefs is None:
        return None

    cultivos = _parse_cultivos(prefs.cultivos)

    parcelas: list[dict[str, Any]] = []
    if settings.parcela_tracking_enabled and prefs.parcela_consent:
        # Solo parcelas vigentes: una fila vencida ya no debe mostrarse aunque la
        # purga programada todavía no haya pasado. Mismo criterio que
        # parcela_service.get_parcelas_for_llm.
        parcela_rows = session.scalars(
            select(Parcela).where(Parcela.phone_hash == phone_hash, Parcela.expires_at > _utcnow_naive())
        ).all()
        parcelas = [
            {
                "cultivo": parcela.cultivo,
                "superficie_ha": float(parcela.superficie_ha),
                "comuna": parcela.comuna,
            }
            for parcela in parcela_rows
        ]

    alertas: list[dict[str, Any]] = []
    if prefs.alert_consent:
        alert_rows = session.scalars(select(Alert).where(Alert.phone_hash == phone_hash, Alert.activa.is_(True))).all()
        alertas = [
            {
                "tipo": alert.tipo,
                "producto": alert.producto,
                "condicion": alert.condicion,
                "umbral": float(alert.umbral) if alert.umbral is not None else None,
            }
            for alert in alert_rows
        ]

    return PanelSummary(
        comuna=prefs.comuna,
        cultivos=cultivos,
        parcelas=parcelas,
        alertas=alertas,
    )


def get_panel_price_history(
    session: Session,
    phone_hash: str,
    *,
    reference_date: datetime.date | None = None,
) -> PanelPriceHistory | None:
    """Obtiene 28 días de precios ODEPA de los cultivos del productor.

    El mercado se resuelve desde la comuna registrada y las series conservan
    una sola unidad de venta para que el gráfico no compare magnitudes distintas.
    ``reference_date`` permite tests deterministas sin depender del reloj.
    """
    if not settings.farmer_panel_enabled:
        return None

    prefs = session.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
    if prefs is None:
        return None

    hasta = reference_date or _panel_today()
    desde = hasta - datetime.timedelta(days=_PANEL_PRICE_DAYS - 1)
    cultivos = _normalizar_cultivos(_parse_cultivos(prefs.cultivos))
    mercado = _mercado_para_comuna(prefs.comuna)
    rows = _query_panel_price_rows(session, cultivos, mercado, desde, hasta)
    rows_by_crop: dict[str, list[OdepaPrice]] = {}
    for row in rows:
        rows_by_crop.setdefault(row.producto.strip().lower(), []).append(row)

    series: list[PanelPriceSeries] = []
    for cultivo in cultivos:
        serie = _build_panel_price_series(cultivo, rows_by_crop.get(cultivo, []), mercado)
        if serie is not None:
            series.append(serie)
    return PanelPriceHistory(desde=desde, hasta=hasta, cultivos=series)
