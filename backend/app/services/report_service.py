"""Generación del reporte PDF semanal de precios y clima.

El reporte es un complemento opcional de WhatsApp. Lee únicamente los
cultivos y la comuna ya registrados en ``user_prefs`` y combina esos precios
ODEPA con cinco días de datos crudos de OpenMeteo. No agrega recomendaciones
agronómicas ni persiste el documento: el caller debe borrarlo después del
envío.
"""

from __future__ import annotations

import datetime
import json
import logging
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.formato import formatear_pesos
from app.core.phone_hash import validate_phone_hash
from app.models.user_prefs import UserPrefs
from app.services.odepa_service import query_latest_by_product
from app.services.weather_service import ForecastDay, get_weather_forecast_daily, resolver_comuna

logger = logging.getLogger(__name__)

REPORT_FILENAME = "agrovoz-reporte-semanal.pdf"
REPORT_CAPTION = "Reporte semanal de precios ODEPA y clima OpenMeteo."
REPORT_FORECAST_DAYS = 5
REPORT_TOOL_SIGNAL = "__AGROVOZ_REPORT_PDF__"
_DEFAULT_COMUNA = "Traiguén"


class ReportGenerationError(RuntimeError):
    """La información necesaria para generar el reporte no está disponible."""


class ReportDisabledError(ReportGenerationError):
    """El reporte fue solicitado mientras su feature gate está apagado."""


@dataclass(frozen=True)
class ReportPrice:
    """Precio ODEPA mostrado para un cultivo y mercado."""

    cultivo: str
    mercado: str
    precio: Decimal | None
    unidad: str
    fecha: datetime.date | None


@dataclass(frozen=True)
class WeeklyReportData:
    """Datos ya resueltos que alimentan el PDF."""

    comuna: str
    cultivos: tuple[str, ...]
    precios: tuple[ReportPrice, ...]
    pronostico: tuple[ForecastDay, ...]


def _parse_cultivos(raw_cultivos: str | None) -> tuple[str, ...]:
    """Lee la lista JSON de cultivos sin confiar en su contenido persistido."""
    if not raw_cultivos:
        return ()

    try:
        parsed: object = json.loads(raw_cultivos)
    except json.JSONDecodeError:
        logger.warning("Cultivos no válidos en preferencias — se omiten del reporte")
        return ()

    if not isinstance(parsed, list):
        return ()

    cultivos: list[str] = []
    vistos: set[str] = set()
    for item in parsed:
        if not isinstance(item, str):
            continue
        cultivo = " ".join(item.split()).strip()
        clave = cultivo.casefold()
        if cultivo and clave not in vistos:
            cultivos.append(cultivo)
            vistos.add(clave)
    return tuple(cultivos)


def _resolve_comuna(comuna: str | None) -> tuple[str, float, float]:
    """Resuelve la comuna registrada y cae a Traiguén sin inventar coordenadas."""
    nombre = " ".join((comuna or "").split()).strip() or _DEFAULT_COMUNA
    coords = resolver_comuna(nombre)
    if coords is not None:
        return nombre, coords[0], coords[1]

    default_coords = resolver_comuna(_DEFAULT_COMUNA)
    if default_coords is None:
        raise ReportGenerationError("No hay coordenadas para la comuna predeterminada")
    logger.warning("Comuna no reconocida para reporte — se usa ubicación predeterminada")
    return _DEFAULT_COMUNA, default_coords[0], default_coords[1]


def _load_report_data(
    session: Session,
    phone_hash: str,
    pronostico: tuple[ForecastDay, ...],
) -> WeeklyReportData:
    """Carga preferencias y precios de una identidad seudonimizada."""
    prefs = session.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
    comuna, _, _ = _resolve_comuna(prefs.comuna if prefs is not None else None)
    cultivos = _parse_cultivos(prefs.cultivos if prefs is not None else None)

    precios: list[ReportPrice] = []
    for cultivo in cultivos:
        try:
            latest_by_market = query_latest_by_product(session, cultivo)
        except (SQLAlchemyError, ValueError):
            logger.warning("Precio no disponible para cultivo del reporte")
            latest_by_market = {}

        if not latest_by_market:
            precios.append(ReportPrice(cultivo, "Sin datos ODEPA", None, "", None))
            continue

        precios.extend(
            ReportPrice(
                cultivo=cultivo,
                mercado=record.mercado,
                precio=record.precio_kg,
                unidad=record.unidad,
                fecha=record.fecha,
            )
            for record in sorted(latest_by_market.values(), key=lambda item: item.mercado.casefold())
        )

    return WeeklyReportData(comuna, cultivos, tuple(precios), pronostico)


def _paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    """Crea un párrafo escapando datos provenientes de SQLite o OpenMeteo."""
    return Paragraph(escape(text), style)


def _price_rows(data: WeeklyReportData, body_style: ParagraphStyle) -> list[list[Paragraph | str]]:
    """Arma las filas de la tabla de precios, incluida la ausencia honesta."""
    header = ["Cultivo", "Mercado", "Precio", "Unidad", "Fecha"]
    rows: list[list[Paragraph | str]] = [header]
    for price in data.precios:
        rows.append(
            [
                _paragraph(price.cultivo, body_style),
                _paragraph(price.mercado, body_style),
                _paragraph(formatear_pesos(price.precio) if price.precio is not None else "Sin dato", body_style),
                _paragraph(price.unidad or "—", body_style),
                _paragraph(price.fecha.isoformat() if price.fecha is not None else "—", body_style),
            ]
        )
    if len(rows) == 1:
        rows.append(["Sin cultivos registrados", "—", "—", "—", "—"])
    return rows


def _forecast_rows(data: WeeklyReportData, body_style: ParagraphStyle) -> list[list[Paragraph | str]]:
    """Arma las filas del pronóstico sin transformarlo en consejo."""
    rows: list[list[Paragraph | str]] = [["Fecha", "Mínima", "Máxima", "Lluvia"]]
    for day in data.pronostico:
        rows.append(
            [
                _paragraph(day.fecha.isoformat(), body_style),
                _paragraph(f"{day.temp_min_c:.1f} °C" if day.temp_min_c is not None else "Sin dato", body_style),
                _paragraph(f"{day.temp_max_c:.1f} °C" if day.temp_max_c is not None else "Sin dato", body_style),
                _paragraph(
                    f"{day.precipitation_sum_mm:.1f} mm"
                    if day.precipitation_sum_mm is not None
                    else "Sin dato",
                    body_style,
                ),
            ]
        )
    if len(rows) == 1:
        rows.append(["Sin pronóstico disponible", "—", "—", "—"])
    return rows


def render_weekly_report(data: WeeklyReportData, output_path: Path) -> Path:
    """Renderiza ``data`` como PDF y retorna la ruta escrita.

    El PDF usa solo fuentes y componentes incluidos en ReportLab para que la
    generación sea reproducible en el VPS mínimo, sin descargar assets.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "AgroVozTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontName="Helvetica-Bold",
        fontSize=18,
        spaceAfter=8,
    )
    body_style = ParagraphStyle(
        "AgroVozBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
    )
    section_style = ParagraphStyle(
        "AgroVozSection",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11,
        spaceBefore=8,
        spaceAfter=5,
    )

    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title="AgroVoz — Reporte semanal",
        author="AgroVoz",
        pageCompression=0,
    )
    story: list[object] = [
        _paragraph("AgroVoz — Reporte semanal", title_style),
        _paragraph(f"Precios y clima para {data.comuna}", body_style),
        Spacer(1, 6),
        _paragraph("Precios de cultivos", section_style),
    ]

    price_table = Table(_price_rows(data, body_style), colWidths=[27 * mm, 62 * mm, 27 * mm, 34 * mm, 25 * mm])
    price_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f5d3a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.HexColor("#edf4ed")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(price_table)
    story.extend(
        [
            Spacer(1, 6),
            _paragraph("Fuente de precios: ODEPA. Son datos mayoristas, sin interpretación.", body_style),
            _paragraph("Pronóstico de cinco días", section_style),
        ]
    )

    forecast_table = Table(_forecast_rows(data, body_style), colWidths=[38 * mm, 38 * mm, 38 * mm, 38 * mm])
    forecast_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#365f91")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.HexColor("#eef3fa")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.extend(
        [
            forecast_table,
            Spacer(1, 6),
            _paragraph(
                "Fuente del pronóstico: OpenMeteo. El reporte entrega datos, "
                "no recomendaciones agronómicas.",
                body_style,
            ),
        ]
    )
    document.build(story)
    return output_path


def _new_report_path(output_dir: Path | None) -> Path:
    """Crea un nombre temporal no predecible dentro del staging de reportes."""
    directory = output_dir or Path(__file__).resolve().parent.parent.parent / "data" / "report_temp"
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="reporte_", suffix=".pdf", dir=directory, delete=False) as handle:
        return Path(handle.name)


async def generate_weekly_report(
    phone_hash: str,
    output_dir: Path | None = None,
    session: Session | None = None,
) -> Path:
    """Genera un PDF semanal para la identidad y deja su limpieza al caller."""
    if not settings.pdf_reports_enabled:
        raise ReportDisabledError("Los reportes PDF no están habilitados.")
    if not validate_phone_hash(phone_hash):
        raise ReportGenerationError("No pude asociar el reporte de forma segura.")

    owns_session = session is None
    db = session or SessionLocal()
    try:
        prefs = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
        comuna, lat, lon = _resolve_comuna(prefs.comuna if prefs is not None else None)
        pronostico = tuple(await get_weather_forecast_daily(lat, lon, days=REPORT_FORECAST_DAYS))
        data = _load_report_data(db, phone_hash, pronostico)
        # _load_report_data normaliza la comuna otra vez para mantener una sola
        # fuente de verdad si una preferencia cambió durante la espera de red.
        data = WeeklyReportData(comuna, data.cultivos, data.precios, data.pronostico)
    except (SQLAlchemyError, OSError, RuntimeError, ValueError) as exc:
        raise ReportGenerationError("No pude reunir los datos del reporte.") from exc
    finally:
        if owns_session:
            db.close()

    output_path = _new_report_path(output_dir)
    try:
        return render_weekly_report(data, output_path)
    except (OSError, RuntimeError, ValueError) as exc:
        output_path.unlink(missing_ok=True)
        raise ReportGenerationError("No pude escribir el PDF del reporte.") from exc


def get_reporte_pdf_for_llm(phone_hash: str = "") -> str:
    """Devuelve una señal interna para que el pipeline genere el adjunto."""
    if not settings.pdf_reports_enabled:
        return "Los reportes PDF todavía no están habilitados."
    if not validate_phone_hash(phone_hash):
        return "No pude asociar el reporte de forma segura."
    return REPORT_TOOL_SIGNAL
