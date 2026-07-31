"""Regresiones de parcelas consentidas, TTL y borrado (C5)."""

import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.parcela import Parcela
from app.models.user_prefs import UserPrefs
from app.services.parcela_service import (
    ParcelaOperationError,
    _parse_superficie_ha,
    delete_parcelas_for_subject,
    get_parcelas_for_llm,
    purge_expired_parcelas,
    register_parcela_for_llm,
)

_PHONE_HASH_A = "a" * 64
_PHONE_HASH_B = "b" * 64
_NOW = datetime.datetime(2026, 7, 30, 12, 0)


def _enable_parcelas(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    *,
    phone_hash: str = _PHONE_HASH_A,
    consent: bool = True,
) -> None:
    """Habilita gate y crea una identidad con consentimiento explícito."""
    monkeypatch.setattr(settings, "parcela_tracking_enabled", True)
    monkeypatch.setattr(settings, "parcela_retention_days", 365)
    monkeypatch.setattr("app.services.parcela_service._utcnow_naive", lambda: _NOW)
    db.add(
        UserPrefs(
            phone_hash=phone_hash,
            comuna="Traiguén",
            parcela_consent=consent,
        )
    )
    db.commit()


@pytest.mark.parametrize(
    ("raw_superficie", "expected"),
    [
        ("2", Decimal("2.00")),
        ("2.5", Decimal("2.50")),
        ("2,5", Decimal("2.50")),
        ("0.1", Decimal("0.10")),
    ],
)
def test_parse_superficie_ha_formatos_validos(raw_superficie: str, expected: Decimal) -> None:
    assert _parse_superficie_ha(raw_superficie) == expected


@pytest.mark.parametrize("raw_superficie", ["", "cero", "0", "-1", "100001"])
def test_parse_superficie_ha_rechaza_valores_inseguros(raw_superficie: str) -> None:
    assert _parse_superficie_ha(raw_superficie) is None


def test_gate_apagado_no_guarda(db: Session) -> None:
    """El default fail-closed no crea registros de parcela."""
    response = register_parcela_for_llm(db, "papa", "2", "Traiguén", phone_hash=_PHONE_HASH_A)

    assert "no guardé" in response.lower()
    assert db.scalars(select(Parcela)).all() == []


def test_sin_consentimiento_no_guarda(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_parcelas(db, monkeypatch, consent=False)

    response = register_parcela_for_llm(db, "papa", "2", "Traiguén", phone_hash=_PHONE_HASH_A)

    assert "no tengo tu consentimiento" in response.lower()
    assert db.scalars(select(Parcela)).all() == []


def test_hash_invalido_no_guarda(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_parcelas(db, monkeypatch)

    response = register_parcela_for_llm(db, "papa", "2", "Traiguén", phone_hash="no-es-un-hash-valido")

    assert "no guardé" in response.lower()
    assert db.scalars(select(Parcela)).all() == []


def test_happy_path_persiste_con_ttl(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_parcelas(db, monkeypatch)

    response = register_parcela_for_llm(
        db,
        " Papa \n",
        "2.5",
        " Traiguén \n",
        phone_hash=_PHONE_HASH_A,
    )

    stored = db.scalar(select(Parcela))
    assert stored is not None
    assert stored.phone_hash == _PHONE_HASH_A
    assert stored.cultivo == "papa"
    assert stored.comuna == "traiguén"
    assert stored.superficie_ha == Decimal("2.50")
    assert stored.expires_at == datetime.datetime(2027, 7, 30, 12, 0)
    assert "2.50" in response
    assert "365 días" in response


@pytest.mark.parametrize(
    ("cultivo", "superficie", "comuna", "fragment"),
    [
        ("", "2", "Traiguén", "cultivo"),
        ("papa", "cero", "Traiguén", "superficie"),
        ("papa", "2", "", "comuna"),
    ],
)
def test_campos_invalidos_no_persisten(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    cultivo: str,
    superficie: str,
    comuna: str,
    fragment: str,
) -> None:
    _enable_parcelas(db, monkeypatch)

    response = register_parcela_for_llm(db, cultivo, superficie, comuna, phone_hash=_PHONE_HASH_A)

    assert fragment in response.lower()
    assert db.scalars(select(Parcela)).all() == []


def test_error_db_no_filtra_detalle(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_parcelas(db, monkeypatch)
    monkeypatch.setattr(db, "commit", MagicMock(side_effect=SQLAlchemyError("secreto-de-conexion")))

    response = register_parcela_for_llm(db, "papa", "2", "Traiguén", phone_hash=_PHONE_HASH_A)

    assert "problema al guardar" in response.lower()
    assert "secreto-de-conexion" not in response


class TestGetParcelasForLlm:
    """Listado de parcelas vigentes, aislado por sujeto y vencimiento."""

    def test_gate_apagado_no_lista(self, db: Session) -> None:
        assert "no está habilitado" in get_parcelas_for_llm(db, phone_hash=_PHONE_HASH_A).lower()

    def test_sin_consentimiento_no_lista(self, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
        _enable_parcelas(db, monkeypatch, consent=False)
        assert "consentimiento" in get_parcelas_for_llm(db, phone_hash=_PHONE_HASH_A).lower()

    def test_sin_parcelas_lo_dice(self, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
        _enable_parcelas(db, monkeypatch)
        assert "no tienes parcelas" in get_parcelas_for_llm(db, phone_hash=_PHONE_HASH_A).lower()

    def test_lista_solo_las_vigentes_del_sujeto(self, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
        """Aísla por sujeto y excluye parcelas ya vencidas."""
        _enable_parcelas(db, monkeypatch, phone_hash=_PHONE_HASH_A)
        db.add(UserPrefs(phone_hash=_PHONE_HASH_B, comuna="Victoria", parcela_consent=True))
        db.add_all(
            [
                Parcela(
                    phone_hash=_PHONE_HASH_A,
                    cultivo="papa",
                    superficie_ha=Decimal("2.50"),
                    comuna="traiguén",
                    expires_at=_NOW + datetime.timedelta(days=300),
                ),
                Parcela(
                    phone_hash=_PHONE_HASH_A,
                    cultivo="trigo",
                    superficie_ha=Decimal("5.00"),
                    comuna="traiguén",
                    expires_at=_NOW - datetime.timedelta(days=1),
                ),
                Parcela(
                    phone_hash=_PHONE_HASH_B,
                    cultivo="avena",
                    superficie_ha=Decimal("3.00"),
                    comuna="victoria",
                    expires_at=_NOW + datetime.timedelta(days=300),
                ),
            ]
        )
        db.commit()

        response = get_parcelas_for_llm(db, phone_hash=_PHONE_HASH_A)

        assert "1 parcela" in response
        assert "papa" in response
        assert "trigo" not in response
        assert "avena" not in response


class TestDeleteParcelasForSubject:
    """Borrado del sujeto, incluso con el gate apagado."""

    def test_borrado_funciona_aunque_gate_este_apagado(self, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
        _enable_parcelas(db, monkeypatch)
        register_parcela_for_llm(db, "papa", "2", "Traiguén", phone_hash=_PHONE_HASH_A)
        monkeypatch.setattr(settings, "parcela_tracking_enabled", False)

        deleted = delete_parcelas_for_subject(_PHONE_HASH_A, session=db)

        assert deleted == 1
        assert db.scalars(select(Parcela)).all() == []

    def test_hash_invalido_lanza_value_error(self) -> None:
        with pytest.raises(ValueError, match="phone_hash inválido"):
            delete_parcelas_for_subject("no-es-un-hash-valido")

    def test_borrado_reporta_error_operativo(self, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(db, "commit", MagicMock(side_effect=SQLAlchemyError("boom")))

        with pytest.raises(ParcelaOperationError):
            delete_parcelas_for_subject(_PHONE_HASH_A, session=db)


class TestPurgeExpiredParcelas:
    """Purga TTL independiente del gate de nuevas escrituras."""

    def test_purga_ttl_no_depende_del_gate(self, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
        _enable_parcelas(db, monkeypatch)
        db.add_all(
            [
                Parcela(
                    phone_hash=_PHONE_HASH_A,
                    cultivo="papa",
                    superficie_ha=Decimal("2.50"),
                    comuna="traiguén",
                    expires_at=_NOW - datetime.timedelta(days=1),
                ),
                Parcela(
                    phone_hash=_PHONE_HASH_A,
                    cultivo="trigo",
                    superficie_ha=Decimal("5.00"),
                    comuna="traiguén",
                    expires_at=_NOW + datetime.timedelta(days=300),
                ),
            ]
        )
        db.commit()
        monkeypatch.setattr(settings, "parcela_tracking_enabled", False)

        deleted = purge_expired_parcelas(session=db, now=_NOW)

        assert deleted == 1
        remaining = db.scalars(select(Parcela)).all()
        assert len(remaining) == 1
        assert remaining[0].cultivo == "trigo"

    def test_purga_reporta_error_operativo(self, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(db, "commit", MagicMock(side_effect=SQLAlchemyError("boom")))

        with pytest.raises(ParcelaOperationError):
            purge_expired_parcelas(session=db, now=_NOW)
