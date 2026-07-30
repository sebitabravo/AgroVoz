"""Regresiones de gastos consentidos, TTL, borrado y formato chileno."""

import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.expense import Expense
from app.models.user_prefs import UserPrefs
from app.services.expense_service import (
    ExpenseOperationError,
    _parse_amount_clp,
    delete_expenses_for_subject,
    get_active_expense_total,
    purge_expired_expenses,
    register_expense_for_llm,
)

_PHONE_HASH_A = "a" * 64
_PHONE_HASH_B = "b" * 64
_TODAY = datetime.date(2026, 7, 29)
_NOW = datetime.datetime(2026, 7, 29, 12, 0)


def _enable_expenses(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    *,
    phone_hash: str = _PHONE_HASH_A,
    consent: bool = True,
) -> None:
    """Habilita gate y crea una identidad con consentimiento explícito."""
    monkeypatch.setattr(settings, "expense_tracking_enabled", True)
    monkeypatch.setattr(settings, "expense_retention_days", 180)
    monkeypatch.setattr(
        "app.services.expense_service._today_santiago",
        lambda: _TODAY,
    )
    monkeypatch.setattr(
        "app.services.expense_service._utcnow_naive",
        lambda: _NOW,
    )
    db.add(
        UserPrefs(
            phone_hash=phone_hash,
            comuna="Traiguén",
            expense_consent=consent,
        )
    )
    db.commit()


@pytest.mark.parametrize(
    ("raw_amount", "expected"),
    [
        ("50000", 50_000),
        ("$50.000", 50_000),
        ("50,000 pesos", 50_000),
        ("50 lucas", 50_000),
        ("50,5 lucas", 50_500),
        ("1.250.000", 1_250_000),
        ("1.250,4", 1_250),
    ],
)
def test_parse_amount_clp_formatos_chilenos(
    raw_amount: str,
    expected: int,
) -> None:
    """Normaliza miles/lucas y redondea centavos inexistentes."""
    assert _parse_amount_clp(raw_amount) == expected


@pytest.mark.parametrize(
    ("raw_amount", "expected"),
    [
        ("treinta mil", 30_000),
        ("veinte lucas", 20_000),
        ("cien mil pesos", 100_000),
        ("treinta y cinco mil", 35_000),
        # El multiplicador solo afecta lo dicho antes de él.
        ("veinte mil quinientos", 20_500),
        ("mil quinientos", 1_500),
        ("mil", 1_000),
        ("veinticinco lucas", 25_000),
    ],
)
def test_parse_amount_clp_numeros_hablados(raw_amount: str, expected: int) -> None:
    """Interpreta lo que Whisper transcribe en palabras cuando el LLM no normaliza."""
    assert _parse_amount_clp(raw_amount) == expected


@pytest.mark.parametrize(
    "raw_amount",
    [
        "",
        "cincuenta",
        "0",
        "-1000",
        "1000000001",
        # Sin multiplicador, una palabra numérica es cantidad, no pesos.
        "dos sacos",
        # Dos multiplicadores no tienen lectura única.
        "dos mil lucas",
        # Un token desconocido vuelve ambigua la frase completa.
        "como treinta y tantos mil",
    ],
)
def test_parse_amount_clp_rechaza_valores_inseguros(raw_amount: str) -> None:
    """No acepta montos ausentes, no numéricos, ambiguos o fuera del dominio."""
    assert _parse_amount_clp(raw_amount) is None


def test_gate_apagado_no_guarda(db: Session) -> None:
    """El default fail-closed no crea registros financieros."""
    response = register_expense_for_llm(
        db,
        producto="papa",
        concepto="semilla",
        monto="50000",
        phone_hash=_PHONE_HASH_A,
    )

    assert "no guardé" in response.lower()
    assert db.scalars(select(Expense)).all() == []


def test_sin_consentimiento_no_guarda(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un gate global nunca reemplaza el opt-in del agricultor."""
    _enable_expenses(db, monkeypatch, consent=False)

    response = register_expense_for_llm(
        db,
        producto="papa",
        concepto="semilla",
        monto="50000",
        phone_hash=_PHONE_HASH_A,
    )

    assert "consentimiento" in response.lower()
    assert "no guardé" in response.lower()
    assert db.scalars(select(Expense)).all() == []


def test_happy_path_persiste_minimo_con_ttl(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persiste solo campos mínimos y fija vencimiento por fila."""
    _enable_expenses(db, monkeypatch)

    response = register_expense_for_llm(
        db,
        producto=" Papa \n",
        concepto="  Semilla\tnueva ",
        monto="$50.000",
        phone_hash=_PHONE_HASH_A,
        fecha="ayer",
    )

    stored = db.scalar(select(Expense))
    assert stored is not None
    assert stored.phone_hash == _PHONE_HASH_A
    assert stored.producto == "papa"
    assert stored.concepto == "semilla nueva"
    assert stored.amount_clp == 50_000
    assert stored.occurred_on == datetime.date(2026, 7, 28)
    assert stored.expires_at == datetime.datetime(2027, 1, 25, 12, 0)
    assert "50.000" in response
    assert "28/07/2026" in response
    assert "180 días" in response


@pytest.mark.parametrize(
    ("fecha", "fragment"),
    [
        ("30/07/2026", "futuro"),
        ("fecha rara", "fecha"),
    ],
)
def test_fecha_invalida_no_persiste(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    fecha: str,
    fragment: str,
) -> None:
    """Fechas futuras o no reconocidas fallan sin escribir."""
    _enable_expenses(db, monkeypatch)

    response = register_expense_for_llm(
        db,
        producto="papa",
        concepto="semilla",
        monto="50000",
        phone_hash=_PHONE_HASH_A,
        fecha=fecha,
    )

    assert fragment in response.lower()
    assert db.scalars(select(Expense)).all() == []


def test_totales_aislan_identidad_producto_y_vencimiento(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La suma solo incluye filas vigentes del sujeto/producto consultado."""
    _enable_expenses(db, monkeypatch, phone_hash=_PHONE_HASH_A)
    db.add(
        UserPrefs(
            phone_hash=_PHONE_HASH_B,
            comuna="Traiguén",
            expense_consent=True,
        )
    )
    db.add_all(
        [
            Expense(
                phone_hash=_PHONE_HASH_A,
                producto="papa",
                concepto="semilla",
                amount_clp=50_000,
                occurred_on=_TODAY,
                expires_at=_NOW + datetime.timedelta(days=10),
            ),
            Expense(
                phone_hash=_PHONE_HASH_A,
                producto="trigo",
                concepto="flete",
                amount_clp=40_000,
                occurred_on=_TODAY,
                expires_at=_NOW + datetime.timedelta(days=10),
            ),
            Expense(
                phone_hash=_PHONE_HASH_A,
                producto="papa",
                concepto="abono",
                amount_clp=30_000,
                occurred_on=_TODAY,
                expires_at=_NOW,
            ),
            Expense(
                phone_hash=_PHONE_HASH_B,
                producto="papa",
                concepto="semilla",
                amount_clp=90_000,
                occurred_on=_TODAY,
                expires_at=_NOW + datetime.timedelta(days=10),
            ),
        ]
    )
    db.commit()

    total = get_active_expense_total(
        db,
        _PHONE_HASH_A,
        "PAPA",
        as_of=_TODAY,
        now=_NOW,
    )

    assert total == 50_000


def test_borrado_funciona_aunque_gate_este_apagado(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Desactivar nuevas escrituras nunca bloquea el derecho de supresión."""
    _enable_expenses(db, monkeypatch)
    db.add(
        Expense(
            phone_hash=_PHONE_HASH_A,
            producto="papa",
            concepto="semilla",
            amount_clp=50_000,
            occurred_on=_TODAY,
            expires_at=_NOW + datetime.timedelta(days=10),
        )
    )
    db.commit()
    monkeypatch.setattr(settings, "expense_tracking_enabled", False)

    deleted = delete_expenses_for_subject(_PHONE_HASH_A, session=db)

    assert deleted == 1
    assert db.scalars(select(Expense)).all() == []


def test_purga_ttl_no_depende_del_gate(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El job elimina expirados aunque la feature esté cerrada."""
    _enable_expenses(db, monkeypatch)
    db.add_all(
        [
            Expense(
                phone_hash=_PHONE_HASH_A,
                producto="papa",
                concepto="semilla",
                amount_clp=10_000,
                occurred_on=_TODAY,
                expires_at=_NOW,
            ),
            Expense(
                phone_hash=_PHONE_HASH_A,
                producto="papa",
                concepto="flete",
                amount_clp=20_000,
                occurred_on=_TODAY,
                expires_at=_NOW + datetime.timedelta(seconds=1),
            ),
        ]
    )
    db.commit()
    monkeypatch.setattr(settings, "expense_tracking_enabled", False)

    deleted = purge_expired_expenses(session=db, now=_NOW)

    assert deleted == 1
    remaining = db.scalars(select(Expense)).all()
    assert [expense.amount_clp for expense in remaining] == [20_000]


def test_error_db_no_filtra_detalle(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Un fallo de commit responde amigable y sanea el log."""
    session = MagicMock(spec=Session)
    session.commit.side_effect = SQLAlchemyError(
        "INSERT amount=50000 phone=secret"
    )
    monkeypatch.setattr(settings, "expense_tracking_enabled", True)
    monkeypatch.setattr(
        "app.services.expense_service._has_expense_consent",
        lambda *_args: True,
    )
    caplog.set_level("ERROR", logger="app.services.expense_service")

    response = register_expense_for_llm(
        session,
        producto="papa",
        concepto="semilla",
        monto="50000",
        phone_hash=_PHONE_HASH_A,
    )

    assert "problema" in response.lower()
    assert "50000" not in caplog.text
    assert "secret" not in caplog.text
    session.rollback.assert_called_once()


def test_borrado_reporta_error_operativo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La capa superior puede reintentar si SQLite no confirmó el borrado."""
    session = MagicMock(spec=Session)
    session.execute.side_effect = SQLAlchemyError("db offline")

    with pytest.raises(ExpenseOperationError):
        delete_expenses_for_subject(_PHONE_HASH_A, session=session)

    session.rollback.assert_called_once()
