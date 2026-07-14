"""Tests para el cron job de alertas climáticas (Issue #88).

Cubre:
- Evaluación de alertas de clima contra pronóstico de OpenMeteo.
- Disparo de alertas solo cuando se cumplen umbrales (helada < 2C, lluvia > 50mm).
- Control de disparo: máximo 1 vez al día (last_triggered_at).
- Manejo de errores: timeout/fallo de weather API no causa crash.
- Múltiples alertas: algunas disparan, otras no, según umbrales.
- Job wrapper: _ejecutar() y main() del módulo check_weather_alerts.py.
"""

import datetime
import sys
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.services.alert_service import evaluar_alertas_clima
from app.services.weather_service import ForecastDay

# ── Helpers ────────────────────────────────────────────────────────


def _crear_alerta_clima(
    db: Session,
    phone_hash: str = "phone" + "a" * 56,
    wa_chat_id: str | None = "56912345678@c.us",
    umbral_clima: str = "helada",
    activa: bool = True,
    last_triggered_at: datetime.datetime | None = None,
) -> Alert:
    """Inserta una alerta climática de prueba."""
    alerta = Alert(
        phone_hash=phone_hash,
        wa_chat_id=wa_chat_id,
        tipo="clima",
        umbral_clima=umbral_clima,
        activa=activa,
        last_triggered_at=last_triggered_at,
    )
    db.add(alerta)
    db.commit()
    return alerta


def _forecast_helada() -> list[ForecastDay]:
    """Pronóstico con helada: temp min de 0°C mañana."""
    hoy = datetime.date.today()
    return [
        ForecastDay(
            fecha=hoy,
            temp_max_c=Decimal("12.0"),
            temp_min_c=Decimal("5.0"),
            precipitation_sum_mm=Decimal("10.0"),
        ),
        ForecastDay(
            fecha=hoy + datetime.timedelta(days=1),
            temp_max_c=Decimal("5.0"),
            temp_min_c=Decimal("0.0"),  # Helada: < 2°C
            precipitation_sum_mm=Decimal("5.0"),
        ),
    ]


def _forecast_lluvia_extrema() -> list[ForecastDay]:
    """Pronóstico con lluvia extrema: > 50mm mañana."""
    hoy = datetime.date.today()
    return [
        ForecastDay(
            fecha=hoy,
            temp_max_c=Decimal("15.0"),
            temp_min_c=Decimal("10.0"),
            precipitation_sum_mm=Decimal("10.0"),
        ),
        ForecastDay(
            fecha=hoy + datetime.timedelta(days=1),
            temp_max_c=Decimal("10.0"),
            temp_min_c=Decimal("5.0"),
            precipitation_sum_mm=Decimal("60.0"),  # Lluvia extrema: > 50mm
        ),
    ]


def _forecast_sin_evento() -> list[ForecastDay]:
    """Pronóstico normal sin eventos extremos."""
    hoy = datetime.date.today()
    return [
        ForecastDay(
            fecha=hoy,
            temp_max_c=Decimal("12.0"),
            temp_min_c=Decimal("8.0"),
            precipitation_sum_mm=Decimal("5.0"),
        ),
        ForecastDay(
            fecha=hoy + datetime.timedelta(days=1),
            temp_max_c=Decimal("10.0"),
            temp_min_c=Decimal("7.0"),
            precipitation_sum_mm=Decimal("2.0"),
        ),
    ]


# ── Tests ──────────────────────────────────────────────────────────


class TestEvaluarAlertasClima:
    """Evaluación de alertas climáticas contra pronóstico."""

    @pytest.mark.asyncio
    async def test_sin_alertas_retorna_lista_vacia(self, db: Session) -> None:
        """Sin alertas activas, retorna lista vacía."""
        with patch("app.services.alert_service.get_weather_forecast_daily") as mock_weather:
            mock_weather.return_value = _forecast_sin_evento()
            enviados = await evaluar_alertas_clima(db, None)  # type: ignore[arg-type]
            assert enviados == []

    @pytest.mark.asyncio
    async def test_alerta_helada_dispara(self, db: Session) -> None:
        """Alerta de helada dispara cuando temp min < 2°C."""
        _crear_alerta_clima(db, umbral_clima="helada")
        enviados_reales: list[str] = []

        async def mock_enviar(wa_chat_id: str, mensaje: str) -> None:
            enviados_reales.append(wa_chat_id)

        with (
            patch("app.services.alert_service.get_weather_forecast_daily") as mock_weather,
            patch("app.services.alert_service.enviar_alerta", side_effect=mock_enviar),
        ):
            mock_weather.return_value = _forecast_helada()
            enviados = await evaluar_alertas_clima(db, None)  # type: ignore[arg-type]

        assert len(enviados) == 1
        assert len(enviados_reales) == 1

    @pytest.mark.asyncio
    async def test_alerta_lluvia_extrema_dispara(self, db: Session) -> None:
        """Alerta de lluvia extrema dispara cuando precip > 50mm."""
        _crear_alerta_clima(db, umbral_clima="lluvia_extrema")
        enviados_reales: list[str] = []

        async def mock_enviar(wa_chat_id: str, mensaje: str) -> None:
            enviados_reales.append(wa_chat_id)

        with (
            patch("app.services.alert_service.get_weather_forecast_daily") as mock_weather,
            patch("app.services.alert_service.enviar_alerta", side_effect=mock_enviar),
        ):
            mock_weather.return_value = _forecast_lluvia_extrema()
            enviados = await evaluar_alertas_clima(db, None)  # type: ignore[arg-type]

        assert len(enviados) == 1
        assert len(enviados_reales) == 1

    @pytest.mark.asyncio
    async def test_alerta_no_dispara_sin_umbral(self, db: Session) -> None:
        """Alerta que no cumple umbral no dispara."""
        _crear_alerta_clima(db, umbral_clima="helada")

        with patch("app.services.alert_service.get_weather_forecast_daily") as mock_weather:
            mock_weather.return_value = _forecast_sin_evento()  # Sin helada
            enviados = await evaluar_alertas_clima(db, None)  # type: ignore[arg-type]

        assert enviados == []

    @pytest.mark.asyncio
    async def test_alerta_ya_disparada_hoy_no_re_dispara(self, db: Session) -> None:
        """Alerta con last_triggered_at hoy no dispara de nuevo."""
        hoy_hace_1h = datetime.datetime.now() - datetime.timedelta(hours=1)
        _crear_alerta_clima(db, umbral_clima="helada", last_triggered_at=hoy_hace_1h)

        with patch("app.services.alert_service.get_weather_forecast_daily") as mock_weather:
            mock_weather.return_value = _forecast_helada()  # Condición sí se cumple
            enviados = await evaluar_alertas_clima(db, None)  # type: ignore[arg-type]

        # Pero no dispara porque ya se disparó hoy
        assert enviados == []

    @pytest.mark.asyncio
    async def test_alerta_disparada_ayer_puede_disparar_hoy(self, db: Session) -> None:
        """Alerta disparada ayer puede disparar hoy."""
        ayer = datetime.datetime.now() - datetime.timedelta(days=1)
        _crear_alerta_clima(db, umbral_clima="helada", last_triggered_at=ayer)
        enviados_reales: list[str] = []

        async def mock_enviar(wa_chat_id: str, mensaje: str) -> None:
            enviados_reales.append(wa_chat_id)

        with (
            patch("app.services.alert_service.get_weather_forecast_daily") as mock_weather,
            patch("app.services.alert_service.enviar_alerta", side_effect=mock_enviar),
        ):
            mock_weather.return_value = _forecast_helada()
            enviados = await evaluar_alertas_clima(db, None)  # type: ignore[arg-type]

        assert len(enviados) == 1

    @pytest.mark.asyncio
    async def test_alerta_inactiva_no_procesa(self, db: Session) -> None:
        """Alerta con activa=False no se procesa."""
        _crear_alerta_clima(db, umbral_clima="helada", activa=False)

        with patch("app.services.alert_service.get_weather_forecast_daily") as mock_weather:
            mock_weather.return_value = _forecast_helada()
            enviados = await evaluar_alertas_clima(db, None)  # type: ignore[arg-type]

        assert enviados == []

    @pytest.mark.asyncio
    async def test_alerta_sin_wa_chat_id_no_envia(self, db: Session) -> None:
        """Alerta sin wa_chat_id no genera envío pero no falla."""
        _crear_alerta_clima(db, umbral_clima="helada", wa_chat_id=None)
        enviados_reales: list[str] = []

        async def mock_enviar(wa_chat_id: str, mensaje: str) -> None:
            enviados_reales.append(wa_chat_id)

        with (
            patch("app.services.alert_service.get_weather_forecast_daily") as mock_weather,
            patch("app.services.alert_service.enviar_alerta", side_effect=mock_enviar),
        ):
            mock_weather.return_value = _forecast_helada()
            enviados = await evaluar_alertas_clima(db, None)  # type: ignore[arg-type]

        # Alerta se evaluó, pero no se envió
        assert enviados == []
        assert len(enviados_reales) == 0

    @pytest.mark.asyncio
    async def test_weather_api_timeout_continua(self, db: Session) -> None:
        """Timeout de weather API no causa crash, retorna lista vacía."""
        _crear_alerta_clima(db, umbral_clima="helada")

        with patch("app.services.alert_service.get_weather_forecast_daily") as mock_weather:
            mock_weather.side_effect = ConnectionError("Weather API timeout")
            enviados = await evaluar_alertas_clima(db, None)  # type: ignore[arg-type]

        # Sin crash, retorna lista vacía
        assert enviados == []

    @pytest.mark.asyncio
    async def test_multiples_alertas_algunas_disparan(self, db: Session) -> None:
        """Con múltiples alertas, solo las que cumplen umbral disparan."""
        _crear_alerta_clima(db, phone_hash="phone1" + "a" * 55, umbral_clima="helada")
        _crear_alerta_clima(db, phone_hash="phone2" + "a" * 55, umbral_clima="lluvia_extrema")
        _crear_alerta_clima(db, phone_hash="phone3" + "a" * 55, umbral_clima="helada", activa=False)

        enviados_reales: list[str] = []

        async def mock_enviar(wa_chat_id: str, mensaje: str) -> None:
            enviados_reales.append(wa_chat_id)

        # Forecast con helada pero sin lluvia extrema
        with (
            patch("app.services.alert_service.get_weather_forecast_daily") as mock_weather,
            patch("app.services.alert_service.enviar_alerta", side_effect=mock_enviar),
        ):
            mock_weather.return_value = _forecast_helada()
            enviados = await evaluar_alertas_clima(db, None)  # type: ignore[arg-type]

        # Solo la alerta de helada dispara (phone1); lluvia no (phone2); inactiva no (phone3)
        assert len(enviados) == 1
        assert len(enviados_reales) == 1

    @pytest.mark.asyncio
    async def test_alerta_actualiza_last_triggered_at(self, db: Session) -> None:
        """Cuando dispara, se actualiza last_triggered_at."""
        alerta = _crear_alerta_clima(db, umbral_clima="helada", last_triggered_at=None)
        original_id = alerta.id

        async def mock_enviar(wa_chat_id: str, mensaje: str) -> None:
            pass

        with (
            patch("app.services.alert_service.get_weather_forecast_daily") as mock_weather,
            patch("app.services.alert_service.enviar_alerta", side_effect=mock_enviar),
        ):
            mock_weather.return_value = _forecast_helada()
            await evaluar_alertas_clima(db, None)  # type: ignore[arg-type]

        # Verificar que last_triggered_at se actualizó
        alerta_reloaded = db.query(Alert).filter_by(id=original_id).first()
        assert alerta_reloaded is not None
        assert alerta_reloaded.last_triggered_at is not None
        # Debería ser aproximadamente ahora (dentro de 1 segundo)
        now = datetime.datetime.now()
        delta = abs((now - alerta_reloaded.last_triggered_at).total_seconds())
        assert delta < 2  # Tolerancia: 2 segundos

    @pytest.mark.asyncio
    async def test_forecast_vacio(self, db: Session) -> None:
        """Pronóstico vacío (lista) no causa crash."""
        _crear_alerta_clima(db, umbral_clima="helada")

        with patch("app.services.alert_service.get_weather_forecast_daily") as mock_weather:
            mock_weather.return_value = []
            enviados = await evaluar_alertas_clima(db, None)  # type: ignore[arg-type]

        assert enviados == []

    @pytest.mark.asyncio
    async def test_alerta_con_temp_min_c_none(self, db: Session) -> None:
        """Pronóstico con temp_min_c=None no causa crash."""
        _crear_alerta_clima(db, umbral_clima="helada")

        hoy = datetime.date.today()
        forecast = [
            ForecastDay(
                fecha=hoy,
                temp_max_c=Decimal("12.0"),
                temp_min_c=None,  # Sin temperatura
                precipitation_sum_mm=Decimal("10.0"),
            ),
        ]

        with patch("app.services.alert_service.get_weather_forecast_daily") as mock_weather:
            mock_weather.return_value = forecast
            enviados = await evaluar_alertas_clima(db, None)  # type: ignore[arg-type]

        assert enviados == []

    @pytest.mark.asyncio
    async def test_alerta_con_precipitation_none(self, db: Session) -> None:
        """Pronóstico con precipitation_sum_mm=None no causa crash."""
        _crear_alerta_clima(db, umbral_clima="lluvia_extrema")

        hoy = datetime.date.today()
        forecast = [
            ForecastDay(
                fecha=hoy,
                temp_max_c=Decimal("12.0"),
                temp_min_c=Decimal("8.0"),
                precipitation_sum_mm=None,  # Sin precipitación
            ),
        ]

        with patch("app.services.alert_service.get_weather_forecast_daily") as mock_weather:
            mock_weather.return_value = forecast
            enviados = await evaluar_alertas_clima(db, None)  # type: ignore[arg-type]

        assert enviados == []


# ── Tests del wrapper del job ───────────────────────────────────────


class TestJobWrapper:
    """Tests del módulo check_weather_alerts.py (cron job wrapper)."""

    @pytest.mark.asyncio
    async def test_ejecutar_retorna_0_cuando_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_ejecutar() retorna 0 si evaluar_alertas_clima no falla."""
        mock_session = MagicMock()
        monkeypatch.setattr(
            "app.jobs.check_weather_alerts.SessionLocal",
            lambda: mock_session,
        )

        with patch(
            "app.jobs.check_weather_alerts.evaluar_alertas_clima",
            new=AsyncMock(return_value=[]),
        ):
            from app.jobs.check_weather_alerts import _ejecutar

            exit_code = await _ejecutar()
            assert exit_code == 0

    @pytest.mark.asyncio
    async def test_ejecutar_retorna_1_cuando_error_db(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_ejecutar() retorna 1 si hay error SQLAlchemy."""
        from sqlalchemy.exc import SQLAlchemyError

        mock_session = MagicMock()
        monkeypatch.setattr(
            "app.jobs.check_weather_alerts.SessionLocal",
            lambda: mock_session,
        )

        with patch(
            "app.jobs.check_weather_alerts.evaluar_alertas_clima",
            new=AsyncMock(side_effect=SQLAlchemyError("DB error")),
        ):
            from app.jobs.check_weather_alerts import _ejecutar

            exit_code = await _ejecutar()
            assert exit_code == 1

    def test_main_llama_sys_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() llama sys.exit con el código de retorno de _ejecutar()."""
        exit_codes: list[int] = []

        monkeypatch.setattr(
            "app.jobs.check_weather_alerts._ejecutar",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(sys, "exit", lambda code: exit_codes.append(code))

        from app.jobs.check_weather_alerts import main

        main()

        assert exit_codes == [0]
