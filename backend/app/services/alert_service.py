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
import hashlib
import json
import logging
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, cast

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.constants import (
    CONDICIONES_VALIDAS,
    HELADA_UMBRAL_C,
    LLUVIA_EXTREMA_UMBRAL_MM,
    UMBRALES_CLIMA_VALIDOS,
    CondicionPrecio,
    TipoAlerta,
    UmbralClima,
)
from app.core.database import SessionLocal
from app.core.formato import formatear_pesos
from app.core.phone_hash import validate_phone_hash
from app.core.rate_limiter import SlidingWindowRateLimiter
from app.models.alert import Alert
from app.models.odepa_price import OdepaPrice
from app.models.user_prefs import UserPrefs
from app.services.odepa_service import (
    PriceVariation,
    _es_unidad_kilo,
    _kilos_por_unidad,
    _obtener_registro_referencia,
    detectar_variaciones_precio,
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
_VARIATION_ROUTE_TYPE = "variacion_precio"
_VARIATION_EVENT_DIGEST_CHARS = 20


# (CONDICIONES_VALIDAS, UMBRALES_CLIMA_VALIDOS, HELADA_UMBRAL_C, LLUVIA_EXTREMA_UMBRAL_MM
# importados de app.core.constants — fuente unica de verdad.)


class AlertServiceError(ValueError):
    """Error de validacion o regla de negocio en alertas."""


def remember_price_variation_route(
    phone_hash: str,
    wa_chat_id: str,
    session: Session | None = None,
) -> bool:
    """Guarda la ruta mínima solo para un opt-in vigente de variaciones.

    El hash no es reversible. Por eso la ruta se aprende desde un mensaje
    entrante autenticado y se conserva en un registro interno de ``Alert``.
    """
    if not validate_phone_hash(phone_hash) or not _is_valid_wa_chat_id(wa_chat_id):
        return False

    owns_session = session is None
    db = session or SessionLocal()
    try:
        prefs = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
        route = _get_variation_route(db, phone_hash)
        if prefs is None or prefs.alert_consent is not True or not _cultivos_de_preferencias(prefs):
            if route is not None:
                db.delete(route)
                db.commit()
            return False

        if route is None:
            route = Alert(
                phone_hash=phone_hash,
                wa_chat_id=wa_chat_id,
                tipo=_VARIATION_ROUTE_TYPE,
                activa=False,
            )
            db.add(route)
        elif route.wa_chat_id != wa_chat_id:
            route.wa_chat_id = wa_chat_id
        db.commit()
        return True
    except SQLAlchemyError:
        db.rollback()
        logger.error("Ruta de alerta de variación no persistida")
        return False
    finally:
        if owns_session:
            db.close()


async def create_price_alert(
    session: Session,
    phone_hash: str,
    wa_chat_id: str | None,
    producto: str,
    condicion: CondicionPrecio,
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
    condicion = cast(CondicionPrecio, condicion.strip())
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
    logger.info("Alerta creada — tipo=precio estado=creada")
    return f"Listo, te avisare cuando {producto_norm} {condicion} {_formatear_pesos(umbral)} el kilo."


async def create_clima_alert(
    session: Session,
    phone_hash: str,
    wa_chat_id: str | None,
    umbral_clima: str,  # str intencional: borde no confiable, acepta alias "lluvia" antes de normalizar/validar
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
    if umbral_norm not in UMBRALES_CLIMA_VALIDOS:
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
    logger.info("Alerta creada — tipo=clima estado=creada")
    if umbral_norm == "helada":
        return "Listo, te avisare si se espera helada: minima bajo 2 grados en Traiguen."
    return "Listo, te avisare si se espera lluvia extrema: mas de 50 milimetros en 24 horas en Traiguen."


async def cancelar_alertas(
    session: Session,
    phone_hash: str,
    tipo: TipoAlerta | None = None,
) -> int:
    """Desactiva las alertas activas de un phone_hash.

    Las alertas de variación de precio (#248) no usan filas ``Alert.activa``
    como interruptor: `evaluar_variaciones_precio` decide a quién enviar
    consultando `UserPrefs.alert_consent` directamente, y la fila `Alert`
    de tipo `_VARIATION_ROUTE_TYPE` solo guarda el destino/idempotencia con
    `activa=False` fijo. Sin este revoke, "cancela mis alertas" confirmaba
    la cancelación mientras el productor seguía recibiendo avisos de
    variación de precio en cada sync — cancelar sin `tipo` también corta esa
    vía apagando `alert_consent`; con un `tipo` específico se deja intacto
    porque esa suscripción no distingue precio de clima.

    Args:
        session: Sesion de SQLAlchemy.
        phone_hash: Hash del numero.
        tipo: Filtro opcional ('precio' o 'clima').

    Returns:
        Cantidad de alertas desactivadas (incluye la revocación de
        `alert_consent` cuando corresponde).
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

    revoked_consent = False
    if tipo is None:
        prefs = session.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
        if prefs is not None and prefs.alert_consent:
            prefs.alert_consent = False
            revoked_consent = True

    session.commit()
    total = len(alertas) + (1 if revoked_consent else 0)
    logger.info(
        "Alertas actualizadas — estado=canceladas count=%d consentimiento_revocado=%s",
        total,
        revoked_consent,
    )
    return total


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
            logger.info("Alerta omitida — tipo=precio estado=unidad_no_convertible")
            continue

        umbral = alerta.umbral or Decimal(0)
        if _cumple_condicion(precio_por_kg, cast(CondicionPrecio, alerta.condicion or ">"), umbral):
            mensaje = _mensaje_alerta_precio(alerta, registro, precio_por_kg)
            if alerta.wa_chat_id:
                await enviar_alerta(alerta.wa_chat_id, mensaje, alerta.phone_hash)
                alerta.last_triggered_at = datetime.datetime.now()
                session.commit()
                enviados.append(alerta.phone_hash)
                logger.info("Alerta procesada — tipo=precio estado=disparada")
    return enviados


async def evaluar_variaciones_precio(
    session: Session,
    settings_obj: "Settings",
    fecha: datetime.date | None = None,
) -> list[str]:
    """Envia alertas por variaciones críticas a productores suscritos.

    ``cultivos`` define la suscripción y ``alert_consent`` habilita el envío.
    El chat de destino se recupera de una alerta existente del mismo productor,
    porque el hash anonimizado no permite derivar un número de WhatsApp. La
    función usa el mismo gate de consentimiento de ``enviar_alerta`` antes de
    sintetizar o llamar a Open-WA.

    Args:
        session: Sesión SQLAlchemy del job de sincronización.
        settings_obj: Configuración con el rate limit de Open-WA.
        fecha: Fecha del boletín a evaluar; ``None`` usa el dato más reciente.

    Returns:
        Lista sin duplicados de phone_hash notificados correctamente.
    """
    variaciones = detectar_variaciones_precio(session, fecha=fecha)
    if not variaciones:
        return []

    preferencias = list(
        session.scalars(
            select(UserPrefs)
            .where(
                UserPrefs.alert_consent.is_(True),
                UserPrefs.cultivos.is_not(None),
            )
            .order_by(UserPrefs.phone_hash)
        ).all()
    )
    if not preferencias:
        return []

    # La cuota es global para Open-WA, no por agricultor: el gateway comparte
    # una sesión y Meta puede penalizar una ráfaga aunque cambie el destino.
    limiter = SlidingWindowRateLimiter(lambda: settings_obj.alert_rate_limit_per_minute)
    enviados: list[str] = []
    cultivos_por_hash = {prefs.phone_hash: _cultivos_de_preferencias(prefs) for prefs in preferencias}

    for prefs in preferencias:
        relevantes = [
            variacion
            for variacion in variaciones
            if variacion.producto.casefold() in cultivos_por_hash[prefs.phone_hash]
        ]
        if not relevantes:
            continue

        route = _ensure_variation_route_from_existing_alert(session, prefs.phone_hash)
        if route is None or route.wa_chat_id is None:
            logger.info("Alerta de variación omitida — estado=chat_no_disponible")
            continue
        mensaje = _mensaje_variaciones_precio(relevantes)
        event_digest = _variation_event_digest(mensaje)
        if route.umbral_clima == event_digest:
            logger.info("Alerta de variación omitida — estado=evento_ya_entregado")
            continue
        if not await _esperar_rate_limit(limiter):
            logger.warning("Alerta de variación omitida — estado=rate_limit")
            continue

        entregada = await enviar_alerta(
            route.wa_chat_id,
            mensaje,
            prefs.phone_hash,
        )
        if entregada is not False:
            route.umbral_clima = event_digest
            route.last_triggered_at = datetime.datetime.now()
            session.commit()
            enviados.append(prefs.phone_hash)

    return enviados


def _cultivos_de_preferencias(prefs: UserPrefs) -> set[str]:
    """Parsea cultivos sin dejar que un JSON corrupto interrumpa el cron."""
    if not prefs.cultivos:
        return set()
    try:
        datos: object = json.loads(prefs.cultivos)
    except (json.JSONDecodeError, TypeError):
        return set()
    if not isinstance(datos, list):
        return set()
    return {cultivo.strip().casefold() for cultivo in datos if isinstance(cultivo, str) and cultivo.strip()}


def _is_valid_wa_chat_id(wa_chat_id: str) -> bool:
    """Acepta únicamente IDs directos de Open-WA, sin normalizar texto libre."""
    if len(wa_chat_id) > 50:
        return False
    suffix = "@lid" if wa_chat_id.endswith("@lid") else "@c.us"
    return wa_chat_id.endswith(suffix) and wa_chat_id.removesuffix(suffix).isdigit()


def _get_variation_route(session: Session, phone_hash: str) -> Alert | None:
    """Obtiene el cursor de destino/idempotencia de variaciones."""
    return session.scalar(
        select(Alert)
        .where(
            Alert.phone_hash == phone_hash,
            Alert.tipo == _VARIATION_ROUTE_TYPE,
        )
        .order_by(Alert.id.desc())
        .limit(1)
    )


def _ensure_variation_route_from_existing_alert(session: Session, phone_hash: str) -> Alert | None:
    """Migra perezosamente una ruta histórica sin duplicar el chat en memoria.

    El `wa_chat_id` histórico puede venir de una alerta creada antes de que
    `_is_valid_wa_chat_id` existiera; sin revalidar acá, un id de grupo
    (`@g.us`) o malformado se promovía a ruta de envío proactivo, convirtiendo
    un opt-in individual en un broadcast a un grupo que nunca consintió.
    """
    route = _get_variation_route(session, phone_hash)
    if route is not None:
        return route
    wa_chat_id = session.scalar(
        select(Alert.wa_chat_id)
        .where(
            Alert.phone_hash == phone_hash,
            Alert.wa_chat_id.is_not(None),
        )
        .order_by(Alert.id.desc())
        .limit(1)
    )
    if wa_chat_id is None or not _is_valid_wa_chat_id(wa_chat_id):
        return None
    route = Alert(
        phone_hash=phone_hash,
        wa_chat_id=wa_chat_id,
        tipo=_VARIATION_ROUTE_TYPE,
        activa=False,
    )
    session.add(route)
    session.commit()
    return route


def _variation_event_digest(mensaje: str) -> str:
    """Identifica exactamente el contenido entregable sin persistirlo."""
    return hashlib.sha256(mensaje.encode("utf-8")).hexdigest()[:_VARIATION_EVENT_DIGEST_CHARS]


async def _esperar_rate_limit(limiter: SlidingWindowRateLimiter) -> bool:
    """Espera una ventana del gateway y retorna si el envío queda permitido."""
    retry_after = limiter.check("openwa-proactive-alerts")
    if retry_after is None:
        return True
    await asyncio.sleep(retry_after)
    return limiter.check("openwa-proactive-alerts") is None


def _mensaje_variacion_precio(variacion: PriceVariation) -> str:
    """Construye una alerta factual con fuente y fecha de ODEPA."""
    direccion = "subió" if variacion.variacion_pct > 0 else "bajó"
    porcentaje = abs(variacion.variacion_pct).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return (
        f"Alerta AgroVoz: {format_price_text(_registro_de_variacion(variacion))} "
        f"El precio {direccion} un {porcentaje}% respecto del dato anterior de ODEPA."
    )


def _mensaje_variaciones_precio(variaciones: list[PriceVariation]) -> str:
    """Agrupa hasta tres eventos para una sola entrega idempotente y acotada."""
    ordenadas = sorted(variaciones, key=lambda value: abs(value.variacion_pct), reverse=True)
    visibles = ordenadas[:3]
    mensaje = " ".join(_mensaje_variacion_precio(item) for item in visibles)
    restantes = len(ordenadas) - len(visibles)
    if restantes:
        mensaje += f" Hay {restantes} variaciones adicionales en tus cultivos registrados."
    return mensaje


def _registro_de_variacion(variacion: PriceVariation) -> OdepaPrice:
    """Adapta una variación al formateador determinista de precios."""
    return OdepaPrice(
        producto=variacion.producto,
        mercado=variacion.mercado,
        precio_kg=variacion.precio_actual,
        unidad=variacion.unidad,
        fecha=variacion.fecha,
        fuente="ODEPA",
    )


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
        logger.warning(
            "Evaluacion de alertas fallida — tipo=clima estado=pronostico_no_disponible error_type=%s",
            type(exc).__name__,
        )
        return []

    enviados: list[str] = []
    for alerta in alertas:
        if _ya_disparada_hoy(alerta, hoy):
            continue
        dia = _dia_cumple_umbral_clima(cast(UmbralClima | None, alerta.umbral_clima), forecast)
        if dia is None:
            continue
        mensaje = _mensaje_alerta_clima(cast(UmbralClima | None, alerta.umbral_clima), dia)
        if alerta.wa_chat_id:
            await enviar_alerta(alerta.wa_chat_id, mensaje, alerta.phone_hash)
            alerta.last_triggered_at = datetime.datetime.now()
            session.commit()
            enviados.append(alerta.phone_hash)
            logger.info("Alerta procesada — tipo=clima estado=disparada")
    return enviados


def _tiene_consentimiento_de_alertas(phone_hash: str) -> bool:
    """Indica si el productor autorizó recibir alertas proactivas.

    Una alerta la inicia AgroVoz sin que el productor pregunte, así que necesita
    su propia base de licitud, distinta de la consulta que él mismo dispara. El
    opt-in se recoge en la sección 7.4 del Acuerdo de Uso (docs/piloto/06).

    Falla cerrado: si la consulta a la DB revienta, se asume que NO hay
    consentimiento. Es preferible no enviar una alerta a enviarla sin permiso.

    Args:
        phone_hash: HMAC del número del productor.

    Returns:
        True solo si hay una fila de preferencias con ``alert_consent`` activo.
    """
    try:
        with SessionLocal() as session:
            prefs = session.scalar(
                select(UserPrefs).where(UserPrefs.phone_hash == phone_hash)
            )
            return bool(prefs and prefs.alert_consent)
    except SQLAlchemyError as exc:
        logger.error(
            "Alerta no enviada — tipo=alerta estado=consentimiento_no_verificado error_type=%s",
            type(exc).__name__,
        )
        return False


async def enviar_alerta(wa_chat_id: str, mensaje: str, phone_hash: str) -> bool:
    """Genera TTS del mensaje y lo envia como audio por Open-WA con retry.

    Antes de enviar verifica el opt-in de alertas del productor: sin
    consentimiento registrado no sale nada (Ley 21.719, comunicación no
    solicitada). Ver docs/legal/aviso-responsabilidad.md.

    Reintenta 3 veces (1s, 2s, 4s backoff) solo en el envío.
    Si tras 3 intentos falla, loguea ERROR y continúa sin interrumpir.

    Args:
        wa_chat_id: Chat ID de WhatsApp destino.
        mensaje: Texto de la alerta.
        phone_hash: HMAC del número (``hash_phone``), el MISMO valor con que se
            guardó ``user_prefs.phone_hash``. Se usa para el chequeo de
            consentimiento. No se debe derivar otro identificador desde
            ``wa_chat_id`` porque no coincidiría con el HMAC almacenado.
    """
    if not await asyncio.to_thread(_tiene_consentimiento_de_alertas, phone_hash):
        logger.info("Alerta no enviada — tipo=alerta estado=sin_consentimiento")
        return False

    tts = TTSService()
    audio_path = ""
    try:
        # Síntesis TTS (sin reintentos).
        audio_path = await asyncio.to_thread(tts.synthesize, mensaje)

        # Envío con reintentos (backoff exponencial).
        openwa = OpenWAService()
        max_intentos = 3
        for intento in range(1, max_intentos + 1):
            try:
                await openwa.send_audio(wa_chat_id, audio_path)
                logger.info("Alerta procesada — tipo=alerta estado=enviada")
                return True
            except (ConnectionError, OSError, RuntimeError) as exc:
                if intento < max_intentos:
                    espera_s = 2 ** (intento - 1)  # 1s, 2s, 4s
                    logger.warning(
                        "Reintento de alerta — "
                        "tipo=alerta estado=retry intento=%d max_intentos=%d "
                        "espera_s=%d error_type=%s",
                        intento,
                        max_intentos,
                        espera_s,
                        type(exc).__name__,
                    )
                    await asyncio.sleep(espera_s)
                else:
                    # Tras 3 intentos, loguear ERROR pero no abortar.
                    logger.error(
                        "Alerta no enviada — tipo=alerta estado=error intentos=%d error_type=%s",
                        max_intentos,
                        type(exc).__name__,
                    )
    except Exception as exc:
        logger.error(
            "Alerta no procesada — tipo=alerta estado=error_inesperado error_type=%s",
            type(exc).__name__,
        )
        return False
    finally:
        if audio_path:
            Path(audio_path).unlink(missing_ok=True)

    return False


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
    condicion: CondicionPrecio,
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
    umbral_clima: UmbralClima | None,
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


def _mensaje_alerta_clima(umbral_clima: UmbralClima | None, dia: ForecastDay) -> str:
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

    Delega en ``app.core.formato.formatear_pesos``. Esta version truncaba con
    ``int()``, asi que un umbral de 14.999,9 se anunciaba como "14.999 pesos"
    en vez de "15.000": en una alerta de precio ese peso de diferencia es
    justo el que define si se cumplio la condicion.
    """
    return formatear_pesos(valor)
