"""Steps BDD para el registro de gastos por voz (#170).

Ejercita las mismas funciones que test_expense_service.py, pero expresadas
como escenarios de aceptación en español: la forma en que el equipo (no solo
quien programa) puede leer y validar las reglas de negocio del feature.
"""

from __future__ import annotations

import datetime
import hashlib

import pytest
from pytest_bdd import given, parsers, scenarios, then, when
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.expense import Expense
from app.models.user_prefs import UserPrefs
from app.services.expense_service import (
    delete_expenses_for_subject,
    register_expense_for_llm,
)

scenarios("../features/registro_gastos.feature")

_TODAY = datetime.date(2026, 7, 29)
_NOW = datetime.datetime(2026, 7, 29, 12, 0)


def _a_hash(etiqueta: str) -> str:
    """Deriva un phone_hash válido (64 hex minúscula) de una etiqueta legible.

    Los escenarios Gherkin nombran productores como "traiguen-01" para que se
    lean bien; la app exige el mismo formato HMAC-SHA256 que produce
    phone_hash.py. sha256 simple alcanza para el test: no valida el HMAC en
    sí, solo necesita el formato correcto para no chocar con validate_phone_hash.
    """
    return hashlib.sha256(etiqueta.encode()).hexdigest()


@pytest.fixture
def contexto() -> dict[str, object]:
    """Guarda la última respuesta del sistema entre steps Given/When/Then."""
    return {}


@given("que el registro de gastos está habilitado")
def _habilitar_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "expense_tracking_enabled", True)
    monkeypatch.setattr(settings, "expense_retention_days", 180)
    monkeypatch.setattr("app.services.expense_service._today_santiago", lambda: _TODAY)
    monkeypatch.setattr("app.services.expense_service._utcnow_naive", lambda: _NOW)


@given(parsers.parse('el productor "{phone_hash}" tiene comuna registrada'))
def _crear_productor(phone_hash: str, db: Session) -> None:
    phone_hash_completo = _a_hash(phone_hash)
    db.add(UserPrefs(phone_hash=phone_hash_completo, comuna="Traiguén"))
    db.commit()


@given(parsers.parse('que "{phone_hash}" no ha dado consentimiento de gastos'))
def _sin_consentimiento(phone_hash: str, db: Session) -> None:
    phone_hash_completo = _a_hash(phone_hash)
    prefs = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash_completo))
    assert prefs is not None
    prefs.expense_consent = False
    db.commit()


@given(parsers.parse('que "{phone_hash}" dio consentimiento de gastos'))
def _con_consentimiento(phone_hash: str, db: Session) -> None:
    phone_hash_completo = _a_hash(phone_hash)
    prefs = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash_completo))
    assert prefs is not None
    prefs.expense_consent = True
    db.commit()


@given(parsers.parse('"{phone_hash}" registró un gasto de "{monto}" en "{concepto}" para "{producto}"'))
def _registrar_gasto_previo(
    phone_hash: str,
    monto: str,
    concepto: str,
    producto: str,
    db: Session,
) -> None:
    phone_hash_completo = _a_hash(phone_hash)
    register_expense_for_llm(db, producto=producto, concepto=concepto, monto=monto, phone_hash=phone_hash_completo)


@when(
    parsers.parse('"{phone_hash}" pide registrar un gasto de "{monto}" en "{concepto}" para "{producto}"'),
    target_fixture="contexto",
)
def _pedir_registro(
    phone_hash: str,
    monto: str,
    concepto: str,
    producto: str,
    db: Session,
    contexto: dict[str, object],
) -> dict[str, object]:
    phone_hash_completo = _a_hash(phone_hash)
    respuesta = register_expense_for_llm(
        db, producto=producto, concepto=concepto, monto=monto, phone_hash=phone_hash_completo
    )
    contexto["respuesta"] = respuesta
    contexto["phone_hash"] = phone_hash_completo
    contexto["producto"] = producto
    return contexto


@when(parsers.parse('"{phone_hash}" revoca el consentimiento de gastos'), target_fixture="contexto")
def _revocar_consentimiento(phone_hash: str, db: Session, contexto: dict[str, object]) -> dict[str, object]:
    phone_hash_completo = _a_hash(phone_hash)
    prefs = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash_completo))
    assert prefs is not None
    prefs.expense_consent = False
    db.commit()
    delete_expenses_for_subject(phone_hash_completo, session=db)
    contexto["phone_hash"] = phone_hash_completo
    return contexto


@then("el sistema responde que no guardó el gasto")
def _valida_rechazo(contexto: dict[str, object]) -> None:
    assert "no guardé" in str(contexto["respuesta"]).lower()


@then(parsers.parse('no queda ningún gasto persistido para "{phone_hash}"'))
def _valida_sin_gastos(phone_hash: str, db: Session) -> None:
    phone_hash_completo = _a_hash(phone_hash)
    gastos = db.scalars(select(Expense).where(Expense.phone_hash == phone_hash_completo)).all()
    assert gastos == []


@then(parsers.parse('el gasto queda persistido con monto "{monto}" para "{producto}"'))
def _valida_persistencia(monto: str, producto: str, db: Session, contexto: dict[str, object]) -> None:
    gasto = db.scalar(select(Expense).where(Expense.phone_hash == contexto["phone_hash"]))
    assert gasto is not None
    assert gasto.amount_clp == int(monto)
    assert gasto.producto == producto.lower()


@then("el gasto vence según los días de retención configurados")
def _valida_ttl(db: Session, contexto: dict[str, object]) -> None:
    gasto = db.scalar(select(Expense).where(Expense.phone_hash == contexto["phone_hash"]))
    assert gasto is not None
    dias_restantes = (gasto.expires_at - _NOW).days
    assert dias_restantes == settings.expense_retention_days
