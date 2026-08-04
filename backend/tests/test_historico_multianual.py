"""Pruebas deterministas del resumen climático histórico multianual."""

import datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.llm_keywords import _extract_historico_request
from app.services.llm_service import TOOLS, WHITELIST_TOOLS, _get_tool_handlers
from app.services.weather_service import (
    HistoricalYearSummary,
    _clear_historical_cache,
    _format_historico_text,
    _parse_historical_response,
    fetch_historico,
    get_clima_historico_multianual,
)


def _summary(
    year: int,
    rain_mm: float,
    frost_days: int,
    season: str | None = None,
) -> HistoricalYearSummary:
    """Construye un resumen pequeño para tests de formato y Tool Calling."""
    return HistoricalYearSummary(
        year=year,
        temp_promedio=12.0,
        temp_max_promedio=18.0,
        temp_min_promedio=6.0,
        precipitacion_total_mm=rain_mm,
        dias_helada=frost_days,
        temporada=season,
    )


def test_parsea_temporada_y_cuenta_heladas_de_forma_determinista() -> None:
    """Solo los meses de invierno forman el total y el conteo de heladas."""
    data: dict[str, object] = {
        "daily": {
            "time": ["2024-06-01", "2024-07-10", "2024-09-01"],
            "temperature_2m_max": [10.0, 8.0, 20.0],
            "temperature_2m_min": [-1.0, 2.0, -4.0],
            "precipitation_sum": [10.0, 5.0, 40.0],
        }
    }

    summaries = _parse_historical_response(
        data,
        2024,
        2024,
        temporada="invierno",
    )

    assert len(summaries) == 1
    assert summaries[0].year == 2024
    assert summaries[0].temp_promedio == pytest.approx(4.75)
    assert summaries[0].precipitacion_total_mm == 15.0
    assert summaries[0].dias_helada == 1
    assert summaries[0].temporada == "invierno"


def test_formatea_comparacion_porcentual_de_lluvia_y_fuente() -> None:
    """La respuesta compara los dos periodos más recientes sin recomendar."""
    summaries = [
        _summary(2023, rain_mm=100.0, frost_days=8),
        _summary(2024, rain_mm=112.0, frost_days=10),
    ]

    text = _format_historico_text(summaries, "Traiguén")

    assert "2023" in text and "2024" in text
    assert "12% más" in text
    assert "según OpenMeteo" in text
    assert "sembr" not in text.lower()


@pytest.mark.asyncio
async def test_fetch_historico_fija_rango_de_temporada_y_reusa_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El mismo periodo explícito no dispara dos llamadas al Archive API."""
    _clear_historical_cache()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "daily": {
                    "time": ["2023-06-01", "2024-07-10"],
                    "temperature_2m_max": [10.0, 8.0],
                    "temperature_2m_min": [-1.0, -2.0],
                    "precipitation_sum": [10.0, 5.0],
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("app.services.weather_service._http_client", client)
    try:
        first = await fetch_historico(
            -38.23,
            -72.68,
            years=2,
            temporada="invierno",
            anio=2024,
        )
        second = await fetch_historico(
            -38.23,
            -72.68,
            years=2,
            temporada="invierno",
            anio=2024,
        )
    finally:
        await client.aclose()

    assert len(first) == len(second) == 2
    assert len(requests) == 1
    assert requests[0].url.params["start_date"] == "2023-06-01"
    assert requests[0].url.params["end_date"] == "2024-08-31"


@pytest.mark.asyncio
async def test_tool_multianual_entrega_parametros_y_comparacion() -> None:
    """La tool resuelve comuna, rango, temporada y métrica sin red real."""
    summaries = [
        _summary(2023, rain_mm=100.0, frost_days=8, season="invierno"),
        _summary(2024, rain_mm=112.0, frost_days=10, season="invierno"),
    ]

    with patch(
        "app.services.weather_service.fetch_historico",
        new_callable=AsyncMock,
        return_value=summaries,
    ) as fetch_mock:
        text = await get_clima_historico_multianual(
            "Traiguén",
            anos=2,
            temporada="invierno",
            anio=2024,
            metrica="lluvia",
        )

    fetch_mock.assert_awaited_once_with(
        -38.23,
        -72.68,
        years=2,
        temporada="invierno",
        anio=2024,
    )
    assert "invierno" in text
    assert "12% más" in text
    assert "según OpenMeteo" in text


def test_tool_multianual_esta_en_whitelist_y_tiene_handler() -> None:
    """La definición, whitelist y dispatcher deben mantenerse sincronizados."""
    names = [str(tool["function"]["name"]) for tool in TOOLS]

    assert "get_clima_historico_multianual" in names
    assert "get_clima_historico_multianual" in WHITELIST_TOOLS
    assert "get_clima_historico_multianual" in _get_tool_handlers()


def test_fallback_extrae_temporada_anio_y_metrica() -> None:
    """El fast-path conserva los datos explícitos cuando el LLM no responde."""
    params = _extract_historico_request("¿Cuántas heladas hubo en el invierno de 2024 en Traiguén?")

    assert params == (1, "invierno", 2024, "heladas")


def test_fallback_extrae_este_anio_comparado_con_anterior() -> None:
    """El caso motivador pide dos periodos e incluye el año actual."""
    params = _extract_historico_request("¿Llovió más este año que el anterior en Traiguén?")

    assert params == (2, None, datetime.date.today().year, "lluvia")


@pytest.mark.asyncio
async def test_fetch_actual_compara_mismo_periodo_con_reloj_inyectado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Año actual y anterior se cortan en el mismo día disponible del Archive."""
    _clear_historical_cache()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "daily": {
                    "time": ["2025-07-29", "2025-08-01", "2026-07-29"],
                    "temperature_2m_max": [10.0, 20.0, 12.0],
                    "temperature_2m_min": [2.0, 4.0, 3.0],
                    "precipitation_sum": [10.0, 99.0, 15.0],
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("app.services.weather_service._http_client", client)
    try:
        summaries = await fetch_historico(
            -38.23,
            -72.68,
            years=2,
            anio=2026,
            today=datetime.date(2026, 8, 3),
        )
    finally:
        await client.aclose()

    assert requests[0].url.params["start_date"] == "2025-01-01"
    assert requests[0].url.params["end_date"] == "2026-07-29"
    assert [summary.precipitacion_total_mm for summary in summaries] == [10.0, 15.0]
    assert all(summary.hasta_mes_dia == (7, 29) for summary in summaries)
    text = _format_historico_text(summaries, "Traiguén", metrica="lluvia")
    assert "hasta el 29/07" in text
    assert "50% más" in text
