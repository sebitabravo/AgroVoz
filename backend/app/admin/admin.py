"""Router HTML del dashboard admin (SSR Jinja2, T5.3).

Renderiza las 5 secciones del mockup (Dashboard, Métricas, ODEPA, Monitor,
Actividad) más login/logout. Server-side rendering: cada GET arma su contexto
desde los servicios y lo pasa al template.

Interactividad con HTMX (sin build step, sin JS framework):
- POST /admin/odepa/sync → partial con estado de sync tras refresh manual.
- GET /admin/monitor/refresh → partial con snapshot refresh (poll cada 30s).
- GET /admin/metrics/filter?days=N → partial con charts para la ventana elegida.

El middleware AdminAuthMiddleware (montado en main.py) protege todas las
rutas /admin/* excepto /admin/login. Acá no repetimos auth.
"""

import datetime
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app import __version__
from app.admin.auth import (
    clear_session_cookie,
    is_valid_login,
    set_session_cookie,
)
from app.core.config import settings
from app.core.database import get_db
from app.models.alert import Alert
from app.models.odepa_price import OdepaPrice
from app.services import export_service, metrics_service, monitor_service
from app.services.odepa_service import (
    _es_unidad_kilo,
    _kilos_por_unidad,
)

logger = logging.getLogger(__name__)

# Directorio de templates: app/admin/templates/. Path absoluto desde __file__
# para no depender del CWD desde donde se lance uvicorn.
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# versión visible en el footer del sidebar (base.html).
templates.env.globals["version"] = __version__

router = APIRouter(prefix="/admin", include_in_schema=False)

# ── PWA (Issue #92) ─────────────────────────────────────────────────

# El manifest vive en app/static/ (lo sirve el mount /static de main.py).
# El Service Worker NO puede ir ahi: la spec limita su scope al path del
# script y necesita scope /admin/ — por eso se sirve desde este router.
_SW_JS_PATH = _STATIC_DIR / "admin-sw.js"


@router.get("/sw.js")
async def admin_service_worker() -> FileResponse:
    """Service Worker del admin: cachea solo assets estaticos del shell.

    no-cache: el navegador revalida el SW en cada visita, asi los cambios
    de estrategia de cache llegan sin esperar expiracion de TTL.
    """
    return FileResponse(
        _SW_JS_PATH,
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache"},
    )


# ── Login / Logout ──────────────────────────────────────────────────


@router.get("/login")
async def login_form(request: Request) -> HTMLResponse:
    """Formulario de login. Público (middleware no lo protege)."""
    # El hint con la key default solo se ve en desarrollo: en prod
    # validate_admin_keys_not_default() bloquea el arranque con key default,
    # y en staging/testing solo hace warning, asi que restringirlo a dev evita
    # filtrar la credencial si staging mantiene la key default.
    return templates.TemplateResponse(request, "login.html", {"is_dev": settings.app_env == "development"})


@router.post("/login")
async def login_submit(admin_key: str = Form(..., alias="admin_key")) -> RedirectResponse:
    """Valida admin_key. Si OK, setea cookie de sesión y redirige al dashboard."""
    if is_valid_login(admin_key):
        response = RedirectResponse("/admin/", status_code=303)
        set_session_cookie(response)
        return response
    # Key inválida: vuelta al login con flag de error.
    response = RedirectResponse("/admin/login?error=1", status_code=303)
    return response


@router.post("/logout")
async def logout() -> RedirectResponse:
    """Borra cookie de sesión y vuelve al login."""
    response = RedirectResponse("/admin/login", status_code=303)
    clear_session_cookie(response)
    return response


# ── Páginas principales ─────────────────────────────────────────────


@router.get("/")
async def index(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Dashboard: KPIs, sparkline, intents, salud de servicios, consultas recientes."""
    kpis = metrics_service.get_dashboard_kpis(db)
    spark_line, spark_fill = metrics_service.build_sparkline_paths(kpis.sparkline)
    snapshot = await monitor_service.get_monitor_snapshot()
    recientes = metrics_service.get_recent_queries(db, hours=24, limit=8)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "kpis": kpis,
            "spark_line": spark_line,
            "spark_fill": spark_fill,
            "snapshot": snapshot,
            "recientes": recientes,
            "active_tab": "dashboard",
        },
    )


@router.get("/metrics")
async def metrics_page(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    days: int = 7,
) -> HTMLResponse:
    """Métricas: series diarias, latencia percentiles, intents, productos, errores."""
    days = max(1, min(days, 90))
    diario = metrics_service.get_daily_counts(db, days=days)
    latencia = metrics_service.get_latency_stats(db, days=days)
    stages = metrics_service.get_stage_stats(db, days=days)
    intents = metrics_service.get_intent_distribution(db, days=days)
    productos = metrics_service.get_top_products(db, days=days, limit=10)
    errores = metrics_service.get_error_stats(db, days=days, limit=15)
    groups = metrics_service.get_prodesal_group_metrics(db, days=days)
    total_30d = metrics_service.get_total_30d(db)
    audio_avg = metrics_service.get_audio_avg(db, days=30)
    # Datos para los charts de Chart.js: se pasan como dict al template,
    # que los serializa con el filtro |tojson de Jinja2 (escapa < > & para
    # que no puedan romper el contexto del <script>). La inicializacion de
    # Chart.js vive en /static/metrics.js y el CSP mantiene script-src
    # 'self' (sin 'unsafe-inline').
    chart_data = {
        "diario": {
            "labels": [d.date for d in diario],
            "counts": [d.count for d in diario],
        },
        "intents": {
            "precio": intents.precio,
            "clima": intents.clima,
            "credito": intents.credito,
            "desconocido": intents.desconocido,
            "total": intents.total,
        },
        "productos": (
            {
                "labels": [p.name for p in productos],
                "counts": [p.queries for p in productos],
            }
            if productos
            else None
        ),
    }
    return templates.TemplateResponse(
        request,
        "metrics.html",
        {
            "days": days,
            "diario": diario,
            "latencia": latencia,
            "stages": stages,
            "intents": intents,
            "productos": productos,
            "errores": errores,
            "groups": groups,
            "total_30d": total_30d,
            "audio_avg": audio_avg,
            "chart_data": chart_data,
            "active_tab": "metrics",
        },
    )


@router.get("/odepa")
async def odepa_page(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """ODEPA: estado de sync, stats, TODOS los productos con sus registros."""
    status = _odepa_status_dict(db)
    productos_stats = metrics_service.get_all_odepa_products(db, days=30)
    return templates.TemplateResponse(
        request,
        "odepa.html",
        {
            "status": status,
            "productos_stats": productos_stats,
            "active_tab": "odepa",
        },
    )


@router.get("/monitor")
async def monitor_page(request: Request) -> HTMLResponse:
    """Monitor: salud de servicios (Whisper, LLM, TTS, SQLite, Open-WA) + CPU/RAM/disco."""
    snapshot = await monitor_service.get_monitor_snapshot()
    return templates.TemplateResponse(
        request,
        "monitor.html",
        {"snapshot": snapshot, "now": _now_ts(), "active_tab": "monitor"},
    )


@router.get("/activity")
async def activity_page(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Actividad: tabla paginada de consultas recientes con detalle."""
    recientes = metrics_service.get_recent_queries(db, hours=168, limit=50)
    return templates.TemplateResponse(
        request,
        "activity.html",
        {"recientes": recientes, "active_tab": "activity"},
    )


# ── Export CSV ──────────────────────────────────────────────────────


@router.get("/prices/export")
async def export_prices_csv(
    producto: str | None = None,
    mercado: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    db: Session = Depends(get_db),  # noqa: B008 — FastAPI DI pattern
) -> StreamingResponse:
    """Exporta el historial de precios ODEPA como CSV descargable.

    Filtros opcionales (todos match exacto contra lo almacenado):
    - ``producto``: nombre del producto (ej: "papa").
    - ``mercado``: nombre del mercado (ej: "Lo Valledor").
    - ``desde``/``hasta``: fechas ISO YYYY-MM-DD (inclusivas).

    Sin filtros = todos los registros, ordenados por fecha descendente.

    Protegido por ``AdminAuthMiddleware`` (cookie de sesión, path="/admin"),
    igual que el resto del dashboard. Un ``<a href="/admin/prices/export">``
    en el template dispara la descarga: el navegador envía la cookie
    automáticamente (el path de la cookie cubre esta ruta). Un <a> simple
    no puede mandar headers custom, por eso el endpoint vive bajo /admin/
    y no bajo /api/v1/admin/ (que usa header X-Admin-Key).

    El CSV lleva BOM UTF-8 al inicio para que Excel en español reconozca
    el encoding. Fecha como YYYY-MM-DD, precio_kg como número crudo.
    """
    fecha_desde = _parse_iso_date(desde, "desde")
    fecha_hasta = _parse_iso_date(hasta, "hasta")

    rows = export_service.get_prices_for_export(
        db, producto=producto, mercado=mercado, desde=fecha_desde, hasta=fecha_hasta
    )
    contenido = export_service.build_prices_csv(rows)
    hoy = datetime.date.today().strftime("%Y%m%d")
    return StreamingResponse(
        iter([contenido]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="odepa_prices_{hoy}.csv"',
        },
    )


# ── Helpers para visualización de precios ────────────────────────


def _calculate_precio_por_kilo(record: OdepaPrice) -> float | None:
    """Calcula el precio real por kilo reusando helpers de odepa_service.

    Retorna:
    - float: precio_kg si unidad es kilo
    - float: precio_kg / kilos si unidad es convertible
    - None: si unidad no es convertible
    """
    if _es_unidad_kilo(record.unidad):
        return float(record.precio_kg)

    kilos = _kilos_por_unidad(record.unidad)
    if kilos is not None:
        return float(record.precio_kg / kilos)

    return None


# ── Listado de precios recientes (visualización en admin) ──────────


@router.get("/odepa/precios")
async def odepa_precios_page(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Muestra últimos precios ODEPA con precio_kg (crudo) y precio_por_kilo (calculado).

    Sección interactiva del admin: tabla de precios recientes distinguiendo
    claramente entre:
    - Precio (unidad): el valor crudo de ODEPA en su unidad de venta.
    - Precio por kilo: el valor calculado convertido a kilo (si es convertible).
    """
    q = select(OdepaPrice).order_by(OdepaPrice.fecha.desc(), OdepaPrice.id.desc()).limit(100)
    precios_recientes = db.scalars(q).all()

    # Enriquecer cada precio con el cálculo de precio_por_kilo
    precios_data = []
    for precio in precios_recientes:
        precio_por_kilo = _calculate_precio_por_kilo(precio)
        precios_data.append(
            {
                "producto": precio.producto,
                "mercado": precio.mercado,
                "precio_kg": float(precio.precio_kg),
                "unidad": precio.unidad,
                "precio_por_kilo": precio_por_kilo,
                "fecha": precio.fecha.isoformat(),
            }
        )

    return templates.TemplateResponse(
        request,
        "odepa_precios.html",
        {
            "precios": precios_data,
            "active_tab": "odepa",
        },
    )


# ── Piloto (Issue #97) ────────────────────────────────────────────


@router.get("/piloto")
async def piloto_page(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    pilot_started_at: datetime.datetime | None = None,
    pilot_ended_at: datetime.datetime | None = None,
) -> HTMLResponse:
    """Piloto: métricas de éxito del piloto para Crea INACAP (sección 7.3).

    Muestra: productores activos, consultas por productor, % útiles,
    latencia promedio vs target, casos de decisión productiva.
    """
    try:
        metrics = metrics_service.get_piloto_metrics(db, pilot_started_at, pilot_ended_at)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Consultas recientes con feedback para la tabla.
    consultas = metrics_service.get_piloto_consultations_with_feedback(
        db,
        pilot_started_at=pilot_started_at,
        pilot_ended_at=pilot_ended_at,
    )
    consultas_data = [
        {
            "id": c.id,
            "phone_hash": c.phone_hash,
            "intent": c.intent,
            "feedback": c.feedback,
            "decision_productiva": c.decision_productiva,
            "latency_ms": c.latency_ms,
            "ts": c.created_at.isoformat(timespec="seconds"),
        }
        for c in consultas
    ]
    return templates.TemplateResponse(
        request,
        "piloto.html",
        {
            "metrics": metrics,
            "consultas_con_feedback": consultas_data,
            "active_tab": "piloto",
        },
    )


# ── Alertas proactivas (issue #88) ────────────────────────────────


@router.get("/alerts")
async def alerts_page(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Lista alertas proactivas de precio y clima."""
    from sqlalchemy import func

    alertas = list(db.scalars(select(Alert).order_by(Alert.activa.desc(), Alert.created_at.desc()).limit(200)).all())
    activas_count = db.scalar(select(func.count()).select_from(Alert).where(Alert.activa.is_(True)))
    total = db.scalar(select(func.count()).select_from(Alert))
    return templates.TemplateResponse(
        request,
        "alerts.html",
        {
            "alertas": alertas,
            "activas_count": activas_count or 0,
            "total": total or 0,
            "active_tab": "alerts",
        },
    )


@router.post("/alerts/{alert_id}/cancel")
async def cancel_alert(
    alert_id: int,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """Desactiva una alerta manualmente desde el admin."""
    alerta = db.get(Alert, alert_id)
    if alerta is not None:
        alerta.activa = False
        db.commit()
        logger.info("Alerta cancelada desde admin — alert_id=%d", alert_id)
    return RedirectResponse("/admin/alerts", status_code=303)


# ── Cola de revisión humana (issue #99) ────────────────────────────


@router.get("/revision")
async def revision_page(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    status: str = "pending",
) -> HTMLResponse:
    """Cola de revisión: consultas marcadas para revisión humana.

    Query params:
        status: "pending" (default), "resolved", "all"
    """
    from app.models.consultation import Consultation

    query = select(Consultation).where(Consultation.requires_review.is_(True))

    if status == "pending":
        query = query.where(Consultation.resuelto.is_(False))
    elif status == "resolved":
        query = query.where(Consultation.resuelto.is_(True))
    # "all" no agrega filtro extra.

    query = query.order_by(Consultation.created_at.desc()).limit(100)
    results = db.execute(query).scalars().all()

    # Usa helper para consistencia en formato.
    consultas = [_format_consultation_for_view(c) for c in results]

    # Contadores para los filtros: consolidar en una sola query con case/sum.
    # select(count(case((Consultation.resuelto==False, 1)))) para pendientes,
    # select(count(case((Consultation.resuelto==True, 1)))) para resueltas.
    counts = db.execute(
        select(
            func.sum(case((Consultation.resuelto.is_(False), 1), else_=0)).label("pending"),
            func.sum(case((Consultation.resuelto.is_(True), 1), else_=0)).label("resolved"),
        )
        .select_from(Consultation)
        .where(Consultation.requires_review.is_(True))
    ).one()
    pending_count = counts.pending or 0
    resolved_count = counts.resolved or 0

    return templates.TemplateResponse(
        request,
        "revision.html",
        {
            "consultas": consultas,
            "status": status,
            "pending_count": pending_count,
            "resolved_count": resolved_count,
            "active_tab": "revision",
        },
    )


@router.post("/consultations/{consultation_id}/decision")
async def toggle_decision(
    consultation_id: int,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Toggle decision_productiva de una consulta. Endpoint HTMX.

    Retorna un partial HTML con el estado actualizado del botón.
    """
    nuevo_valor = metrics_service.toggle_decision_productiva(db, consultation_id)
    if nuevo_valor is None:
        return HTMLResponse(
            '<span style="font-size:11px;color:#c05252">no encontrada</span>',
            status_code=404,
        )
    if nuevo_valor:
        return HTMLResponse('<span style="font-size:11px;font-weight:700;color:#4f7d5a">✓ productiva</span>')
    return HTMLResponse('<span style="font-size:11px;color:#7e827a">marcar</span>')


@router.get("/piloto/export")
async def piloto_export(
    db: Session = Depends(get_db),  # noqa: B008
    pilot_started_at: datetime.datetime | None = None,
    pilot_ended_at: datetime.datetime | None = None,
) -> StreamingResponse:
    """Export CSV de las métricas del piloto.

    Genera un CSV con las métricas calculadas para el informe de Crea INACAP.
    """
    import csv
    import io

    try:
        data = metrics_service.get_piloto_export_data(db, pilot_started_at, pilot_ended_at)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["metrica", "valor"])
    writer.writeheader()
    writer.writerows(data)
    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=agrovoz_piloto_metricas.csv"},
    )


@router.post("/consultations/{consultation_id}/resolve")
async def resolve_consultation(
    consultation_id: int,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
    nota: str = Form(default=""),
) -> HTMLResponse:
    """Marca o desmarca una consulta como resuelta (toggle).

    HTMX: hx-post="/admin/consultations/{id}/resolve".
    Retorna el partial de la fila actualizada.

    Notas:
    - revisado_por siempre se asigna como "admin" (server-side).
      TODO post-MVP: capturar desde sesión de admin autenticado.
    - Si nota está vacía en el form, el campo NO se borra (append-only).
      Ver línea ~300 para el update condicional.
    """
    from app.models.consultation import Consultation

    consulta = db.get(Consultation, consultation_id)
    if consulta is None:
        return HTMLResponse("<span class='badge-error'>No encontrada</span>", status_code=404)

    # Toggle resuelto.
    consulta.resuelto = not consulta.resuelto
    # La nota es append-only: si llega vacía, no se borra.
    # El form HTMX siempre manda nota="" en toggle, pero un update incondicional
    # la borraría. Por eso hacemos if nota para solo actualizar si hay valor.
    if nota:
        consulta.nota_revision = nota
    # revisado_por asignado server-side (actualmente hardcoded a "admin").
    # Post-MVP: obtener de sesión autenticada.
    consulta.revisado_por = "admin"

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "Error al actualizar consulta — consultation_id=%d error=%s",
            consultation_id,
            type(exc).__name__,
        )
        return HTMLResponse("<span class='badge-error'>Error</span>", status_code=500)

    # Retornar partial HTMX con el estado actualizado usando helper.
    return templates.TemplateResponse(
        request,
        "_revision_row.html",
        {"c": _format_consultation_for_view(consulta)},
    )


# ── Partials HTMX ───────────────────────────────────────────────────


@router.post("/odepa/sync")
async def odepa_sync(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Dispara sync ODEPA manual y retorna el partial de status actualizado.

    HTMX: hx-post="/admin/odepa/sync" hx-target="#odepa-status" hx-swap="outerHTML".
    """
    from app.services.odepa_service import sync_odepa

    resultado = await sync_odepa()
    logger.info(
        "Sync ODEPA manual (dashboard): %d insertados, %d actualizados",
        resultado.insertados,
        resultado.actualizados,
    )
    status = _odepa_status_dict(db)
    return templates.TemplateResponse(
        request,
        "_odepa_status.html",
        {"status": status, "just_synced": True, "sync_result": resultado},
    )


@router.get("/monitor/refresh")
async def monitor_refresh(request: Request) -> HTMLResponse:
    """Partial con snapshot fresco. HTMX poll cada 30s.

    HTMX: hx-get="/admin/monitor/refresh" hx-trigger="every 30s" hx-target="#monitor-snapshot".
    """
    snapshot = await monitor_service.get_monitor_snapshot()
    return templates.TemplateResponse(
        request,
        "_monitor_snapshot.html",
        {"snapshot": snapshot, "now": _now_ts()},
    )


# ── Acciones operativas (botones del monitor) ─────────────────────────


@router.post("/monitor/reload-llm")
async def monitor_reload_llm(request: Request) -> HTMLResponse:
    """Fuerza la carga (o recarga) del modelo LLM. Retorna snapshot fresco."""
    result = monitor_service.reload_llm()
    snapshot = await monitor_service.get_monitor_snapshot()
    return templates.TemplateResponse(
        request,
        "_monitor_snapshot.html",
        {"snapshot": snapshot, "now": _now_ts(), "action_result": result},
    )


@router.post("/monitor/clear-weather-cache")
async def monitor_clear_weather_cache(request: Request) -> HTMLResponse:
    """Limpia el cache de clima en memoria. Retorna snapshot fresco."""
    deleted = monitor_service.clear_weather_cache()
    result = {"status": "ok", "detail": f"Cache de clima limpiado: {deleted} entrada(s) eliminada(s)."}
    snapshot = await monitor_service.get_monitor_snapshot()
    return templates.TemplateResponse(
        request,
        "_monitor_snapshot.html",
        {"snapshot": snapshot, "now": _now_ts(), "action_result": result},
    )


@router.post("/monitor/wa-check")
async def monitor_wa_check(request: Request) -> HTMLResponse:
    """Verifica estado de Open-WA ahora (sin esperar el poll de 30s). Retorna snapshot fresco."""
    wa_result = await monitor_service.check_openwa_now()
    snapshot = await monitor_service.get_monitor_snapshot()
    return templates.TemplateResponse(
        request,
        "_monitor_snapshot.html",
        {"snapshot": snapshot, "now": _now_ts(), "action_result": wa_result},
    )


@router.post("/monitor/clear-audio-temp")
async def monitor_clear_audio_temp(request: Request) -> HTMLResponse:
    """Elimina archivos de audio temporal en data/audio_temp/. Retorna snapshot fresco."""
    deleted = monitor_service.clear_audio_temp_files()
    count_before = deleted
    result = {
        "status": "ok",
        "detail": f"Archivos temporales de audio eliminados: {count_before} archivo(s).",
    }
    snapshot = await monitor_service.get_monitor_snapshot()
    return templates.TemplateResponse(
        request,
        "_monitor_snapshot.html",
        {"snapshot": snapshot, "now": _now_ts(), "action_result": result},
    )


# ── Helpers ─────────────────────────────────────────────────────────


def truncate_text(text: str, max_len: int = 80, suffix: str = "...") -> str:
    """Trunca texto a max_len caracteres, agregando suffix si es necesario.

    Uso: truncate_text(long_text, max_len=80) retorna 'primeros 80 chars...'
    si el texto es más largo que max_len.
    """
    if len(text) <= max_len:
        return text
    return text[:max_len] + suffix


def _format_consultation_for_view(c: object) -> dict[str, object]:
    """Arma dict de consulta para renderizar en vista de revisión.

    Normaliza truncado de texts sensibles y formatting.
    Se usa en revision_page() y resolve_consultation() para mantener
    consistencia en la representación.
    """
    query_text = str(c.query_text or "").strip()  # type: ignore[attr-defined]
    response_text = str(c.response_text or "").strip()  # type: ignore[attr-defined]
    unavailable = "Contenido no conservado"
    return {
        "id": c.id,  # type: ignore[attr-defined]
        "phone_hash_short": "protegida",
        "intent": c.intent,  # type: ignore[attr-defined]
        "query_text_short": (truncate_text(query_text, max_len=80) if query_text else unavailable),
        "response_text_short": (truncate_text(response_text, max_len=80) if response_text else unavailable),
        "created_at": c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else "",  # type: ignore[attr-defined]
        "resuelto": c.resuelto,  # type: ignore[attr-defined]
        "revisado_por": c.revisado_por or "",  # type: ignore[attr-defined]
        "nota_revision": c.nota_revision or "",  # type: ignore[attr-defined]
    }


def _now_ts() -> str:
    """Timestamp HH:MM:SS para el "Actualizado:" de los partials de monitor."""
    return datetime.datetime.now().strftime("%H:%M:%S")


def _odepa_status_dict(db: Session) -> dict[str, object]:
    """Estado ODEPA para el template: totales + fecha más reciente."""
    from app.services.metrics_service import get_odepa_status

    status = get_odepa_status(db)
    return {
        "total_filas": status.total_filas,
        "ultima_fecha": status.ultima_fecha,
        "productos": status.productos,
        "mercados": status.mercados,
        "ultima_fecha_iso": status.ultima_fecha.isoformat() if status.ultima_fecha else None,
        "ahora": datetime.datetime.now(),
    }


def _parse_iso_date(valor: str | None, campo: str) -> datetime.date | None:
    """Parsea una fecha ISO (YYYY-MM-DD). None pasa sin validar.

    Lanza 400 si el string no es una fecha ISO válida, para no dejar
    pasar filtros mal formados que devolverían DB vacía silenciosamente.
    """
    if valor is None:
        return None
    try:
        return datetime.date.fromisoformat(valor)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Formato de '{campo}' inválido. Usar YYYY-MM-DD.",
        ) from None
