"""Tests determinísticos para alertas por variación brusca de precios ODEPA."""

import datetime
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.jobs import sync_odepa as sync_job
from app.models.alert import Alert
from app.models.odepa_price import OdepaPrice
from app.models.user_prefs import UserPrefs
from app.services.alert_service import evaluar_variaciones_precio
from app.services.odepa_service import SyncResult, detectar_variaciones_precio

_MERCADO = "Mercado Mayorista Lo Valledor de Santiago"
_FECHA_ANTERIOR = datetime.date(2026, 7, 1)
_FECHA_ACTUAL = datetime.date(2026, 7, 2)


def _agregar_precio(
    db: Session,
    producto: str,
    precio: str,
    fecha: datetime.date,
    *,
    unidad: str = "kg",
) -> None:
    """Inserta un dato ODEPA de prueba."""
    db.add(
        OdepaPrice(
            producto=producto,
            mercado=_MERCADO,
            precio_kg=Decimal(precio),
            unidad=unidad,
            fecha=fecha,
        )
    )
    db.commit()


def _agregar_suscriptor(
    db: Session,
    phone_hash: str,
    *,
    cultivos: list[str] | None = None,
    alert_consent: bool = True,
    wa_chat_id: str | None = None,
) -> None:
    """Inserta preferencias y un chat conocido para el envío proactivo."""
    db.add(
        UserPrefs(
            phone_hash=phone_hash,
            cultivos=json.dumps(cultivos or ["papa"]),
            alert_consent=alert_consent,
        )
    )
    if wa_chat_id is not None:
        db.add(
            Alert(
                phone_hash=phone_hash,
                wa_chat_id=wa_chat_id,
                tipo="precio",
                producto="papa",
                condicion=">",
                umbral=Decimal("1"),
            )
        )
    db.commit()


class TestDetectarVariacionesPrecio:
    """Detector de variaciones entre datos consecutivos del mismo mercado."""

    def test_detecta_alza_exactamente_en_el_umbral(self, db: Session) -> None:
        _agregar_precio(db, "papa", "100", _FECHA_ANTERIOR)
        _agregar_precio(db, "papa", "115", _FECHA_ACTUAL)

        variaciones = detectar_variaciones_precio(db, fecha=_FECHA_ACTUAL)

        assert len(variaciones) == 1
        assert variaciones[0].variacion_pct == Decimal("15")
        assert variaciones[0].precio_actual == Decimal("115.00")

    def test_detecta_caida_critica(self, db: Session) -> None:
        _agregar_precio(db, "papa", "200", _FECHA_ANTERIOR)
        _agregar_precio(db, "papa", "160", _FECHA_ACTUAL)

        variaciones = detectar_variaciones_precio(db, fecha=_FECHA_ACTUAL)

        assert len(variaciones) == 1
        assert variaciones[0].variacion_pct == Decimal("-20")

    def test_omite_variacion_bajo_quince_por_ciento(self, db: Session) -> None:
        _agregar_precio(db, "papa", "100", _FECHA_ANTERIOR)
        _agregar_precio(db, "papa", "114", _FECHA_ACTUAL)

        assert detectar_variaciones_precio(db, fecha=_FECHA_ACTUAL) == []

    def test_omite_cambio_de_unidad(self, db: Session) -> None:
        _agregar_precio(db, "papa", "100", _FECHA_ANTERIOR)
        _agregar_precio(db, "papa", "200", _FECHA_ACTUAL, unidad="saco 25 kilos")

        assert detectar_variaciones_precio(db, fecha=_FECHA_ACTUAL) == []

    def test_omite_precio_anterior_cero(self, db: Session) -> None:
        _agregar_precio(db, "papa", "0", _FECHA_ANTERIOR)
        _agregar_precio(db, "papa", "200", _FECHA_ACTUAL)

        assert detectar_variaciones_precio(db, fecha=_FECHA_ACTUAL) == []


class TestEvaluarVariacionesPrecio:
    """Filtro por cultivo/consentimiento y entrega por Open-WA."""

    @pytest.mark.asyncio
    async def test_envia_solo_a_cultivo_con_consentimiento(self, db: Session) -> None:
        _agregar_precio(db, "papa", "100", _FECHA_ANTERIOR)
        _agregar_precio(db, "papa", "120", _FECHA_ACTUAL)
        autorizado = "a" * 64
        sin_cultivo = "b" * 64
        sin_consentimiento = "c" * 64
        _agregar_suscriptor(
            db,
            autorizado,
            wa_chat_id="56911111111@c.us",
        )
        _agregar_suscriptor(
            db,
            sin_cultivo,
            cultivos=["trigo"],
            wa_chat_id="56922222222@c.us",
        )
        _agregar_suscriptor(
            db,
            sin_consentimiento,
            alert_consent=False,
            wa_chat_id="56933333333@c.us",
        )

        with patch(
            "app.services.alert_service.enviar_alerta",
            new=AsyncMock(return_value=True),
        ) as enviar:
            enviados = await evaluar_variaciones_precio(db, settings, fecha=_FECHA_ACTUAL)

        assert enviados == [autorizado]
        enviar.assert_awaited_once()
        llamada = enviar.await_args
        assert llamada is not None
        args = llamada.args
        assert args[0] == "56911111111@c.us"
        assert "subió" in args[1]
        assert "20.0%" in args[1]
        assert "ODEPA" in args[1]
        assert args[2] == autorizado

    @pytest.mark.asyncio
    async def test_omite_suscriptor_sin_chat_conocido(self, db: Session) -> None:
        _agregar_precio(db, "papa", "100", _FECHA_ANTERIOR)
        _agregar_precio(db, "papa", "120", _FECHA_ACTUAL)
        phone_hash = "d" * 64
        _agregar_suscriptor(db, phone_hash)

        with patch(
            "app.services.alert_service.enviar_alerta",
            new=AsyncMock(),
        ) as enviar:
            enviados = await evaluar_variaciones_precio(db, settings, fecha=_FECHA_ACTUAL)

        assert enviados == []
        enviar.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rate_limit_omite_segundo_envio_sin_reventar_el_job(
        self,
        db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _agregar_precio(db, "papa", "100", _FECHA_ANTERIOR)
        _agregar_precio(db, "papa", "120", _FECHA_ACTUAL)
        _agregar_suscriptor(db, "e" * 64, wa_chat_id="56944444444@c.us")
        _agregar_suscriptor(db, "f" * 64, wa_chat_id="56955555555@c.us")
        monkeypatch.setattr(settings, "alert_rate_limit_per_minute", 1)

        with (
            patch("app.services.alert_service.enviar_alerta", new=AsyncMock(return_value=True)) as enviar,
            patch("app.services.alert_service.asyncio.sleep", new=AsyncMock()),
        ):
            enviados = await evaluar_variaciones_precio(db, settings, fecha=_FECHA_ACTUAL)

        assert len(enviados) == 1
        enviar.assert_awaited_once()


class TestSyncJobVariaciones:
    """El cron evalúa variaciones después de un sync exitoso."""

    @pytest.mark.asyncio
    async def test_job_llama_evaluador_de_variaciones(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(sync_job, "_TS_FILE", tmp_path / ".odepa_last_sync")
        monkeypatch.setattr(sync_job, "_FAIL_COUNT_FILE", tmp_path / ".odepa_fail_count")
        mock_session = MagicMock()
        with (
            patch(
                "app.jobs.sync_odepa.sync_odepa",
                new=AsyncMock(return_value=SyncResult(insertados=1)),
            ),
            patch("app.jobs.sync_odepa.SessionLocal", return_value=mock_session),
            patch("app.jobs.sync_odepa.evaluar_alertas_precio", new=AsyncMock(return_value=[])),
            patch(
                "app.jobs.sync_odepa.evaluar_variaciones_precio",
                new=AsyncMock(return_value=["a" * 64]),
            ) as evaluar,
        ):
            resultado = await sync_job._ejecutar()

        assert resultado == 0
        evaluar.assert_awaited_once_with(mock_session, settings)
        mock_session.close.assert_called_once()


class TestAlertConsentAPI:
    """El onboarding administrativo puede registrar el consentimiento explícito."""

    @pytest.mark.asyncio
    async def test_put_comuna_guarda_alert_consent(self, client: AsyncClient) -> None:
        phone_hash = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b"
        response = await client.put(
            f"/api/v1/admin/users/{phone_hash}/comuna",
            json={
                "comuna": "Traiguén",
                "cultivos": ["papa"],
                "alert_consent": True,
            },
            headers={"X-Admin-Key": "dev-admin-key"},
        )

        assert response.status_code == 200
        assert response.json()["alert_consent"] is True
