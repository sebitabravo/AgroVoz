"""Regresiones de gastos consentidos, TTL, borrado y formato chileno."""

import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.expense import Expense
from app.models.user_prefs import UserPrefs
from app.services.expense_service import (
    _MAX_EXPENSE_CLP,
    ExpenseOperationError,
    _finalize_amount,
    _parse_amount_clp,
    _parse_expense_date,
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


class TestFinalizeAmount:
    """Regresión directa de _finalize_amount: redondeo y límites del dominio.

    Encontrado por mutation testing: sin un caso de mitad exacta, remover
    ``rounding=ROUND_HALF_UP`` no rompía ningún test; sin probar los bordes
    exactos 1 y _MAX_EXPENSE_CLP, ``<= 0``/``<= 1`` y ``> límite``/``>= límite``
    eran indistinguibles.
    """

    def test_mitad_exacta_redondea_hacia_arriba(self) -> None:
        assert _finalize_amount(Decimal("1200.5")) == 1201

    def test_un_peso_es_el_minimo_valido(self) -> None:
        assert _finalize_amount(Decimal("1")) == 1

    def test_cero_es_invalido(self) -> None:
        assert _finalize_amount(Decimal("0")) is None

    def test_el_limite_maximo_es_valido(self) -> None:
        assert _finalize_amount(Decimal(_MAX_EXPENSE_CLP)) == _MAX_EXPENSE_CLP

    def test_sobre_el_limite_maximo_es_invalido(self) -> None:
        assert _finalize_amount(Decimal(_MAX_EXPENSE_CLP + 1)) is None


class TestParseExpenseDate:
    """Regresión directa de _parse_expense_date, sin diluirse en el promedio
    tolerante del corpus de extracción (test_expense_extraction_accuracy.py).

    Encontrado por mutation testing (mutmut): 15 mutantes sobrevivían en esta
    función porque el corpus de precisión tolera hasta 10% de fallos — un caso
    de fecha roto no baja el promedio lo suficiente para que ese test falle.
    """

    @pytest.fixture(autouse=True)
    def _fijar_hoy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.services.expense_service._today_santiago", lambda: _TODAY)

    def test_vacio_es_hoy(self) -> None:
        assert _parse_expense_date("") == _TODAY

    def test_hoy_es_hoy(self) -> None:
        assert _parse_expense_date("hoy") == _TODAY

    def test_hoy_no_distingue_mayusculas(self) -> None:
        assert _parse_expense_date("HOY") == _TODAY

    def test_ayer_es_un_dia_antes(self) -> None:
        assert _parse_expense_date("ayer") == _TODAY - datetime.timedelta(days=1)

    def test_formato_iso(self) -> None:
        assert _parse_expense_date("2026-07-20") == datetime.date(2026, 7, 20)

    def test_formato_dd_mm_aaaa_con_barra(self) -> None:
        assert _parse_expense_date("20/07/2026") == datetime.date(2026, 7, 20)

    def test_formato_dd_mm_aaaa_con_guion(self) -> None:
        """Solo coincide con el tercer formato de la lista: si un mutante
        detiene la búsqueda en el primer intento fallido en vez de probar el
        siguiente, esta fecha deja de reconocerse."""
        assert _parse_expense_date("20-07-2026") == datetime.date(2026, 7, 20)

    def test_hoy_explicito_por_formato_no_se_rechaza_como_futuro(self) -> None:
        """La fecha de hoy escrita en ISO debe aceptarse: no es futuro."""
        assert _parse_expense_date("2026-07-29") == _TODAY

    def test_manana_se_rechaza_como_futuro(self) -> None:
        assert _parse_expense_date("2026-07-30") is None

    def test_fecha_no_reconocida_retorna_none(self) -> None:
        assert _parse_expense_date("fecha rara") is None


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


class TestGetActiveExpenseTotalGates:
    """Regresión directa de get_active_expense_total, sin diluirse en el
    happy path de test_totales_aislan_identidad_producto_y_vencimiento.

    Encontrado por mutation testing: la condición de gate encadenaba tres
    ``or``, y un mutante que la cambiaba a ``and`` seguía pasando porque
    ningún test aislaba el gate/consentimiento del resto de la query.
    """

    def test_retorna_cero_sin_expenses_del_sujeto(self, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sin filas para el sujeto/producto, el total es exactamente 0, no None ni 1."""
        _enable_expenses(db, monkeypatch, phone_hash=_PHONE_HASH_A)

        total = get_active_expense_total(db, _PHONE_HASH_A, "papa", as_of=_TODAY, now=_NOW)

        assert total == 0

    def test_retorna_cero_con_gate_apagado_aunque_haya_expenses(
        self,
        db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Apagar el gate bloquea la lectura aunque consentimiento y hash sean válidos."""
        _enable_expenses(db, monkeypatch, phone_hash=_PHONE_HASH_A)
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

        total = get_active_expense_total(db, _PHONE_HASH_A, "papa", as_of=_TODAY, now=_NOW)

        assert total == 0

    def test_retorna_cero_sin_consentimiento_aunque_gate_este_prendido(
        self,
        db: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Revocar el consentimiento bloquea la lectura con gate prendido y hash válido.

        Distingue el AND del OR en la condición de gate: un mutante que
        cambia ``or`` por ``and`` solo falla si gate y consentimiento se
        prueban por separado.
        """
        _enable_expenses(db, monkeypatch, phone_hash=_PHONE_HASH_A, consent=False)
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

        total = get_active_expense_total(db, _PHONE_HASH_A, "papa", as_of=_TODAY, now=_NOW)

        assert total == 0

    def test_retorna_cero_con_hash_invalido(self, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
        """Un phone_hash mal formado nunca debe llegar a consultar la tabla."""
        _enable_expenses(db, monkeypatch, phone_hash=_PHONE_HASH_A)

        total = get_active_expense_total(db, "no-es-un-hash-valido", "papa", as_of=_TODAY, now=_NOW)

        assert total == 0

    def test_excluye_gastos_posteriores_a_as_of(self, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
        """Un gasto ocurrido después de la fecha de referencia no debe sumarse.

        Sin este filtro, un gasto futuro se contaría contra un margen de una
        venta pasada, inflando el descuento sin relación con esa venta.
        """
        _enable_expenses(db, monkeypatch, phone_hash=_PHONE_HASH_A)
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
                    producto="papa",
                    concepto="fertilizante",
                    amount_clp=20_000,
                    occurred_on=_TODAY + datetime.timedelta(days=5),
                    expires_at=_NOW + datetime.timedelta(days=10),
                ),
            ]
        )
        db.commit()

        total = get_active_expense_total(db, _PHONE_HASH_A, "papa", as_of=_TODAY, now=_NOW)

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
    session.commit.side_effect = SQLAlchemyError("INSERT amount=50000 phone=secret")
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
