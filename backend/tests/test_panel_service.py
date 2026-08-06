"""Tests del panel PWA del agricultor: link firmado sin login (C3)."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.alert import Alert
from app.models.odepa_price import OdepaPrice
from app.models.parcela import Parcela
from app.models.user_prefs import UserPrefs
from app.services.panel_service import (
    PanelLinkError,
    generate_panel_token,
    get_panel_link_for_llm,
    get_panel_price_history,
    get_panel_summary,
    verify_panel_token,
)

_PHONE_HASH = "a" * 64
_NOW = 1_800_000_000
_REFERENCE_DATE = datetime.date(2026, 8, 3)


def _insertar_precio(
    db: Session,
    *,
    producto: str = "papa",
    mercado: str = "Vega Modelo de Temuco",
    precio: str = "1200",
    fecha: datetime.date = _REFERENCE_DATE,
    unidad: str = "kg",
) -> None:
    """Inserta un dato ODEPA determinista para las pruebas del panel."""
    db.add(
        OdepaPrice(
            producto=producto,
            mercado=mercado,
            precio_kg=Decimal(precio),
            unidad=unidad,
            fecha=fecha,
        )
    )


@pytest.fixture(autouse=True)
def _configure_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fija una clave de firma segura, un TTL determinista y el gate encendido."""
    monkeypatch.setattr(settings, "panel_link_secret", SecretStr("x" * 32))
    monkeypatch.setattr(settings, "panel_link_ttl_hours", 24)
    monkeypatch.setattr(settings, "farmer_panel_enabled", True)


class TestGenerateAndVerifyToken:
    """Firma y verificación del token del panel."""

    def test_token_recien_generado_es_valido(self) -> None:
        token = generate_panel_token(_PHONE_HASH, now=_NOW)
        assert verify_panel_token(token, now=_NOW) == _PHONE_HASH

    def test_token_vencido_no_es_valido(self) -> None:
        token = generate_panel_token(_PHONE_HASH, now=_NOW)
        vencido = _NOW + 25 * 3600  # TTL es 24h

        assert verify_panel_token(token, now=vencido) is None

    def test_token_justo_en_el_limite_es_valido(self) -> None:
        token = generate_panel_token(_PHONE_HASH, now=_NOW)
        limite = _NOW + 24 * 3600

        assert verify_panel_token(token, now=limite) == _PHONE_HASH

    def test_firma_alterada_no_es_valida(self) -> None:
        token = generate_panel_token(_PHONE_HASH, now=_NOW)
        alterado = token[:-1] + ("0" if token[-1] != "0" else "1")

        assert verify_panel_token(alterado, now=_NOW) is None

    def test_phone_hash_alterado_no_es_valido(self) -> None:
        """Cambiar el hash sin re-firmar debe invalidar el token completo."""
        token = generate_panel_token(_PHONE_HASH, now=_NOW)
        otro_hash = "b" * 64
        _, expires_at, signature = token.split(".")
        falsificado = f"{otro_hash}.{expires_at}.{signature}"

        assert verify_panel_token(falsificado, now=_NOW) is None

    def test_expiracion_no_numerica_es_invalida(self) -> None:
        assert verify_panel_token(f"{_PHONE_HASH}.no-es-un-numero.firma") is None

    def test_formato_con_partes_de_mas_es_invalido(self) -> None:
        assert verify_panel_token(f"{_PHONE_HASH}.123.firma.extra") is None

    def test_hash_invalido_en_el_token_es_invalido(self) -> None:
        assert verify_panel_token("no-es-un-hash-valido.123.firma") is None

    def test_generar_con_hash_invalido_lanza_value_error(self) -> None:
        with pytest.raises(ValueError, match="phone_hash inválido"):
            generate_panel_token("no-es-un-hash-valido")

    def test_clave_insegura_lanza_panel_link_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "panel_link_secret", SecretStr("corta"))

        with pytest.raises(PanelLinkError):
            generate_panel_token(_PHONE_HASH, now=_NOW)

    def test_verificar_con_clave_insegura_no_lanza(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Un token viejo no debe explotar la verificación si la clave cambió mal."""
        token = generate_panel_token(_PHONE_HASH, now=_NOW)
        monkeypatch.setattr(settings, "panel_link_secret", SecretStr("corta"))

        assert verify_panel_token(token, now=_NOW) is None


class TestGetPanelLinkForLlm:
    """Mensaje con el link, respetando los gates operativos."""

    def test_gate_apagado_no_genera_link(self) -> None:
        assert "no está disponible" in get_panel_link_for_llm(_PHONE_HASH).lower()

    def test_sin_base_url_no_genera_link(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "farmer_panel_enabled", True)
        monkeypatch.setattr(settings, "panel_base_url", "")

        assert "no está disponible" in get_panel_link_for_llm(_PHONE_HASH).lower()

    def test_hash_invalido_no_genera_link(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "farmer_panel_enabled", True)
        monkeypatch.setattr(settings, "panel_base_url", "https://app.agrovoz.cl/panel")

        response = get_panel_link_for_llm("no-es-un-hash-valido")

        assert "de forma segura" in response.lower()

    def test_link_valido_incluye_url_y_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "farmer_panel_enabled", True)
        monkeypatch.setattr(settings, "panel_base_url", "https://app.agrovoz.cl/panel")

        response = get_panel_link_for_llm(_PHONE_HASH)

        assert "https://app.agrovoz.cl/panel/" in response
        assert "24 horas" in response


class TestGetPanelSummary:
    """El resumen solo expone lo que el productor ya consintió."""

    def test_sin_preferencias_retorna_none(self, db: Session) -> None:
        assert get_panel_summary(db, _PHONE_HASH) is None

    def test_resumen_minimo_sin_consentimientos(self, db: Session) -> None:
        db.add(UserPrefs(phone_hash=_PHONE_HASH, comuna="Traiguén"))
        db.commit()

        summary = get_panel_summary(db, _PHONE_HASH)

        assert summary is not None
        assert summary.comuna == "Traiguén"
        assert summary.cultivos == []
        assert summary.parcelas == []
        assert summary.alertas == []

    def test_parcelas_solo_si_gate_y_consentimiento_activos(
        self,
        db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "parcela_tracking_enabled", True)
        db.add(
            UserPrefs(
                phone_hash=_PHONE_HASH,
                comuna="Traiguén",
                parcela_consent=True,
                cultivos='["papa", "trigo"]',
            )
        )
        db.add(
            Parcela(
                phone_hash=_PHONE_HASH,
                cultivo="papa",
                superficie_ha=2.5,
                comuna="traiguén",
                expires_at=datetime.datetime.now() + datetime.timedelta(days=300),
            )
        )
        db.commit()

        summary = get_panel_summary(db, _PHONE_HASH)

        assert summary is not None
        assert summary.cultivos == ["papa", "trigo"]
        assert summary.parcelas == [{"cultivo": "papa", "superficie_ha": 2.5, "comuna": "traiguén"}]

    def test_parcela_vencida_no_aparece_en_el_panel(
        self,
        db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Una fila vencida no debe verse aunque la purga programada no haya pasado."""
        monkeypatch.setattr(settings, "parcela_tracking_enabled", True)
        db.add(
            UserPrefs(
                phone_hash=_PHONE_HASH,
                comuna="Traiguén",
                parcela_consent=True,
            )
        )
        db.add(
            Parcela(
                phone_hash=_PHONE_HASH,
                cultivo="papa",
                superficie_ha=2.5,
                comuna="traiguén",
                expires_at=datetime.datetime.now() - datetime.timedelta(seconds=1),
            )
        )
        db.add(
            Parcela(
                phone_hash=_PHONE_HASH,
                cultivo="trigo",
                superficie_ha=1.0,
                comuna="traiguén",
                expires_at=datetime.datetime.now() + datetime.timedelta(days=300),
            )
        )
        db.commit()

        summary = get_panel_summary(db, _PHONE_HASH)

        assert summary is not None
        assert summary.parcelas == [{"cultivo": "trigo", "superficie_ha": 1.0, "comuna": "traiguén"}]

    def test_resumen_vacio_si_el_gate_del_panel_esta_apagado(
        self,
        db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """El servicio corta por su cuenta, sin depender del router."""
        monkeypatch.setattr(settings, "farmer_panel_enabled", False)
        db.add(UserPrefs(phone_hash=_PHONE_HASH, comuna="Traiguén"))
        db.commit()

        assert get_panel_summary(db, _PHONE_HASH) is None

    def test_parcelas_ocultas_si_gate_apagado_aunque_haya_consentimiento(
        self,
        db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "parcela_tracking_enabled", False)
        db.add(UserPrefs(phone_hash=_PHONE_HASH, comuna="Traiguén", parcela_consent=True))
        db.add(
            Parcela(
                phone_hash=_PHONE_HASH,
                cultivo="papa",
                superficie_ha=2.5,
                comuna="traiguén",
                expires_at=datetime.datetime.now() + datetime.timedelta(days=300),
            )
        )
        db.commit()

        summary = get_panel_summary(db, _PHONE_HASH)

        assert summary is not None
        assert summary.parcelas == []

    def test_alertas_solo_si_hay_consentimiento(self, db: Session) -> None:
        db.add(UserPrefs(phone_hash=_PHONE_HASH, comuna="Traiguén", alert_consent=True))
        db.add(
            Alert(
                phone_hash=_PHONE_HASH,
                tipo="precio",
                producto="papa",
                condicion=">",
                umbral=500,
                activa=True,
            )
        )
        db.commit()

        summary = get_panel_summary(db, _PHONE_HASH)

        assert summary is not None
        assert summary.alertas == [{"tipo": "precio", "producto": "papa", "condicion": ">", "umbral": 500.0}]

    def test_alertas_inactivas_no_aparecen(self, db: Session) -> None:
        db.add(UserPrefs(phone_hash=_PHONE_HASH, comuna="Traiguén", alert_consent=True))
        db.add(
            Alert(
                phone_hash=_PHONE_HASH,
                tipo="precio",
                producto="papa",
                condicion=">",
                umbral=500,
                activa=False,
            )
        )
        db.commit()

        summary = get_panel_summary(db, _PHONE_HASH)

        assert summary is not None
        assert summary.alertas == []

    def test_cultivos_json_corrupto_no_rompe(self, db: Session) -> None:
        db.add(UserPrefs(phone_hash=_PHONE_HASH, comuna="Traiguén", cultivos="no es json valido ["))
        db.commit()

        summary = get_panel_summary(db, _PHONE_HASH)

        assert summary is not None
        assert summary.cultivos == []


class TestGetPanelPriceHistory:
    """Historial ODEPA filtrado por identidad, cultivo y ventana temporal."""

    def test_retorna_28_dias_del_mercado_de_la_comuna(self, db: Session) -> None:
        db.add(UserPrefs(phone_hash=_PHONE_HASH, comuna="Traiguén", cultivos='["papa", "trigo"]'))
        _insertar_precio(db, fecha=_REFERENCE_DATE - datetime.timedelta(days=27), precio="1000")
        _insertar_precio(db, fecha=_REFERENCE_DATE - datetime.timedelta(days=7), precio="1150")
        _insertar_precio(db, fecha=_REFERENCE_DATE, precio="1200")
        _insertar_precio(db, fecha=_REFERENCE_DATE - datetime.timedelta(days=28), precio="500")
        _insertar_precio(db, producto="trigo", precio="900")
        _insertar_precio(db, mercado="Vega de Osorno", precio="300")
        db.commit()

        history = get_panel_price_history(db, _PHONE_HASH, reference_date=_REFERENCE_DATE)

        assert history is not None
        assert history.desde == _REFERENCE_DATE - datetime.timedelta(days=27)
        assert history.hasta == _REFERENCE_DATE
        assert len(history.cultivos) == 2
        papa = history.cultivos[0]
        assert papa.cultivo == "papa"
        assert papa.mercado == "Vega Modelo de Temuco"
        assert papa.unidad == "kg"
        assert [(point.fecha, point.precio) for point in papa.precios] == [
            (_REFERENCE_DATE - datetime.timedelta(days=27), Decimal("1000.00")),
            (_REFERENCE_DATE - datetime.timedelta(days=7), Decimal("1150.00")),
            (_REFERENCE_DATE, Decimal("1200.00")),
        ]

    def test_no_mezcla_unidades_y_omite_otro_mercado(self, db: Session) -> None:
        db.add(UserPrefs(phone_hash=_PHONE_HASH, comuna="Traiguén", cultivos='["papa"]'))
        _insertar_precio(db, fecha=_REFERENCE_DATE - datetime.timedelta(days=1), precio="1200")
        _insertar_precio(db, fecha=_REFERENCE_DATE, precio="900", unidad="$/saco 25 kilos")
        db.add(
            OdepaPrice(
                producto="papa",
                mercado="Vega de Osorno",
                precio_kg=Decimal("9999"),
                unidad="kg",
                fecha=_REFERENCE_DATE,
                fuente="ODEPA",
            )
        )
        db.commit()

        history = get_panel_price_history(db, _PHONE_HASH, reference_date=_REFERENCE_DATE)

        assert history is not None
        assert len(history.cultivos) == 1
        assert history.cultivos[0].unidad == "$/saco 25 kilos"
        assert [point.precio for point in history.cultivos[0].precios] == [Decimal("900.00")]

    def test_sin_preferencias_retorna_none(self, db: Session) -> None:
        assert get_panel_price_history(db, _PHONE_HASH, reference_date=_REFERENCE_DATE) is None

    def test_sin_datos_retorna_historial_vacio(self, db: Session) -> None:
        db.add(UserPrefs(phone_hash=_PHONE_HASH, comuna="Traiguén", cultivos='["papa"]'))
        db.commit()

        history = get_panel_price_history(db, _PHONE_HASH, reference_date=_REFERENCE_DATE)

        assert history is not None
        assert history.cultivos == []
