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
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import __version__
from app.admin.auth import (
    clear_session_cookie,
    is_valid_login,
    set_session_cookie,
)
from app.core.database import get_db
from app.services import metrics_service, monitor_service

logger = logging.getLogger(__name__)

# Directorio de templates: app/admin/templates/. Path absoluto desde __file__
# para no depender del CWD desde donde se lance uvicorn.
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# versión visible en el footer del sidebar (base.html).
templates.env.globals["version"] = __version__

router = APIRouter(prefix="/admin", include_in_schema=False)


# ── Login / Logout ──────────────────────────────────────────────────


@router.get("/login")
async def login_form(request: Request) -> HTMLResponse:
    """Formulario de login. Público (middleware no lo protege)."""
    return templates.TemplateResponse(request, "login.html", {})


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
    total_30d = metrics_service.get_total_30d(db)
    audio_avg = metrics_service.get_audio_avg(db, days=30)
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
            "total_30d": total_30d,
            "audio_avg": audio_avg,
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
        {"snapshot": snapshot, "active_tab": "monitor"},
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
        {"snapshot": snapshot},
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
        {"snapshot": snapshot, "action_result": result},
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
        {"snapshot": snapshot, "action_result": result},
    )


@router.post("/monitor/wa-check")
async def monitor_wa_check(request: Request) -> HTMLResponse:
    """Verifica estado de Open-WA ahora (sin esperar el poll de 30s). Retorna snapshot fresco."""
    wa_result = await monitor_service.check_openwa_now()
    snapshot = await monitor_service.get_monitor_snapshot()
    return templates.TemplateResponse(
        request,
        "_monitor_snapshot.html",
        {"snapshot": snapshot, "action_result": wa_result},
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
        {"snapshot": snapshot, "action_result": result},
    )


# ── Helpers ─────────────────────────────────────────────────────────


def _odepa_status_dict(db: Session) -> dict[str, Any]:
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
