"""Servicio de alertas proactivas de precio y clima por WhatsApp.

Issue #88: permite configurar alertas de voz y evaluarlas contra datos
ODEPA (precio) y el pronostico de OpenMeteo (clima). Cuando se cumple
una condicion, genera TTS y envia un audio por Open-WA.

Reglas de negocio:
- Maximo 5 alertas activas por phone_hash.
- Maximo 1 disparo por alerta al dia (last_triggered_at).
- Las alertas de clima INFORMAN el pronostico; no recomiendan practicas.
- El precio de ODEPA se convierte a pesos por kilo cuando la unidad de
  venta lo permite, para comparar con el umbral configurado por kg.
"""

import asyncio
import datetime
import logging
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.odepa_price import OdepaPrice
from app.services.odepa_service import (
    _es_unidad_kilo,
    _kilos_por_unidad,
    _obtener_registro_referencia,
    format_price_text,
)
from app.services.openwa_service import OpenWAService
from app.services.tts_service import TTSService
from app.services.weather_service import (
    ForecastDay,
    get_weather_forecast_daily,
)

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)

# Tope de alertas activas por productor (anti-spam y control de superficie).
MAX_ALERTAS_ACTIVAS = 5

# Condiciones de comparacion validas para alertas de precio.
CONDICIONES_VALIDAS = {">", "<", ">=", "<="}

# Umbrales climaticos fijos soportados.
CLIMA_UMBRALES = {"helada", "lluvia_extrema"}

# Helada: temperatura minima inferior a 2 C.
HELADA_UMBRAL_C = Decimal("2.0")
# Lluvia extrema: mas de 50 mm de precipitacion en 24 horas.
LLUVIA_EXTREMA_UMBRAL_MM = Decimal("50.0")


class AlertServiceError(ValueError):
    """Error de validacion o regla de negocio en alertas."""


async def create_price_alert(
    session: Session,
    phone_hash: str,
    wa_chat_id: str | None,
    producto: str,
    condicion: str,
    umbral: Decimal,
) -> str:
    """Crea una alerta de precio.

    Valida la condicion, el umbral positivo y el tope de alertas activas.
    Normaliza producto a minusculas.

    Args:
        session: Sesion de SQLAlchemy.
        phone_hash: Hash HMAC-SHA256 del numero de telefono.
        wa_chat_id: Chat ID de WhatsApp para enviar avisos, o None.
        producto: Nombre del producto (ej: "papa").
        condicion: ">", "<", ">=" o "<=".
        umbral: Precio por kg en pesos chilenos.

    Returns:
        Mensaje de confirmacion hablado.

    Raises:
        AlertServiceError: Si falla alguna validacion.
    """
    condicion = condicion.strip()
    if condicion not in CONDICIONES_VALIDAS:
        raise AlertServiceError(f"Condicion invalida: {condicion}")
    if umbral <= 0:
        raise AlertServiceError("El umbral debe ser mayor a cero")
    producto_norm = producto.strip().lower()
    if not producto_norm:
        raise AlertServiceError("El producto no puede estar vacio")

    _validar_tope_alertas(session, phone_hash)

    alerta = Alert(
        phone_hash=phone_hash,
        wa_chat_id=wa_chat_id,
        tipo="precio",
        producto=producto_norm,
        condicion=condicion,
        umbral=umbral,
    )
    session.add(alerta)
    session.commit()
    logger.info(
        "Alerta de precio creada — phone_hash=%s producto=%s condicion=%s umbral=%s",
        phone_hash[:8],
        producto_norm,
        condicion,
        umbral,
    )
    return f"Listo, te avisare cuando {producto_norm} {condicion} {_formatear_pesos(umbral)} el kilo."


async def create_clima_alert(
    session: Session,
    phone_hash: str,
    wa_chat_id: str | None,
    umbral_clima: str,
) -> str:
    """Crea una alerta climatica fija.

    Args:
        session: Sesion de SQLAlchemy.
        phone_hash: Hash HMAC-SHA256 del numero.
        wa_chat_id: Chat ID de WhatsApp, o None.
        umbral_clima: "helada", "lluvia" o "lluvia_extrema".

    Returns:
        Mensaje de confirmacion hablado.

    Raises:
        AlertServiceError: Si el umbral no es valido.
    """
    umbral_norm = umbral_clima.strip().lower()
    # "lluvia" es alias hablado para el umbral fijo "lluvia_extrema".
    if umbral_norm == "lluvia":
        umbral_norm = "lluvia_extrema"
    if umbral_norm not in CLIMA_UMBRALES:
        raise AlertServiceError(f"Umbral climatico invalido: {umbral_clima}")

    _validar_tope_alertas(session, phone_hash)

    alerta = Alert(
        phone_hash=phone_hash,
        wa_chat_id=wa_chat_id,
        tipo="clima",
        umbral_clima=umbral_norm,
    )
    session.add(alerta)
    session.commit()
    logger.info(
        "Alerta de clima creada — phone_hash=%s umbral=%s",
        phone_hash[:8],
        umbral_norm,
    )
    if umbral_norm == "helada":
        return "Listo, te avisare si se espera helada: minima bajo 2 grados en Traiguen."
    return "Listo, te avisare si se espera lluvia extrema: mas de 50 milimetros en 24 horas en Traiguen."


async def cancelar_alertas(
    session: Session,
    phone_hash: str,
    tipo: str | None = None,
) -> int:
    """Desactiva las alertas activas de un phone_hash.

    Args:
        session: Sesion de SQLAlchemy.
        phone_hash: Hash del numero.
        tipo: Filtro opcional "precio" o "clima".

    Returns:
        Cantidad de alertas desactivadas.
    """
    query = select(Alert).where(
        Alert.phone_hash == phone_hash,
        Alert.activa.is_(True),
    )
    if tipo:
        query = query.where(Alert.tipo == tipo.strip().lower())

    alertas = list(session.scalars(query).all())
    for alerta in alertas:
        alerta.activa = False
    session.commit()
    logger.info(
        "Alertas canceladas — phone_hash=%s tipo=%s count=%d",
        phone_hash[:8],
        tipo or "todas",
        len(alertas),
    )
    return len(alertas)


async def evaluar_alertas_precio(
    session: Session,
    settings_obj: "Settings",
) -> list[str]:
    """Evalua alertas de precio contra el ultimo dato ODEPA.

    Para cada alerta activa, obtiene el precio de referencia del producto,
    lo convierte a pesos por kilo cuando es posible, y si se cumple la
    condicion (y no se disparo hoy), envia un audio por WhatsApp.

    Args:
        session: Sesion de SQLAlchemy.
        settings_obj: Settings del proyecto (para futura configuracion).

    Returns:
        Lista de phone_hash a los que se envio alerta.
    """
    del settings_obj  # Reservado para futura configuracion de mercado/coordenadas.
    hoy = datetime.datetime.now().date()
    alertas = list(
        session.scalars(
            select(Alert).where(
                Alert.tipo == "precio",
                Alert.activa.is_(True),
            )
        ).all()
    )

    enviados: list[str] = []
    for alerta in alertas:
        if _ya_disparada_hoy(alerta, hoy):
            continue
        if not alerta.producto:
            continue
        try:
            registro = _obtener_registro_referencia(session, alerta.producto, "")
        except ValueError:
            continue
        if registro is None:
            continue

        precio_por_kg = _calcular_precio_por_kg(registro)
        if precio_por_kg is None:
            logger.info(
                "Alerta precio omitida — unidad no convertible phone_hash=%s producto=%s unidad=%s",
                alerta.phone_hash[:8],
                alerta.producto,
                registro.unidad,
            )
            continue

        umbral = alerta.umbral or Decimal(0)
        if _cumple_condicion(precio_por_kg, alerta.condicion or ">", umbral):
            mensaje = _mensaje_alerta_precio(alerta, registro, precio_por_kg)
            if alerta.wa_chat_id:
                await enviar_alerta(alerta.wa_chat_id, mensaje)
                alerta.last_triggered_at = datetime.datetime.now()
                session.commit()
                enviados.append(alerta.phone_hash)
                logger.info(
                    "Alerta de precio disparada — phone_hash=%s producto=%s",
                    alerta.phone_hash[:8],
                    alerta.producto,
                )
    return enviados


async def evaluar_alertas_clima(
    session: Session,
    settings_obj: "Settings",
) -> list[str]:
    """Evalua alertas climaticas contra el pronostico de OpenMeteo.

    MVP usa coordenadas fijas de Traiguen. Envia un audio si se pronostica
    helada (minima < 2 C) o lluvia extrema (> 50 mm/24h) en los proximos
    dias, siempre que no se haya disparado hoy.

    Args:
        session: Sesion de SQLAlchemy.
        settings_obj: Settings del proyecto.

    Returns:
        Lista de phone_hash a los que se envio alerta.
    """
    del settings_obj  # Reservado para coordenadas dinamicas post-MVP.
    hoy = datetime.datetime.now().date()
    alertas = list(
        session.scalars(
            select(Alert).where(
                Alert.tipo == "clima",
                Alert.activa.is_(True),
            )
        ).all()
    )
    if not alertas:
        return []

    # MVP: coordenadas fijas de Traiguen para todos los productores.
    from app.services.weather_service import DEFAULT_LAT, DEFAULT_LON

    try:
        forecast = await get_weather_forecast_daily(DEFAULT_LAT, DEFAULT_LON)
    except (ConnectionError, RuntimeError, OSError) as exc:
        logger.warning("No se pudo obtener pronostico para alertas de clima: %s", exc)
        return []

    enviados: list[str] = []
    for alerta in alertas:
        if _ya_disparada_hoy(alerta, hoy):
            continue
        dia = _dia_cumple_umbral_clima(alerta.umbral_clima, forecast)
        if dia is None:
            continue
        mensaje = _mensaje_alerta_clima(alerta.umbral_clima, dia)
        if alerta.wa_chat_id:
            await enviar_alerta(alerta.wa_chat_id, mensaje)
            alerta.last_triggered_at = datetime.datetime.now()
            session.commit()
            enviados.append(alerta.phone_hash)
            logger.info(
                "Alerta de clima disparada — phone_hash=%s umbral=%s",
                alerta.phone_hash[:8],
                alerta.umbral_clima,
            )
    return enviados


async def enviar_alerta(wa_chat_id: str, mensaje: str) -> None:
    """Genera TTS del mensaje y lo envia como audio por Open-WA.

    Args:
        wa_chat_id: Chat ID de WhatsApp destino.
        mensaje: Texto de la alerta.
    """
    tts = TTSService()
    audio_path = ""
    try:
        audio_path = await asyncio.to_thread(tts.synthesize, mensaje)
        openwa = OpenWAService()
        await openwa.send_audio(wa_chat_id, audio_path)
        logger.info(
            "Alerta enviada — chat_id_hash=%s mensaje=%.80s...",
            _hash_chat_id(wa_chat_id),
            mensaje,
        )
    finally:
        if audio_path:
            Path(audio_path).unlink(missing_ok=True)


def _validar_tope_alertas(session: Session, phone_hash: str) -> None:
    """Lanza AlertServiceError si el phone_hash alcanzo el tope de alertas activas."""
    count = session.scalar(
        select(func.count())
        .select_from(Alert)
        .where(
            Alert.phone_hash == phone_hash,
            Alert.activa.is_(True),
        )
    )
    if count and count >= MAX_ALERTAS_ACTIVAS:
        raise AlertServiceError(f"Ya tienes {MAX_ALERTAS_ACTIVAS} alertas activas. Cancela una antes de crear otra.")


def _ya_disparada_hoy(alerta: Alert, hoy: datetime.date) -> bool:
    """True si la alerta ya fue disparada hoy."""
    if alerta.last_triggered_at is None:
        return False
    return alerta.last_triggered_at.date() == hoy


def _calcular_precio_por_kg(registro: OdepaPrice) -> Decimal | None:
    """Deriva el precio por kg desde el registro ODEPA.

    Si la unidad ya es kg, retorna el precio directo. Si es un contenedor
    convertible (saco de N kilos, etc.), divide. Si no es convertible,
    retorna None para no comparar erroneamente con el umbral por kg.
    """
    if _es_unidad_kilo(registro.unidad):
        return registro.precio_kg
    kilos = _kilos_por_unidad(registro.unidad)
    if kilos is None:
        return None
    return (registro.precio_kg / kilos).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _cumple_condicion(
    valor: Decimal,
    condicion: str,
    umbral: Decimal,
) -> bool:
    """Evalua valor condicion umbral."""
    if condicion == ">":
        return valor > umbral
    if condicion == ">=":
        return valor >= umbral
    if condicion == "<":
        return valor < umbral
    if condicion == "<=":
        return valor <= umbral
    return False


def _mensaje_alerta_precio(
    alerta: Alert,
    registro: OdepaPrice,
    precio_por_kg: Decimal,
) -> str:
    """Arma el texto hablado de una alerta de precio disparada."""
    umbral_str = _formatear_pesos(alerta.umbral or Decimal(0))
    precio_str = _formatear_pesos(precio_por_kg)
    return (
        f"Alerta de precio: {format_price_text(registro)} "
        f"Eso equivale a {precio_str} el kilo, "
        f"que es {alerta.condicion} tu umbral de {umbral_str} el kilo."
    )


def _dia_cumple_umbral_clima(
    umbral_clima: str | None,
    forecast: list[ForecastDay],
) -> ForecastDay | None:
    """Retorna el primer dia del pronostico que cumple el umbral climatico."""
    if umbral_clima == "helada":
        for dia in forecast:
            if dia.temp_min_c is not None and dia.temp_min_c < HELADA_UMBRAL_C:
                return dia
    elif umbral_clima == "lluvia_extrema":
        for dia in forecast:
            if dia.precipitation_sum_mm is not None and dia.precipitation_sum_mm > LLUVIA_EXTREMA_UMBRAL_MM:
                return dia
    return None


def _mensaje_alerta_clima(umbral_clima: str | None, dia: ForecastDay) -> str:
    """Arma el texto hablado de una alerta climatica.

    Solo informa el pronostico; no recomienda practicas agronomicas.
    """
    fecha_str = dia.fecha.strftime("%d/%m")
    if umbral_clima == "helada":
        temp_str = f"{dia.temp_min_c:.1f}" if dia.temp_min_c is not None else "no disponible"
        return (
            f"Alerta de clima: se espera helada en Traiguen el {fecha_str}, "
            f"con minima de {temp_str} grados, segun OpenMeteo."
        )
    precip_str = f"{dia.precipitation_sum_mm:.0f}" if dia.precipitation_sum_mm is not None else "no disponible"
    return f"Alerta de clima: se esperan {precip_str} milimetros de lluvia el {fecha_str} en Traiguen, segun OpenMeteo."


def _formatear_pesos(valor: Decimal) -> str:
    """Formatea un Decimal como pesos chilenos hablados.

    Ejemplo: 10000 -> "10.000 pesos".
    """
    entero = int(valor)
    return f"{entero:,}".replace(",", ".") + " pesos"


def _hash_chat_id(chat_id: str) -> str:
    """Hash corto del chat_id para logs, sin exponer el numero completo."""
    import hashlib

    return hashlib.sha256(chat_id.encode()).hexdigest()[:8]
