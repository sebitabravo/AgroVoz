"""Servicio de métricas sobre consultas para el dashboard admin.

Todas las consultas se hacen sobre el modelo Consultation. Los datos son
anonimizados (phone_hash, no hay PII directamente identificable).

Definiciones de negocio (acordadas con el diseño del mockup):
- "Éxito" = respuesta principal aceptada por Open-WA (`delivered`).
  Los fallos confirmados usan `failed`; registros históricos sin evidencia
  permanecen `pending` y no entran al denominador.
- "Producto consultado" = valor estructurado de Consultation.producto,
  normalizado contra el catálogo ODEPA. Las métricas no inspeccionan
  query_text ni response_text.
- Períodos en días calendario locales. VPS Hetzner por defecto UTC,
  consistente con func.now() de SQLite.

No conoce HTTP: recibe Session y retorna dataclasses puras. El router
api/admin/metrics.py las serializa a JSON.
"""

import datetime
import logging
from dataclasses import dataclass, field

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.models.consultation import Consultation
from app.models.odepa_price import OdepaPrice
from app.models.user_prefs import UserPrefs
from app.services.odepa_service import list_products

logger = logging.getLogger(__name__)

# Días por defecto para las ventanas de métricas.
_DEFAULT_WINDOW_DAYS = 30


@dataclass(frozen=True)
class DailyCount:
    """Conteo de consultas para una fecha."""

    date: str  # ISO YYYY-MM-DD
    count: int


@dataclass(frozen=True)
class LatencyStats:
    """Estadísticos de latencia end-to-end en milisegundos."""

    avg: float
    p50: float
    p95: float
    p99: float
    count: int


@dataclass(frozen=True)
class StageStats:
    """Desglose de latencia promedio por etapa del pipeline (ms).

    Permite identificar el cuello de botella (Whisper/LLM/TTS). El campo
    total es la suma de los tres para validar contra latency_ms end-to-end.
    count es la muestra usada (consultas con timing persistido).
    """

    whisper_ms: float
    llm_ms: float
    tts_ms: float
    total_ms: float
    count: int


@dataclass(frozen=True)
class IntentDistribution:
    """Distribución de intents en una ventana."""

    precio: int
    clima: int
    credito: int
    desconocido: int

    @property
    def total(self) -> int:
        """Total de los cuatro intents operativos representados."""
        return self.precio + self.clima + self.credito + self.desconocido


@dataclass(frozen=True)
class ProductStat:
    """Producto ODEPA con conteo de consultas que lo mencionan."""

    name: str
    queries: int
    pct: float  # porcentaje sobre el producto más consultado (para barra)
    records: int = 0  # número de registros ODEPA para este producto
    updated: str = ""  # última fecha de actualización (ISO date)


@dataclass(frozen=True)
class OdepaStatus:
    """Estado de la data ODEPA: totales y cardinalidades.

    Compartido por el router JSON (/admin/odepa/status) y el dashboard HTML
    para evitar duplicar las queries. ultima_fecha es proxy de la última
    sync exitosa (ODEPA publica datos del día anterior).
    """

    total_filas: int
    ultima_fecha: datetime.date | None
    productos: int
    mercados: int


@dataclass(frozen=True)
class ErrorEntry:
    """Una consulta con intent desconocido para la tabla de errores."""

    ts: str  # ISO timestamp
    query: str
    response: str
    latency_ms: int


@dataclass(frozen=True)
class ErrorStats:
    """Tasa de error + últimos errores."""

    rate: float  # 0..1
    total_errors: int
    total_queries: int
    recent: list[ErrorEntry] = field(default_factory=list)


@dataclass(frozen=True)
class RecentQuery:
    """Una consulta reciente para la tabla del dashboard."""

    text: str
    intent: str
    latency_s: float
    ok: bool
    status_text: str
    ago: str
    ts: str


@dataclass(frozen=True)
class DashboardKpis:
    """KPIs principales de la vista Dashboard."""

    today: int
    yesterday: int
    today_trend_pct: float | None  # variación porcentual hoy vs ayer
    latency_avg: float  # segundos
    p95: float  # segundos
    p99: float  # segundos
    success_rate: float  # 0..1
    error_count_24h: int
    active_farmers_7d: int
    farmers_today: int
    last14: int
    last30: int
    trend_pct: float | None  # variación porcentual 14d vs 14d previos
    sparkline: list[int]  # conteos diarios de los últimos 14 días
    intents: IntentDistribution


@dataclass(frozen=True, slots=True)
class ProdesalGroupMetrics:
    """Métricas agregadas de un grupo, sin identificar a sus integrantes."""

    group_label: str
    comuna: str | None
    localidad: str | None
    total_consultations: int
    delivered: int
    failed: int
    pending: int
    delivery_rate: float
    avg_latency_ms: float
    last_activity: str | None


# ── Helpers ─────────────────────────────────────────────────────────


def _percentile(sorted_values: list[int], p: float) -> float:
    """Percentil p (0..1) sobre una lista ya ordenada ascendentemente.

    Interpolación lineal (método default de numpy). Retorna 0 si la lista
    está vacía (ventana sin datos).
    """
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])
    k = (n - 1) * p
    lower = int(k)
    upper = min(lower + 1, n - 1)
    if lower == upper:
        return float(sorted_values[lower])
    weight = k - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * weight


def _time_ago(dt: datetime.datetime, now: datetime.datetime | None = None) -> str:
    """Texto relativo en español ('hace 2h', 'hace 3d')."""
    now = now or datetime.datetime.now()
    delta = now - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return "hace menos de 1m"
    if secs < 3600:
        return f"hace {secs // 60}m"
    if secs < 86400:
        return f"hace {secs // 3600}h"
    return f"hace {secs // 86400}d"


def _build_sparkline(values: list[int], width: int = 320, height: int = 52) -> tuple[str, str]:
    """Genera los paths SVG (line, fill) para un sparkline de N puntos.

    Escala los valores al viewBox dado. Si todos los valores son iguales
    o hay un solo punto, dibuja una línea horizontal en el medio.
    """
    if not values:
        return "", ""
    n = len(values)
    vmax = max(values)
    vmin = min(values)
    span = vmax - vmin
    if span == 0 or n == 1:
        y = height / 2
        line = f"M0 {y:.1f} L{width} {y:.1f}"
        fill = f"{line} L{width} {height} L0 {height} Z"
        return line, fill
    points: list[tuple[float, float]] = []
    for i, v in enumerate(values):
        x = (i / (n - 1)) * width
        y = height - ((v - vmin) / span) * height
        points.append((x, y))
    line = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in points)
    fill = f"{line} L{width:.1f} {height} L0 {height} Z"
    return line, fill


def _days_ago(days: int) -> datetime.datetime:
    """Datetime de inicio (00:00) hace N días."""
    now = datetime.datetime.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(days=days)


# ── Consultas ───────────────────────────────────────────────────────


def get_daily_counts(db: Session, days: int = _DEFAULT_WINDOW_DAYS) -> list[DailyCount]:
    """Consultas agrupadas por día para los últimos N días.

    Genera entradas con count=0 para días sin consultas, así el gráfico
    no tiene huecos.
    """
    cutoff = _days_ago(days)
    stmt = (
        select(
            func.date(Consultation.created_at).label("dia"),
            func.count(Consultation.id).label("total"),
        )
        .where(Consultation.created_at >= cutoff, Consultation.is_test.is_(False))
        .group_by(func.date(Consultation.created_at))
        .order_by(func.date(Consultation.created_at))
    )
    rows = {str(dia): total for dia, total in db.execute(stmt).all()}

    resultado: list[DailyCount] = []
    hoy = datetime.date.today()
    for i in range(days - 1, -1, -1):
        fecha = hoy - datetime.timedelta(days=i)
        iso = fecha.isoformat()
        resultado.append(DailyCount(date=iso, count=int(rows.get(iso, 0))))
    return resultado


def get_latency_stats(db: Session, days: int = _DEFAULT_WINDOW_DAYS) -> LatencyStats:
    """Estadísticos de latencia (ms) en la ventana. Percentiles en Python."""
    cutoff = _days_ago(days)
    stmt = (
        select(Consultation.latency_ms)
        .where(Consultation.created_at >= cutoff, Consultation.is_test.is_(False))
        .order_by(Consultation.latency_ms)
    )
    valores = [int(v) for v in db.execute(stmt).scalars().all()]
    if not valores:
        return LatencyStats(avg=0.0, p50=0.0, p95=0.0, p99=0.0, count=0)
    return LatencyStats(
        avg=sum(valores) / len(valores),
        p50=_percentile(valores, 0.50),
        p95=_percentile(valores, 0.95),
        p99=_percentile(valores, 0.99),
        count=len(valores),
    )


def get_stage_stats(db: Session, days: int = _DEFAULT_WINDOW_DAYS) -> StageStats:
    """Latencia promedio por etapa del pipeline (Whisper, LLM, TTS) en ms.

    Una sola query agrega los tres campos. Solo cuenta consultas con timing
    persistido (whisper_ms > 0 OR llm_ms > 0 OR tts_ms > 0) para no diluir
    el promedio con registros pre-migración (todos en 0).
    """
    cutoff = _days_ago(days)
    stmt = (
        select(
            func.avg(Consultation.whisper_ms),
            func.avg(Consultation.llm_ms),
            func.avg(Consultation.tts_ms),
            func.count(Consultation.id),
        )
        .where(Consultation.created_at >= cutoff, Consultation.is_test.is_(False))
        .where((Consultation.whisper_ms > 0) | (Consultation.llm_ms > 0) | (Consultation.tts_ms > 0))
    )
    w_val, llm_val, t_val, n = db.execute(stmt).one()
    if not n:
        return StageStats(whisper_ms=0.0, llm_ms=0.0, tts_ms=0.0, total_ms=0.0, count=0)
    whisper = float(w_val or 0)
    llm = float(llm_val or 0)
    tts = float(t_val or 0)
    return StageStats(
        whisper_ms=round(whisper, 1),
        llm_ms=round(llm, 1),
        tts_ms=round(tts, 1),
        total_ms=round(whisper + llm + tts, 1),
        count=int(n),
    )


def get_intent_distribution(db: Session, days: int = _DEFAULT_WINDOW_DAYS) -> IntentDistribution:
    """Conteo de consultas por intent en la ventana."""
    cutoff = _days_ago(days)
    stmt = (
        select(Consultation.intent, func.count(Consultation.id))
        .where(Consultation.created_at >= cutoff, Consultation.is_test.is_(False))
        .group_by(Consultation.intent)
    )
    conteos: dict[str, int] = {intent: int(total) for intent, total in db.execute(stmt).all()}
    return IntentDistribution(
        precio=conteos.get("precio", 0),
        clima=conteos.get("clima", 0),
        credito=conteos.get("credito", 0),
        # Corpus, resumen, alerta y cualquier otro intent conservan su
        # identidad: no se presentan como consultas desconocidas.
        desconocido=conteos.get("desconocido", 0),
    )


def _normalizar_producto(producto: str) -> str:
    """Normaliza mayúsculas y espacios sin alterar el nombre visible."""
    return " ".join(producto.split()).casefold()


def _contar_productos_estructurados(
    db: Session,
    cutoff: datetime.datetime,
    productos: list[str],
) -> dict[str, int]:
    """Cuenta Consultation.producto sin acceder al contenido libre.

    Solo considera consultas de precio, no sintéticas y dentro de la ventana.
    Los valores nulos o compuestos únicamente por espacios se excluyen en SQL.
    Las variantes de mayúsculas y espacios se agrupan bajo el nombre canónico
    del catálogo ODEPA; valores ajenos al catálogo no se publican.
    """
    catalogo = {_normalizar_producto(producto): producto for producto in productos}
    stmt = (
        select(Consultation.producto, func.count(Consultation.id))
        .where(
            Consultation.created_at >= cutoff,
            Consultation.intent == "precio",
            Consultation.is_test.is_(False),
            Consultation.producto.is_not(None),
            func.trim(Consultation.producto) != "",
        )
        .group_by(Consultation.producto)
    )
    conteos: dict[str, int] = {}
    for producto, total in db.execute(stmt).all():
        nombre = catalogo.get(_normalizar_producto(producto))
        if nombre is not None:
            conteos[nombre] = conteos.get(nombre, 0) + int(total)
    return conteos


def get_top_products(db: Session, days: int = _DEFAULT_WINDOW_DAYS, limit: int = 10) -> list[ProductStat]:
    """Top productos registrados en consultas de precio.

    Usa exclusivamente Consultation.producto, normalizado contra ODEPA.
    El pct es relativo al producto más consultado (para escalar las barras).
    """
    cutoff = _days_ago(days)
    productos = list_products(db)

    conteos = _contar_productos_estructurados(db, cutoff, productos)

    # Obtener registros ODEPA y fecha última actualización por producto
    stmt_records = select(
        OdepaPrice.producto,
        func.count(OdepaPrice.id).label("cnt"),
        func.max(OdepaPrice.created_at).label("last"),
    ).group_by(OdepaPrice.producto)
    odepa_data: dict[str, tuple[int, str]] = {
        prod: (cnt, last.isoformat(timespec="seconds") if last else "")
        for (prod, cnt, last) in db.execute(stmt_records).all()
    }

    ordenados = sorted(conteos.items(), key=lambda x: x[1], reverse=True)[:limit]
    max_q = ordenados[0][1] if ordenados else 1
    return [
        ProductStat(
            name=nombre,
            queries=q,
            pct=round(q / max_q * 100, 1) if max_q else 0.0,
            records=odepa_data.get(nombre, (0, ""))[0],
            updated=odepa_data.get(nombre, (0, ""))[1],
        )
        for nombre, q in ordenados
    ]


def get_error_stats(db: Session, days: int = _DEFAULT_WINDOW_DAYS, limit: int = 20) -> ErrorStats:
    """Tasa de error (intent desconocido) + últimos errores."""
    cutoff = _days_ago(days)
    total = (
        db.scalar(
            select(func.count(Consultation.id)).where(
                Consultation.created_at >= cutoff, Consultation.is_test.is_(False)
            )
        )
        or 0
    )
    errores = (
        db.scalar(
            select(func.count(Consultation.id)).where(
                Consultation.created_at >= cutoff,
                Consultation.intent == "desconocido",
                Consultation.is_test.is_(False),
            )
        )
        or 0
    )
    rate = errores / total if total else 0.0

    stmt = (
        select(Consultation)
        .where(
            Consultation.created_at >= cutoff,
            Consultation.intent == "desconocido",
            Consultation.is_test.is_(False),
        )
        .order_by(Consultation.created_at.desc())
        .limit(limit)
    )
    recientes = [
        ErrorEntry(
            ts=c.created_at.isoformat(timespec="seconds"),
            query=c.query_text,
            response=c.response_text,
            latency_ms=c.latency_ms,
        )
        for c in db.scalars(stmt).all()
    ]
    return ErrorStats(
        rate=rate,
        total_errors=int(errores),
        total_queries=int(total),
        recent=recientes,
    )


def get_recent_queries(db: Session, hours: int = 24, limit: int = 20) -> list[RecentQuery]:
    """Últimas consultas para la tabla del dashboard."""
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours)
    stmt = (
        select(Consultation)
        .where(Consultation.created_at >= cutoff, Consultation.is_test.is_(False))
        .order_by(Consultation.created_at.desc())
        .limit(limit)
    )
    now = datetime.datetime.now()
    resultado: list[RecentQuery] = []
    for c in db.scalars(stmt).all():
        ok = c.delivery_status == "delivered"
        status_text = {
            "delivered": "ok",
            "failed": "error",
            "pending": "pendiente",
        }.get(c.delivery_status, "pendiente")
        resultado.append(
            RecentQuery(
                text=c.query_text,
                intent=c.intent,
                latency_s=round(c.latency_ms / 1000, 1),
                ok=ok,
                status_text=status_text,
                ago=_time_ago(c.created_at, now),
                ts=c.created_at.isoformat(timespec="seconds"),
            )
        )
    return resultado


def get_dashboard_kpis(db: Session) -> DashboardKpis:
    """KPIs principales para la vista Dashboard."""
    hoy_inicio = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    ayer_inicio = hoy_inicio - datetime.timedelta(days=1)

    today = (
        db.scalar(
            select(func.count(Consultation.id)).where(
                Consultation.created_at >= hoy_inicio, Consultation.is_test.is_(False)
            )
        )
        or 0
    )
    yesterday = (
        db.scalar(
            select(func.count(Consultation.id)).where(
                Consultation.created_at >= ayer_inicio,
                Consultation.created_at < hoy_inicio,
                Consultation.is_test.is_(False),
            )
        )
        or 0
    )

    if yesterday > 0:
        today_trend_pct = round((today - yesterday) / yesterday * 100, 1)
    elif today > 0:
        today_trend_pct = 100.0
    else:
        today_trend_pct = None

    lat_hoy = get_latency_stats(db, days=1)
    intents_24h = get_intent_distribution(db, days=1)
    cutoff_24h = datetime.datetime.now() - datetime.timedelta(days=1)
    delivery_counts = {
        delivery_status: int(count)
        for delivery_status, count in db.execute(
            select(Consultation.delivery_status, func.count(Consultation.id))
            .where(
                Consultation.created_at >= cutoff_24h,
                Consultation.is_test.is_(False),
                Consultation.delivery_status.in_(("delivered", "failed")),
            )
            .group_by(Consultation.delivery_status)
        ).all()
    }
    delivered = delivery_counts.get("delivered", 0)
    delivery_attempts = delivered + delivery_counts.get("failed", 0)
    # ``pending`` incluye filas históricas cuya entrega no puede probarse.
    # Excluirlas evita presentar ausencia de evidencia como éxito o fracaso.
    success_rate = delivered / delivery_attempts if delivery_attempts else 0.0

    active_7d = (
        db.scalar(
            select(func.count(func.distinct(Consultation.phone_hash))).where(
                Consultation.created_at >= hoy_inicio - datetime.timedelta(days=6),
                Consultation.is_test.is_(False),
            )
        )
        or 0
    )
    farmers_today = (
        db.scalar(
            select(func.count(func.distinct(Consultation.phone_hash))).where(
                Consultation.created_at >= hoy_inicio, Consultation.is_test.is_(False)
            )
        )
        or 0
    )

    diario_30 = get_daily_counts(db, days=30)
    last30 = sum(d.count for d in diario_30)
    sparkline_14 = [d.count for d in diario_30[-14:]]
    last14 = sum(sparkline_14)
    prev14 = sum(d.count for d in diario_30[-28:-14]) if len(diario_30) >= 28 else 0
    if prev14 > 0:
        trend_pct = round((last14 - prev14) / prev14 * 100, 1)
    elif last14 > 0:
        trend_pct = 100.0
    else:
        trend_pct = None

    return DashboardKpis(
        today=int(today),
        yesterday=int(yesterday),
        today_trend_pct=today_trend_pct,
        latency_avg=round(lat_hoy.avg / 1000, 1) if lat_hoy.count else 0.0,
        p95=round(lat_hoy.p95 / 1000, 1) if lat_hoy.count else 0.0,
        p99=round(lat_hoy.p99 / 1000, 1) if lat_hoy.count else 0.0,
        success_rate=round(success_rate, 3),
        error_count_24h=delivery_counts.get("failed", 0),
        active_farmers_7d=int(active_7d),
        farmers_today=int(farmers_today),
        last14=last14,
        last30=last30,
        trend_pct=trend_pct,
        sparkline=sparkline_14,
        intents=intents_24h,
    )


def get_prodesal_group_metrics(
    db: Session,
    days: int = _DEFAULT_WINDOW_DAYS,
) -> list[ProdesalGroupMetrics]:
    """Agrupa actividad reciente por identidad colectiva PRODESAL.

    La consulta parte desde ``UserPrefs`` y usa LEFT JOIN para conservar grupos
    existentes sin actividad. Los filtros temporales y ``is_test`` viven en el
    ``ON``: moverlos al ``WHERE`` convertiría el join en interno y ocultaría
    esos grupos. La tasa usa solo estados confirmados (delivered + failed).

    Args:
        db: Sesión SQLAlchemy activa.
        days: Ventana exacta hacia atrás en días.

    Returns:
        Grupos ordenados, sin hashes, textos ni detalle de integrantes.
    """
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    join_condition = and_(
        Consultation.phone_hash == UserPrefs.phone_hash,
        Consultation.created_at >= cutoff,
        Consultation.is_test.is_(False),
    )
    stmt = (
        select(
            UserPrefs.group_label,
            UserPrefs.comuna,
            UserPrefs.localidad,
            func.count(Consultation.id).label("total_consultations"),
            func.sum(
                case(
                    (Consultation.delivery_status == "delivered", 1),
                    else_=0,
                )
            ).label("delivered"),
            func.sum(
                case(
                    (Consultation.delivery_status == "failed", 1),
                    else_=0,
                )
            ).label("failed"),
            func.sum(
                case(
                    (Consultation.delivery_status == "pending", 1),
                    else_=0,
                )
            ).label("pending"),
            func.avg(Consultation.latency_ms).label("avg_latency_ms"),
            func.max(Consultation.created_at).label("last_activity"),
        )
        .select_from(UserPrefs)
        .outerjoin(Consultation, join_condition)
        .where(
            UserPrefs.identity_type == "prodesal_group",
            UserPrefs.group_label.is_not(None),
            func.length(func.trim(UserPrefs.group_label)) > 0,
        )
        .group_by(
            UserPrefs.group_label,
            UserPrefs.comuna,
            UserPrefs.localidad,
        )
        .order_by(
            func.lower(UserPrefs.group_label),
            func.lower(UserPrefs.comuna),
            func.lower(UserPrefs.localidad),
        )
    )

    groups: list[ProdesalGroupMetrics] = []
    for row in db.execute(stmt):
        delivered = int(row.delivered or 0)
        failed = int(row.failed or 0)
        confirmed = delivered + failed
        last_activity: datetime.datetime | None = row.last_activity
        groups.append(
            ProdesalGroupMetrics(
                group_label=str(row.group_label).strip(),
                comuna=str(row.comuna).strip() if row.comuna else None,
                localidad=str(row.localidad).strip() if row.localidad else None,
                total_consultations=int(row.total_consultations or 0),
                delivered=delivered,
                failed=failed,
                pending=int(row.pending or 0),
                delivery_rate=round(delivered / confirmed, 3) if confirmed else 0.0,
                avg_latency_ms=round(float(row.avg_latency_ms or 0.0), 1),
                last_activity=(last_activity.isoformat(timespec="seconds") if last_activity is not None else None),
            )
        )
    return groups


def get_total_30d(db: Session) -> int:
    """Total de consultas procesadas en los últimos 30 días."""
    cutoff = datetime.datetime.now() - datetime.timedelta(days=30)
    return (
        db.scalar(
            select(func.count(Consultation.id)).where(
                Consultation.created_at >= cutoff, Consultation.is_test.is_(False)
            )
        )
        or 0
    )


def get_audio_avg(db: Session, days: int = 30) -> float:
    """Promedio de duración de audio de entrada en segundos (ventana N días)."""
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    avg_ms = db.scalar(
        select(func.avg(Consultation.audio_duration_ms)).where(
            Consultation.created_at >= cutoff, Consultation.is_test.is_(False)
        )
    )
    return round(avg_ms / 1000, 1) if avg_ms else 0.0


def get_all_odepa_products(db: Session, days: int = 30) -> list[ProductStat]:
    """Todos los productos ODEPA con sus registros y conteo de consultas.

    A diferencia de get_top_products, esta función retorna TODOS los productos
    disponibles en ODEPA, incluso si no tienen consultas en el período.
    """
    cutoff = _days_ago(days)

    # Cacheamos la lista de productos UNA vez fuera del loop para evitar N+1:
    # antes se consultaba la DB por cada consulta.
    productos_cache = list_products(db)

    conteos = _contar_productos_estructurados(db, cutoff, productos_cache)

    # Obtener registros ODEPA y fecha última actualización por producto
    stmt_records = select(
        OdepaPrice.producto,
        func.count(OdepaPrice.id).label("cnt"),
        func.max(OdepaPrice.created_at).label("last"),
    ).group_by(OdepaPrice.producto)
    odepa_data: dict[str, tuple[int, str]] = {
        prod: (cnt, last.isoformat(timespec="seconds") if last else "")
        for (prod, cnt, last) in db.execute(stmt_records).all()
    }

    # Retornar TODOS los productos ODEPA ordenados por nombre (reusar cache)
    todos_productos = sorted(productos_cache)
    max_q = max(conteos.values()) if conteos else 1

    return [
        ProductStat(
            name=nombre,
            queries=conteos.get(nombre, 0),
            pct=round(conteos.get(nombre, 0) / max_q * 100, 1) if max_q else 0.0,
            records=odepa_data.get(nombre, (0, ""))[0],
            updated=odepa_data.get(nombre, (0, ""))[1],
        )
        for nombre in todos_productos
    ]


def build_sparkline_paths(values: list[int]) -> tuple[str, str]:
    """Wrapper público de _build_sparkline para uso desde el template/router."""
    return _build_sparkline(values)


def get_odepa_status(db: Session) -> OdepaStatus:
    """Retorna totales y cardinalidades de ODEPA en una sola query.

    Combina 4 aggregates (count, max fecha, distinct producto, distinct
    mercado) en un único SELECT para evitar 4 round-trips a la DB. Lo usan
    tanto el endpoint JSON /admin/odepa/status como el dashboard HTML.
    """
    stmt = select(
        func.count(OdepaPrice.id),
        func.max(OdepaPrice.fecha),
        func.count(func.distinct(OdepaPrice.producto)),
        func.count(func.distinct(OdepaPrice.mercado)),
    )
    total, ultima, productos, mercados = db.execute(stmt).one()
    return OdepaStatus(
        total_filas=int(total or 0),
        ultima_fecha=ultima,
        productos=int(productos or 0),
        mercados=int(mercados or 0),
    )


# ── Métricas de piloto (Issue #97) ─────────────────────────────────


@dataclass(frozen=True)
class PilotoMetrics:
    """Métricas del piloto para Crea INACAP (sección 7.3 del paper).

    Criterios de éxito:
    - productores_activos: COUNT(DISTINCT phone_hash) con 3+ consultas
    - consultas_por_productor: AVG(COUNT consultas por phone_hash)
    - pct_utiles: COUNT(feedback="util") / COUNT(feedback IS NOT NULL) * 100
    - latencia_promedio_ms: AVG(latency_ms) vs target 15000ms
    - decisiones_productivas: COUNT(decision_productiva=True)
    """

    productores_activos: int
    consultas_por_productor: float
    pct_utiles: float
    latencia_promedio_ms: float
    latencia_target_ms: int = 15_000
    decisiones_productivas: int = 0
    total_consultas: int = 0
    total_feedback_util: int = 0
    total_feedback_no_util: int = 0
    total_con_feedback: int = 0


def get_piloto_metrics(db: Session) -> PilotoMetrics:
    """Calcula las 5 métricas del piloto para Crea INACAP.

    Ejecuta queries optimizadas sobre la tabla Consultation.
    """
    # 1. Productores activos: DISTINCT phone_hash con 3+ consultas.
    # Necesitamos contar los grupos, no los distinct. Usar subquery.
    subq = (
        select(Consultation.phone_hash)
        .where(Consultation.is_test.is_(False))
        .group_by(Consultation.phone_hash)
        .having(func.count(Consultation.id) >= 3)
    ).subquery()
    productores_activos = db.scalar(select(func.count()).select_from(subq)) or 0

    # 2. Consultas por productor: AVG de consultas por phone_hash.
    stmt_por_productor = (
        select(func.count(Consultation.id)).where(Consultation.is_test.is_(False)).group_by(Consultation.phone_hash)
    )
    conteos = [int(c) for c in db.execute(stmt_por_productor).scalars().all()]
    consultas_por_productor = round(sum(conteos) / len(conteos), 1) if conteos else 0.0

    # 3. % útiles: feedback="util" / feedback IS NOT NULL * 100.
    total_con_feedback = (
        db.scalar(
            select(func.count(Consultation.id)).where(
                Consultation.feedback.is_not(None), Consultation.is_test.is_(False)
            )
        )
        or 0
    )
    total_feedback_util = (
        db.scalar(
            select(func.count(Consultation.id)).where(Consultation.feedback == "util", Consultation.is_test.is_(False))
        )
        or 0
    )
    total_feedback_no_util = (
        db.scalar(
            select(func.count(Consultation.id)).where(
                Consultation.feedback == "no_util", Consultation.is_test.is_(False)
            )
        )
        or 0
    )
    pct_utiles = round(total_feedback_util / total_con_feedback * 100, 1) if total_con_feedback > 0 else 0.0

    # 4. Latencia promedio.
    latencia_promedio = (
        db.scalar(select(func.avg(Consultation.latency_ms)).where(Consultation.is_test.is_(False))) or 0.0
    )

    # 5. Decisiones productivas.
    decisiones = (
        db.scalar(
            select(func.count(Consultation.id)).where(
                Consultation.decision_productiva == True,  # noqa: E712
                Consultation.is_test.is_(False),
            )
        )
        or 0
    )

    # Total de consultas.
    total = db.scalar(select(func.count(Consultation.id)).where(Consultation.is_test.is_(False))) or 0

    return PilotoMetrics(
        productores_activos=int(productores_activos),
        consultas_por_productor=consultas_por_productor,
        pct_utiles=pct_utiles,
        latencia_promedio_ms=round(float(latencia_promedio), 1),
        decisiones_productivas=int(decisiones),
        total_consultas=int(total),
        total_feedback_util=int(total_feedback_util),
        total_feedback_no_util=int(total_feedback_no_util),
        total_con_feedback=int(total_con_feedback),
    )


def get_piloto_export_data(db: Session) -> list[dict[str, object]]:
    """Datos para export CSV del piloto.

    Retorna lista de dicts con las métricas calculadas.
    """
    metrics = get_piloto_metrics(db)
    return [
        {"metrica": "Productores activos (3+ consultas)", "valor": metrics.productores_activos},
        {"metrica": "Consultas por productor (promedio)", "valor": metrics.consultas_por_productor},
        {"metrica": "% respuestas útiles", "valor": f"{metrics.pct_utiles}%"},
        {"metrica": "Latencia promedio (ms)", "valor": metrics.latencia_promedio_ms},
        {"metrica": "Latencia target (ms)", "valor": metrics.latencia_target_ms},
        {"metrica": "Casos de decisión productiva", "valor": metrics.decisiones_productivas},
        {"metrica": "Total consultas", "valor": metrics.total_consultas},
        {"metrica": "Feedback útil", "valor": metrics.total_feedback_util},
        {"metrica": "Feedback no útil", "valor": metrics.total_feedback_no_util},
        {"metrica": "Total con feedback", "valor": metrics.total_con_feedback},
    ]


def get_piloto_consultations_with_feedback(db: Session, limit: int = 50) -> list[Consultation]:
    """Obtiene consultas recientes con feedback para la tabla del piloto.

    Args:
        db: Sesión de SQLAlchemy.
        limit: Número máximo de consultas a retornar.

    Returns:
        Lista de Consultation con feedback, ordenadas por created_at descendente.
    """
    consultas = db.scalars(
        select(Consultation)
        .where(Consultation.feedback.is_not(None), Consultation.is_test.is_(False))
        .order_by(Consultation.created_at.desc())
        .limit(limit)
    ).all()
    return list(consultas)


def toggle_decision_productiva(db: Session, consultation_id: int) -> bool | None:
    """Toggle del campo decision_productiva de una consulta.

    Retorna el nuevo valor de decision_productiva si se encontró y actualizó,
    None si no se encontró la consulta.
    """
    consulta = db.scalars(select(Consultation).where(Consultation.id == consultation_id)).first()
    if consulta is None:
        return None
    consulta.decision_productiva = not consulta.decision_productiva
    db.commit()
    logger.info(
        "Decision productiva toggled — consultation_id=%d nuevo_valor=%s",
        consultation_id,
        consulta.decision_productiva,
    )
    return consulta.decision_productiva
