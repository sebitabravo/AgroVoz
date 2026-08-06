"""Tests de la tool de pronostico (get_pronostico).

Antes de esta tool el LLM solo tenia clima ACTUAL (get_weather) e historico,
asi que "va a llover manana?" —la consulta mas comun del productor antes de
decidir si cosecha— no se podia contestar con datos. El codigo de pronostico
ya existia pero solo lo usaban las alertas de helada.

Deterministicos: sin llamadas reales a OpenMeteo.
"""

import datetime
from unittest.mock import AsyncMock, patch

from app.models.user_prefs import UserPrefs
from app.services.llm_service import TOOLS, WHITELIST_TOOLS, _get_tool_handlers
from app.services.weather_service import (
    ForecastDay,
    _format_pronostico_text,
    get_pronostico,
)


def _dia(fecha: str, tmin: float | None, tmax: float | None, mm: float | None) -> ForecastDay:
    return ForecastDay(
        fecha=datetime.date.fromisoformat(fecha),
        temp_min_c=tmin,
        temp_max_c=tmax,
        precipitation_sum_mm=mm,
    )


class TestFormatoDelTexto:
    """El texto va a TTS: sin tablas, sin simbolos que Piper deletree."""

    def test_nombra_manana_y_pasado_manana(self) -> None:
        txt = _format_pronostico_text(
            [_dia("2026-07-27", 9, 14, 23.9), _dia("2026-07-28", 9, 12, 4.4)],
            "Traiguén",
        )
        assert "Mañana" in txt
        assert "Pasado mañana" in txt

    def test_incluye_temperaturas_y_lluvia(self) -> None:
        txt = _format_pronostico_text([_dia("2026-07-27", 9, 14, 23.9)], "Traiguén")
        assert "máxima de 14 grados" in txt
        assert "mínima de 9" in txt
        assert "23,9 milímetros" in txt

    def test_decimal_con_coma_no_con_punto(self) -> None:
        """Piper lee el punto como 'punto'; en espanol la coma es lo correcto."""
        txt = _format_pronostico_text([_dia("2026-07-27", 9, 14, 4.4)], "Traiguén")
        assert "4,4 milímetros" in txt
        assert "4.4" not in txt

    def test_sin_lluvia_lo_dice_explicito(self) -> None:
        """'No llueve' tambien contesta '¿va a llover manana?'.

        Omitir la lluvia cuando es cero dejaria la pregunta sin responder.
        """
        txt = _format_pronostico_text([_dia("2026-07-27", 9, 14, 0.0)], "Traiguén")
        assert "sin lluvia" in txt

    def test_cita_la_fuente(self) -> None:
        """La atribucion es obligatoria: Open-Meteo es CC BY 4.0."""
        txt = _format_pronostico_text([_dia("2026-07-27", 9, 14, 1.0)], "Traiguén")
        assert "OpenMeteo" in txt

    def test_sin_datos_no_revienta(self) -> None:
        assert "no tengo" in _format_pronostico_text([], "Traiguén").lower()

    def test_temperaturas_nulas_no_revientan(self) -> None:
        txt = _format_pronostico_text([_dia("2026-07-27", None, None, 5.0)], "Traiguén")
        assert "5,0 milímetros" in txt

    def test_no_recomienda_practicas_agricolas(self) -> None:
        """Restriccion de producto: se entregan datos, no recomendaciones."""
        txt = _format_pronostico_text([_dia("2026-07-27", 2, 14, 30.0)], "Traiguén")
        for prohibido in ("deberías", "recomiendo", "conviene", "regá", "cosechá"):
            assert prohibido not in txt.lower()


class TestGetPronostico:
    """La tool que ve el LLM."""

    async def test_comuna_desconocida_no_llama_a_la_api(self) -> None:
        with patch(
            "app.services.weather_service.get_weather_forecast_daily",
            new_callable=AsyncMock,
        ) as mock_api:
            resp = await get_pronostico("Wakanda")
        mock_api.assert_not_awaited()
        assert "no reconozco" in resp.lower()
        assert "Traiguén" in resp

    async def test_dias_se_acota_entre_1_y_3(self) -> None:
        """Un LLM puede pedir 30 dias; OpenMeteo y el TTS no lo aguantan."""
        with patch(
            "app.services.weather_service.get_weather_forecast_daily",
            new_callable=AsyncMock,
            return_value=[_dia("2026-07-27", 9, 14, 1.0)],
        ) as mock_api:
            await get_pronostico("Traiguén", dias=30)
            assert mock_api.await_args.kwargs["days"] == 3

            await get_pronostico("Traiguén", dias=0)
            assert mock_api.await_args.kwargs["days"] == 1

    async def test_error_de_red_degrada_sin_excepcion(self) -> None:
        with patch(
            "app.services.weather_service.get_weather_forecast_daily",
            new_callable=AsyncMock,
            side_effect=ConnectionError("sin red"),
        ):
            resp = await get_pronostico("Traiguén")
        assert "no pude consultar" in resp.lower()

    async def test_respuesta_util_con_datos(self) -> None:
        with patch(
            "app.services.weather_service.get_weather_forecast_daily",
            new_callable=AsyncMock,
            return_value=[_dia("2026-07-27", 6, 14, 12.0)],
        ):
            resp = await get_pronostico("Traiguén", dias=1)
        assert "Traiguén" in resp
        assert "12,0 milímetros" in resp
        assert "OpenMeteo" in resp

    async def test_comuna_explicita_prioriza_sobre_gps_guardado(
        self,
        db,  # type: ignore[no-untyped-def]
        monkeypatch,
    ) -> None:  # type: ignore[no-untyped-def]
        """Una comuna explícita no queda pisada por el GPS guardado."""
        phone_hash = "e" * 64
        db.add(
            UserPrefs(
                phone_hash=phone_hash,
                comuna="Traiguén",
                lat=-38.23,
                lng=-72.68,
                location_consent=True,
            )
        )
        db.commit()

        async def fake_forecast(lat: float, lon: float, days: int) -> list[ForecastDay]:
            assert lat == -33.45
            assert lon == -70.65
            assert days == 1
            return [_dia("2026-07-27", 4, 17, 0)]

        monkeypatch.setattr(
            "app.services.weather_service.get_weather_forecast_daily",
            fake_forecast,
        )

        response = await get_pronostico("Santiago", dias=1, phone_hash=phone_hash)

        assert "Santiago" in response
        assert "tu parcela" not in response
        assert "OpenMeteo" in response

    async def test_sin_comuna_usa_gps_guardado(self, db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """Sin ubicación explícita, el pronóstico sí usa el pin guardado."""
        phone_hash = "f" * 64
        db.add(
            UserPrefs(
                phone_hash=phone_hash,
                lat=-38.23,
                lng=-72.68,
                location_consent=True,
            )
        )
        db.commit()

        async def fake_forecast(lat: float, lon: float, days: int) -> list[ForecastDay]:
            assert lat == -38.23
            assert lon == -72.68
            return [_dia("2026-07-27", 4, 17, 0)]

        monkeypatch.setattr("app.services.weather_service.get_weather_forecast_daily", fake_forecast)
        response = await get_pronostico(None, dias=1, phone_hash=phone_hash)

        assert "tu parcela" in response


class TestRegistroEnElLLM:
    """La tool tiene que estar realmente expuesta, no solo existir."""

    def test_esta_en_la_whitelist(self) -> None:
        assert "get_pronostico" in WHITELIST_TOOLS

    def test_tiene_definicion_de_tool(self) -> None:
        nombres = [t["function"]["name"] for t in TOOLS]
        assert "get_pronostico" in nombres

    def test_tiene_handler_conectado(self) -> None:
        """Regresion: sin handler, el LLM la llama y explota en runtime."""
        assert "get_pronostico" in _get_tool_handlers()

    def test_la_descripcion_la_distingue_de_las_otras_de_clima(self) -> None:
        """Con 3 tools de clima, el LLM tiene que saber cual usar."""
        desc = next(
            t["function"]["description"] for t in TOOLS if t["function"]["name"] == "get_pronostico"
        )
        assert "get_weather" in desc, "debe decir cuando NO usarla (clima actual)"
        assert "get_clima_historico" in desc, "debe decir cuando NO usarla (pasado)"

    def test_entra_en_el_subset_de_clima(self) -> None:
        """El subset por intent es lo que se manda al LLM en consultas de clima."""
        from app.services.llm_service import _TOOLS_CLIMA

        assert "get_pronostico" in _TOOLS_CLIMA
