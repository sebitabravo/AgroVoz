"""Endpoints admin de métricas (JSON, T5.1).

Devuelven agregaciones sobre Consultation para consumo programático:
dashboard, series diarias, latencia, intents, top productos, errores,
consultas recientes. El dashboard HTML usa estos mismos servicios vía SSR.

Todos requieren header X-Admin-Key (hmac.compare_digest).
"""

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.admin.deps import require_admin_key
from app.core.database import get_db
from app.services import metrics_service

router = APIRouter(
    prefix="/admin/metrics",
    tags=["admin-metrics"],
    dependencies=[Depends(require_admin_key)],
)

# Ventanas y límites acotados para evitar queries pesadas sobre SQLite.
Days = Annotated[int, Query(ge=1, le=90, description="Días de la ventana")]
Limit = Annotated[int, Query(ge=1, le=100, description="Máximo de resultados")]


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)) -> dict[str, Any]:  # noqa: B008
    """KPIs principales: hoy, latencia, tasa de éxito, agricultores activos."""
    kpis = metrics_service.get_dashboard_kpis(db)
    spark_line, spark_fill = metrics_service.build_sparkline_paths(kpis.sparkline)
    payload = asdict(kpis)
    payload["sparkline_line_path"] = spark_line
    payload["sparkline_fill_path"] = spark_fill
    return payload


@router.get("/daily")
def daily(
    db: Session = Depends(get_db),  # noqa: B008
    days: Days = 30,
) -> list[dict[str, Any]]:
    """Consultas agrupadas por día (sin huecos) para los últimos N días."""
    return [asdict(d) for d in metrics_service.get_daily_counts(db, days=days)]


@router.get("/latency")
def latency(
    db: Session = Depends(get_db),  # noqa: B008
    days: Days = 30,
) -> dict[str, Any]:
    """Estadísticos de latencia (avg, p50, p95, p99) en ms."""
    return asdict(metrics_service.get_latency_stats(db, days=days))


@router.get("/stages")
def stages(
    db: Session = Depends(get_db),  # noqa: B008
    days: Days = 30,
) -> dict[str, Any]:
    """Desglose de latencia promedio por etapa del pipeline (Whisper/LLM/TTS) en ms."""
    return asdict(metrics_service.get_stage_stats(db, days=days))


@router.get("/intents")
def intents(
    db: Session = Depends(get_db),  # noqa: B008
    days: Days = 30,
) -> dict[str, Any]:
    """Distribución de intents (precio, clima, desconocido)."""
    return asdict(metrics_service.get_intent_distribution(db, days=days))


@router.get("/products")
def products(
    db: Session = Depends(get_db),  # noqa: B008
    days: Days = 30,
    limit: Limit = 10,
) -> list[dict[str, Any]]:
    """Top productos mencionados en consultas de precio."""
    return [asdict(p) for p in metrics_service.get_top_products(db, days=days, limit=limit)]


@router.get("/errors")
def errors(
    db: Session = Depends(get_db),  # noqa: B008
    days: Days = 30,
    limit: Limit = 20,
) -> dict[str, Any]:
    """Tasa de error (intent desconocido) + últimos errores."""
    return asdict(metrics_service.get_error_stats(db, days=days, limit=limit))


@router.get("/recent")
def recent(
    db: Session = Depends(get_db),  # noqa: B008
    hours: Annotated[int, Query(ge=1, le=168, description="Horas hacia atrás")] = 24,
    limit: Limit = 20,
) -> list[dict[str, Any]]:
    """Consultas más recientes para la tabla del dashboard."""
    return [asdict(r) for r in metrics_service.get_recent_queries(db, hours=hours, limit=limit)]
