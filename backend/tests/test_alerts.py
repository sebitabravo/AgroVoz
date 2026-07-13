"""Tests para app.services.alert_service.

Deterministicos: sin OpenMeteo real, sin Open-WA real, sin TTS real.
Mockean envio de alertas y pronostico climatico.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.alert import Alert
from app.models.odepa_price import OdepaPrice
from app.services.alert_service import (
    MAX_ALERTAS_ACTIVAS,
    AlertServiceError,
    cancelar_alertas,
    create_clima_alert,
    create_price_alert,
    enviar_alerta,
    evaluar_alertas_clima,
    evaluar_alertas_precio,
)
from app.services.pipeline_service import AgroVozPipeline
from app.services.weather_service import ForecastDay


@pytest.fixture
def phone_hash() -> str:
    """Hash de ejemplo para tests."""
    return "a" * 64


@pytest.fixture
def wa_chat_id() -> str:
    """Chat ID de WhatsApp de ejemplo."""
    return "56912345678@c.us"


@pytest_asyncio.fixture
async def mock_enviar_alerta() -> AsyncMock:
    """Mockea enviar_alerta para no sintetizar ni enviar audio real."""
    with patch(
        "app.services.alert_service.enviar_alerta",
        new_callable=AsyncMock,
    ) as mock:
        yield mock


@pytest_asyncio.fixture
async def seed_precio_papa(db: Session) -> OdepaPrice:
    """Crea un precio de referencia de papa por kilo en Lo Valledor."""
    registro = OdepaPrice(
        producto="papa",
        mercado="Mercado Mayorista Lo Valledor de Santiago",
        precio_kg=Decimal("12000.00"),
        unidad="kg",
        fecha=date.today(),
    )
    db.add(registro)
    db.commit()
    return registro


@pytest_asyncio.fixture
async def seed_alerta_precio(
    db: Session,
    phone_hash: str,
    wa_chat_id: str,
) -> Alert:
    """Crea una alerta de precio activa."""
    alerta = Alert(
        phone_hash=phone_hash,
        wa_chat_id=wa_chat_id,
        tipo="precio",
        producto="papa",
        condicion=">",
        umbral=Decimal("10000.00"),
        activa=True,
    )
    db.add(alerta)
    db.commit()
    return alerta


@pytest_asyncio.fixture
async def seed_alerta_clima(
    db: Session,
    phone_hash: str,
    wa_chat_id: str,
) -> Alert:
    """Crea una alerta de clima activa."""
    alerta = Alert(
        phone_hash=phone_hash,
        wa_chat_id=wa_chat_id,
        tipo="clima",
        umbral_clima="helada",
        activa=True,
    )
    db.add(alerta)
    db.commit()
    return alerta


class TestCreatePriceAlert:
    """Creacion de alertas de precio."""

    @pytest.mark.asyncio
    async def test_crear_alerta_precio_ok(
        self,
        db: Session,
        phone_hash: str,
        wa_chat_id: str,
    ) -> None:
        mensaje = await create_price_alert(
            db,
            phone_hash,
            wa_chat_id,
            "papa",
            ">",
            Decimal("10000"),
        )
        assert "te avisare" in mensaje
        assert "papa" in mensaje

        alerta = db.query(Alert).first()
        assert alerta is not None
        assert alerta.tipo == "precio"
        assert alerta.producto == "papa"
        assert alerta.condicion == ">"
        assert alerta.umbral == Decimal("10000")
        assert alerta.activa is True
        assert alerta.wa_chat_id == wa_chat_id

    @pytest.mark.asyncio
    async def test_condicion_invalida(
        self,
        db: Session,
        phone_hash: str,
    ) -> None:
        with pytest.raises(AlertServiceError, match="Condicion invalida"):
            await create_price_alert(
                db,
                phone_hash,
                None,
                "papa",
                "=",
                Decimal("10000"),
            )

    @pytest.mark.asyncio
    async def test_umbral_debe_ser_positivo(
        self,
        db: Session,
        phone_hash: str,
    ) -> None:
        with pytest.raises(AlertServiceError, match="mayor a cero"):
            await create_price_alert(
                db,
                phone_hash,
                None,
                "papa",
                ">",
                Decimal("0"),
            )


class TestCreateClimaAlert:
    """Creacion de alertas climaticas."""

    @pytest.mark.asyncio
    async def test_crear_alerta_helada(
        self,
        db: Session,
        phone_hash: str,
        wa_chat_id: str,
    ) -> None:
        mensaje = await create_clima_alert(db, phone_hash, wa_chat_id, "helada")
        assert "helada" in mensaje
        assert "2 grados" in mensaje

        alerta = db.query(Alert).first()
        assert alerta is not None
        assert alerta.tipo == "clima"
        assert alerta.umbral_clima == "helada"

    @pytest.mark.asyncio
    async def test_crear_alerta_lluvia_extrema(
        self,
        db: Session,
        phone_hash: str,
        wa_chat_id: str,
    ) -> None:
        mensaje = await create_clima_alert(db, phone_hash, wa_chat_id, "lluvia")
        assert "lluvia extrema" in mensaje
        assert "50 milimetros" in mensaje

        alerta = db.query(Alert).first()
        assert alerta.umbral_clima == "lluvia_extrema"

    @pytest.mark.asyncio
    async def test_umbral_clima_invalido(
        self,
        db: Session,
        phone_hash: str,
    ) -> None:
        with pytest.raises(AlertServiceError, match="Umbral climatico invalido"):
            await create_clima_alert(db, phone_hash, None, "viento")


class TestCancelAlertas:
    """Cancelacion de alertas."""

    @pytest.mark.asyncio
    async def test_cancelar_todas_las_alertas(
        self,
        db: Session,
        phone_hash: str,
        seed_alerta_precio: Alert,
        seed_alerta_clima: Alert,
    ) -> None:
        count = await cancelar_alertas(db, phone_hash)
        assert count == 2
        assert db.query(Alert).filter(Alert.activa.is_(True)).count() == 0

    @pytest.mark.asyncio
    async def test_cancelar_por_tipo(
        self,
        db: Session,
        phone_hash: str,
        seed_alerta_precio: Alert,
        seed_alerta_clima: Alert,
    ) -> None:
        count = await cancelar_alertas(db, phone_hash, tipo="precio")
        assert count == 1
        assert seed_alerta_precio.activa is False
        assert seed_alerta_clima.activa is True

    @pytest.mark.asyncio
    async def test_cancelar_sin_alertas(
        self,
        db: Session,
        phone_hash: str,
    ) -> None:
        count = await cancelar_alertas(db, phone_hash)
        assert count == 0


class TestEvaluarAlertasPrecio:
    """Disparo de alertas de precio."""

    @pytest.mark.asyncio
    async def test_dispara_cuando_se_cumple_condicion(
        self,
        db: Session,
        phone_hash: str,
        seed_alerta_precio: Alert,
        seed_precio_papa: OdepaPrice,
        mock_enviar_alerta: AsyncMock,
    ) -> None:
        enviados = await evaluar_alertas_precio(db, settings)
        assert phone_hash in enviados
        mock_enviar_alerta.assert_awaited_once()
        mensaje = mock_enviar_alerta.await_args[0][1]
        assert "Alerta de precio" in mensaje
        assert "Papa" in mensaje
        assert "12.000 pesos" in mensaje or "12000" in mensaje

    @pytest.mark.asyncio
    async def test_no_dispara_cuando_no_se_cumple(
        self,
        db: Session,
        phone_hash: str,
        seed_precio_papa: OdepaPrice,
        mock_enviar_alerta: AsyncMock,
    ) -> None:
        alerta = Alert(
            phone_hash=phone_hash,
            wa_chat_id="56912345678@c.us",
            tipo="precio",
            producto="papa",
            condicion=">",
            umbral=Decimal("50000"),
            activa=True,
        )
        db.add(alerta)
        db.commit()

        enviados = await evaluar_alertas_precio(db, settings)
        assert phone_hash not in enviados
        mock_enviar_alerta.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rate_limit_una_por_dia(
        self,
        db: Session,
        phone_hash: str,
        seed_alerta_precio: Alert,
        seed_precio_papa: OdepaPrice,
        mock_enviar_alerta: AsyncMock,
    ) -> None:
        seed_alerta_precio.last_triggered_at = datetime.now() - timedelta(hours=2)
        db.commit()

        enviados = await evaluar_alertas_precio(db, settings)
        assert phone_hash not in enviados
        mock_enviar_alerta.assert_not_awaited()


class TestEvaluarAlertasClima:
    """Disparo de alertas climaticas."""

    @pytest.mark.asyncio
    async def test_dispara_alerta_helada(
        self,
        db: Session,
        phone_hash: str,
        seed_alerta_clima: Alert,
        mock_enviar_alerta: AsyncMock,
    ) -> None:
        forecast = [
            ForecastDay(
                fecha=date.today() + timedelta(days=1),
                temp_min_c=0.5,
                temp_max_c=12.0,
                precipitation_sum_mm=0.0,
            )
        ]
        with patch(
            "app.services.alert_service.get_weather_forecast_daily",
            new_callable=AsyncMock,
            return_value=forecast,
        ):
            enviados = await evaluar_alertas_clima(db, settings)

        assert phone_hash in enviados
        mock_enviar_alerta.assert_awaited_once()
        mensaje = mock_enviar_alerta.await_args[0][1]
        assert "helada" in mensaje
        assert "OpenMeteo" in mensaje

    @pytest.mark.asyncio
    async def test_mensaje_clima_no_recomienda_practicas(
        self,
        db: Session,
        phone_hash: str,
        seed_alerta_clima: Alert,
        mock_enviar_alerta: AsyncMock,
    ) -> None:
        forecast = [
            ForecastDay(
                fecha=date.today() + timedelta(days=1),
                temp_min_c=0.5,
                temp_max_c=12.0,
                precipitation_sum_mm=0.0,
            )
        ]
        with patch(
            "app.services.alert_service.get_weather_forecast_daily",
            new_callable=AsyncMock,
            return_value=forecast,
        ):
            await evaluar_alertas_clima(db, settings)

        mensaje = mock_enviar_alerta.await_args[0][1]
        # Solo informa el pronostico; no recomienda acciones agronomicas.
        assert "cubre" not in mensaje.lower()
        assert "protege" not in mensaje.lower()
        assert "riega" not in mensaje.lower()
        assert "segun OpenMeteo" in mensaje

    @pytest.mark.asyncio
    async def test_no_dispara_si_no_cumple(
        self,
        db: Session,
        phone_hash: str,
        seed_alerta_clima: Alert,
        mock_enviar_alerta: AsyncMock,
    ) -> None:
        forecast = [
            ForecastDay(
                fecha=date.today() + timedelta(days=1),
                temp_min_c=5.0,
                temp_max_c=12.0,
                precipitation_sum_mm=0.0,
            )
        ]
        with patch(
            "app.services.alert_service.get_weather_forecast_daily",
            new_callable=AsyncMock,
            return_value=forecast,
        ):
            enviados = await evaluar_alertas_clima(db, settings)

        assert phone_hash not in enviados
        mock_enviar_alerta.assert_not_awaited()


class TestLimitesAlertas:
    """Topes y rate limiting."""

    @pytest.mark.asyncio
    async def test_tope_alertas_activas(
        self,
        db: Session,
        phone_hash: str,
    ) -> None:
        for i in range(MAX_ALERTAS_ACTIVAS):
            db.add(
                Alert(
                    phone_hash=phone_hash,
                    wa_chat_id=None,
                    tipo="precio",
                    producto=f"producto{i}",
                    condicion=">",
                    umbral=Decimal("1000"),
                    activa=True,
                )
            )
        db.commit()

        with pytest.raises(AlertServiceError, match="5 alertas activas"):
            await create_price_alert(db, phone_hash, None, "tomate", ">", Decimal("2000"))


class TestEnviarAlerta:
    """Envio de audio proactivo."""

    @pytest.mark.asyncio
    async def test_enviar_alerta_sintetiza_y_envia(
        self,
    ) -> None:
        with (
            patch(
                "app.services.alert_service.TTSService.synthesize",
                return_value="/tmp/fake.ogg",
            ),
            patch(
                "app.services.alert_service.OpenWAService.send_audio",
                new_callable=AsyncMock,
            ) as mock_send,
            patch("app.services.alert_service.Path.unlink") as mock_unlink,
        ):
            await enviar_alerta("56912345678@c.us", "Alerta de prueba")

        mock_send.assert_awaited_once()
        mock_unlink.assert_called_once()


class TestPipelineAlertCommands:
    """Deteccion de comandos de alerta por voz en el pipeline."""

    @pytest.mark.asyncio
    async def test_detecta_alerta_precio(self) -> None:
        with patch(
            "app.services.alert_service.create_price_alert",
            new_callable=AsyncMock,
            return_value="Alerta de precio creada",
        ) as mock_create:
            response, intent = await AgroVozPipeline._handle_alert_commands(
                "avisame cuando la papa pase de 10000 pesos",
                "a" * 64,
                "56912345678@c.us",
            )

        assert response == "Alerta de precio creada"
        assert intent == "alerta"
        mock_create.assert_awaited_once()
        args = mock_create.await_args[0]
        assert args[3] == "papa"  # producto
        assert args[4] == ">"  # condicion
        assert args[5] == Decimal("10000")

    @pytest.mark.asyncio
    async def test_detecta_alerta_helada(self) -> None:
        with patch(
            "app.services.alert_service.create_clima_alert",
            new_callable=AsyncMock,
            return_value="Alerta de clima creada",
        ) as mock_create:
            response, intent = await AgroVozPipeline._handle_alert_commands(
                "avisame si viene helada",
                "a" * 64,
                "56912345678@c.us",
            )

        assert response == "Alerta de clima creada"
        assert intent == "alerta"
        mock_create.assert_awaited_once()
        assert mock_create.await_args[0][3] == "helada"

    @pytest.mark.asyncio
    async def test_detecta_cancelar_alertas(self) -> None:
        with patch(
            "app.services.alert_service.cancelar_alertas",
            new_callable=AsyncMock,
            return_value=2,
        ) as mock_cancel:
            response, intent = await AgroVozPipeline._handle_alert_commands(
                "cancelar alertas",
                "a" * 64,
                None,
            )

        assert "cancelado" in response
        assert intent == "alerta"
        mock_cancel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_detecta_consulta_normal(self) -> None:
        response, intent = await AgroVozPipeline._handle_alert_commands(
            "cual es el precio de la papa",
            "a" * 64,
            None,
        )
        assert response is None
        assert intent is None
