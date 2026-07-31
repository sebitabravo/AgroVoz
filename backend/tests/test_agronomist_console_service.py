"""Tests de la console de agrónomos PRODESAL: link firmado sin cuenta (C4)."""

from __future__ import annotations

import datetime

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.parcela import Parcela
from app.models.user_prefs import UserPrefs
from app.services.agronomist_console_service import (
    AgronomistLinkError,
    generate_agronomist_token,
    get_group_summary,
    validate_group_label,
    verify_agronomist_token,
)

_GROUP = "prodesal-traiguen-norte"
_NOW = 1_800_000_000


@pytest.fixture(autouse=True)
def _configure_agronomist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fija una clave de firma segura y un TTL determinista para cada test."""
    monkeypatch.setattr(settings, "agronomist_link_secret", SecretStr("x" * 32))
    monkeypatch.setattr(settings, "agronomist_link_ttl_hours", 72)


class TestValidateGroupLabel:
    """Mismo formato de slug que ComunaRequest exige para group_label."""

    @pytest.mark.parametrize(
        "label",
        ["prodesal-traiguen-norte", "prodesal-a", "prodesal-a1-b2"],
    )
    def test_labels_validos(self, label: str) -> None:
        assert validate_group_label(label) is True

    @pytest.mark.parametrize(
        "label",
        ["", "traiguen-norte", "PRODESAL-traiguen", "prodesal-", "prodesal--norte", "prodesal-Traiguén"],
    )
    def test_labels_invalidos(self, label: str) -> None:
        assert validate_group_label(label) is False


class TestGenerateAndVerifyToken:
    """Firma y verificación del token de la console."""

    def test_token_recien_generado_es_valido(self) -> None:
        token = generate_agronomist_token(_GROUP, now=_NOW)
        assert verify_agronomist_token(token, now=_NOW) == _GROUP

    def test_token_vencido_no_es_valido(self) -> None:
        token = generate_agronomist_token(_GROUP, now=_NOW)
        vencido = _NOW + 73 * 3600  # TTL es 72h

        assert verify_agronomist_token(token, now=vencido) is None

    def test_token_justo_en_el_limite_es_valido(self) -> None:
        token = generate_agronomist_token(_GROUP, now=_NOW)
        limite = _NOW + 72 * 3600

        assert verify_agronomist_token(token, now=limite) == _GROUP

    def test_firma_alterada_no_es_valida(self) -> None:
        token = generate_agronomist_token(_GROUP, now=_NOW)
        alterado = token[:-1] + ("0" if token[-1] != "0" else "1")

        assert verify_agronomist_token(alterado, now=_NOW) is None

    def test_group_label_alterado_no_es_valido(self) -> None:
        """Cambiar el group_label sin re-firmar debe invalidar el token completo."""
        token = generate_agronomist_token(_GROUP, now=_NOW)
        otro_grupo = "prodesal-otro-grupo"
        _, expires_at, signature = token.split(".")
        falsificado = f"{otro_grupo}.{expires_at}.{signature}"

        assert verify_agronomist_token(falsificado, now=_NOW) is None

    def test_expiracion_no_numerica_es_invalida(self) -> None:
        assert verify_agronomist_token(f"{_GROUP}.no-es-un-numero.firma") is None

    def test_formato_con_partes_de_mas_es_invalido(self) -> None:
        assert verify_agronomist_token(f"{_GROUP}.123.firma.extra") is None

    def test_group_label_invalido_en_el_token_es_invalido(self) -> None:
        assert verify_agronomist_token("no-es-un-slug-valido.123.firma") is None

    def test_generar_con_group_label_invalido_lanza_value_error(self) -> None:
        with pytest.raises(ValueError, match="group_label inválido"):
            generate_agronomist_token("no-es-un-slug-valido")

    def test_clave_insegura_lanza_agronomist_link_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "agronomist_link_secret", SecretStr("corta"))

        with pytest.raises(AgronomistLinkError):
            generate_agronomist_token(_GROUP, now=_NOW)

    def test_verificar_con_clave_insegura_no_lanza(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Un token viejo no debe explotar la verificación si la clave cambió mal."""
        token = generate_agronomist_token(_GROUP, now=_NOW)
        monkeypatch.setattr(settings, "agronomist_link_secret", SecretStr("corta"))

        assert verify_agronomist_token(token, now=_NOW) is None


class TestGetGroupSummary:
    """El resumen del grupo nunca expone phone_hash y respeta cada consentimiento."""

    def test_grupo_sin_contactos_retorna_none(self, db: Session) -> None:
        assert get_group_summary(db, _GROUP) is None

    def test_resumen_de_un_solo_productor(self, db: Session) -> None:
        db.add(
            UserPrefs(
                phone_hash="a" * 64,
                identity_type="prodesal_group",
                group_label=_GROUP,
                comuna="Traiguén",
                localidad="Sector Norte",
            )
        )
        db.commit()

        resumenes = get_group_summary(db, _GROUP)

        assert resumenes is not None
        assert len(resumenes) == 1
        assert resumenes[0].comuna == "Traiguén"
        assert resumenes[0].localidad == "Sector Norte"
        assert resumenes[0].cultivos == []
        assert resumenes[0].parcelas == []

    def test_resumen_agrega_varios_productores_del_mismo_grupo(self, db: Session) -> None:
        db.add(
            UserPrefs(
                phone_hash="a" * 64,
                identity_type="prodesal_group",
                group_label=_GROUP,
                comuna="Traiguén",
                cultivos='["papa"]',
            )
        )
        db.add(
            UserPrefs(
                phone_hash="b" * 64,
                identity_type="prodesal_group",
                group_label=_GROUP,
                comuna="Lautaro",
                cultivos='["trigo"]',
            )
        )
        db.commit()

        resumenes = get_group_summary(db, _GROUP)

        assert resumenes is not None
        assert len(resumenes) == 2
        assert {r.comuna for r in resumenes} == {"Traiguén", "Lautaro"}

    def test_otro_grupo_no_aparece_en_el_resumen(self, db: Session) -> None:
        db.add(
            UserPrefs(
                phone_hash="a" * 64,
                identity_type="prodesal_group",
                group_label=_GROUP,
                comuna="Traiguén",
            )
        )
        db.add(
            UserPrefs(
                phone_hash="b" * 64,
                identity_type="prodesal_group",
                group_label="prodesal-otro-grupo",
                comuna="Lautaro",
            )
        )
        db.commit()

        resumenes = get_group_summary(db, _GROUP)

        assert resumenes is not None
        assert len(resumenes) == 1
        assert resumenes[0].comuna == "Traiguén"

    def test_parcelas_solo_si_gate_y_consentimiento_activos(
        self,
        db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "parcela_tracking_enabled", True)
        db.add(
            UserPrefs(
                phone_hash="a" * 64,
                identity_type="prodesal_group",
                group_label=_GROUP,
                comuna="Traiguén",
                parcela_consent=True,
            )
        )
        db.add(
            Parcela(
                phone_hash="a" * 64,
                cultivo="papa",
                superficie_ha=2.5,
                comuna="traiguén",
                expires_at=datetime.datetime.now() + datetime.timedelta(days=300),
            )
        )
        db.commit()

        resumenes = get_group_summary(db, _GROUP)

        assert resumenes is not None
        assert resumenes[0].parcelas == [{"cultivo": "papa", "superficie_ha": 2.5, "comuna": "traiguén"}]

    def test_parcelas_ocultas_si_gate_apagado_aunque_haya_consentimiento(
        self,
        db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "parcela_tracking_enabled", False)
        db.add(
            UserPrefs(
                phone_hash="a" * 64,
                identity_type="prodesal_group",
                group_label=_GROUP,
                comuna="Traiguén",
                parcela_consent=True,
            )
        )
        db.add(
            Parcela(
                phone_hash="a" * 64,
                cultivo="papa",
                superficie_ha=2.5,
                comuna="traiguén",
                expires_at=datetime.datetime.now() + datetime.timedelta(days=300),
            )
        )
        db.commit()

        resumenes = get_group_summary(db, _GROUP)

        assert resumenes is not None
        assert resumenes[0].parcelas == []

    def test_cultivos_json_corrupto_no_rompe(self, db: Session) -> None:
        db.add(
            UserPrefs(
                phone_hash="a" * 64,
                identity_type="prodesal_group",
                group_label=_GROUP,
                comuna="Traiguén",
                cultivos="no es json valido [",
            )
        )
        db.commit()

        resumenes = get_group_summary(db, _GROUP)

        assert resumenes is not None
        assert resumenes[0].cultivos == []

    def test_resumen_nunca_expone_phone_hash(self, db: Session) -> None:
        db.add(
            UserPrefs(
                phone_hash="a" * 64,
                identity_type="prodesal_group",
                group_label=_GROUP,
                comuna="Traiguén",
            )
        )
        db.commit()

        resumenes = get_group_summary(db, _GROUP)

        assert resumenes is not None
        assert not hasattr(resumenes[0], "phone_hash")
