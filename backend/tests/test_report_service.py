"""Tests deterministas del reporte semanal de precios y clima."""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.odepa_price import OdepaPrice
from app.models.user_prefs import UserPrefs
from app.services.report_service import (
    REPORT_FORECAST_DAYS,
    ReportDisabledError,
    ReportPrice,
    WeeklyReportData,
    generate_weekly_report,
    render_weekly_report,
)
from app.services.weather_service import ForecastDay

_PHONE_HASH = "a" * 64


def _forecast() -> list[ForecastDay]:
    """Pronóstico fijo para no depender del reloj ni de OpenMeteo."""
    return [
        ForecastDay(
            fecha=datetime.date(2026, 8, 3) + datetime.timedelta(days=offset),
            temp_min_c=2.0 + offset,
            temp_max_c=14.0 + offset,
            precipitation_sum_mm=float(offset),
        )
        for offset in range(REPORT_FORECAST_DAYS)
    ]


def test_render_weekly_report_escribe_pdf_con_fuentes_y_datos(tmp_path: Path) -> None:
    """El PDF incluye precios, cinco días y las fuentes de datos."""
    data = WeeklyReportData(
        comuna="Traiguén",
        cultivos=("papa",),
        precios=(
            ReportPrice(
                cultivo="papa",
                mercado="Vega Modelo de Temuco",
                precio=Decimal("1250"),
                unidad="kg",
                fecha=datetime.date(2026, 8, 3),
            ),
        ),
        pronostico=tuple(_forecast()),
    )

    output_path = render_weekly_report(data, tmp_path / "reporte.pdf")

    contenido = output_path.read_bytes()
    assert output_path.exists()
    assert contenido.startswith(b"%PDF")
    assert b"ODEPA" in contenido
    assert b"OpenMeteo" in contenido


@pytest.mark.asyncio
async def test_generate_weekly_report_usa_cultivos_y_solicita_cinco_dias(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La generación consulta preferencias, precios y exactamente cinco días."""
    db.add(
        UserPrefs(
            phone_hash=_PHONE_HASH,
            comuna="Traiguén",
            cultivos=json.dumps(["papa", "trigo"], ensure_ascii=False),
        )
    )
    db.add(
        OdepaPrice(
            producto="papa",
            mercado="Vega Modelo de Temuco",
            precio_kg=Decimal("1250"),
            unidad="kg",
            fecha=datetime.date(2026, 8, 3),
            fuente="ODEPA",
        )
    )
    db.commit()
    weather_mock = AsyncMock(return_value=_forecast())
    monkeypatch.setattr(settings, "pdf_reports_enabled", True)
    monkeypatch.setattr("app.services.report_service.get_weather_forecast_daily", weather_mock)

    output_path = await generate_weekly_report(_PHONE_HASH, tmp_path, db)

    assert output_path.exists()
    assert output_path.suffix == ".pdf"
    assert b"papa" in output_path.read_bytes()
    weather_mock.assert_awaited_once_with(-38.23, -72.68, days=REPORT_FORECAST_DAYS)
    output_path.unlink()


@pytest.mark.asyncio
async def test_generate_weekly_report_apagado_por_defecto(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El feature gate bloquea generación antes de consultar DB o red."""
    monkeypatch.setattr(settings, "pdf_reports_enabled", False)

    with pytest.raises(ReportDisabledError):
        await generate_weekly_report(_PHONE_HASH, tmp_path)
