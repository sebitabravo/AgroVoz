"""Mide la extracción de gastos contra el umbral de #170 (>90% correcto).

La definición de hecho de #170 exige más de 90% de extracción correcta sobre
casos de prueba. Acá un caso es correcto cuando el resultado real coincide con
lo que un humano llamaría correcto: persistir el monto, producto, concepto y
fecha esperados, o rechazar sin escribir cuando la entrada es ambigua, futura
o está fuera del dominio de montos válidos.

El corpus refleja lo que la tool recibe en producción: argumentos ya elegidos
por el LLM a partir de la transcripción de Whisper, incluyendo los casos en que
el LLM no normaliza el monto y pasa el número hablado tal cual.
"""

import datetime
from dataclasses import dataclass

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.expense import Expense
from app.models.user_prefs import UserPrefs
from app.services.expense_service import register_expense_for_llm

_PHONE_HASH = "c" * 64
_TODAY = datetime.date(2026, 7, 29)
_YESTERDAY = datetime.date(2026, 7, 28)
_NOW = datetime.datetime(2026, 7, 29, 12, 0)

# Umbral contractual de #170. Bajarlo exige reabrir el issue, no editar el test.
_MIN_ACCURACY = 0.90


@dataclass(frozen=True)
class _Case:
    """Un caso de extracción con su resultado esperado."""

    utterance: str
    producto: str
    concepto: str
    monto: str
    fecha: str
    # None significa "debe rechazar sin persistir".
    expected: tuple[str, str, int, datetime.date] | None


def _persist(
    producto: str,
    concepto: str,
    amount_clp: int,
    occurred_on: datetime.date = _TODAY,
) -> tuple[str, str, int, datetime.date]:
    """Azúcar para declarar el resultado esperado de un caso que sí guarda."""
    return (producto, concepto, amount_clp, occurred_on)


_CORPUS: tuple[_Case, ...] = (
    # ── Formatos numéricos chilenos ──
    _Case(
        "compré semilla de papa, cincuenta mil", "papa", "semilla", "$50.000", "", _persist("papa", "semilla", 50_000)
    ),
    _Case("gasté 50 lucas en semilla", "papa", "semilla", "50 lucas", "", _persist("papa", "semilla", 50_000)),
    _Case("fueron 50000 pesos", "papa", "semilla", "50000", "", _persist("papa", "semilla", 50_000)),
    _Case("doce mil quinientos de flete", "trigo", "flete", "$12.500", "", _persist("trigo", "flete", 12_500)),
    _Case("siete mil quinientos en petróleo", "trigo", "petroleo", "7.500", "", _persist("trigo", "petroleo", 7_500)),
    _Case(
        "un millón doscientos cincuenta mil de arriendo",
        "general",
        "arriendo maquinaria",
        "1.250.000",
        "",
        _persist("general", "arriendo maquinaria", 1_250_000),
    ),
    _Case(
        "veinticinco mil en fertilizante",
        "avena",
        "fertilizante",
        "25 mil",
        "",
        _persist("avena", "fertilizante", 25_000),
    ),
    _Case("tres lucas de bencina", "general", "bencina", "3 lucas", "", _persist("general", "bencina", 3_000)),
    _Case("media luca de cordel", "general", "cordel", "0,5 lucas", "", _persist("general", "cordel", 500)),
    _Case("cincuenta lucas y media", "papa", "semilla", "50,5 lucas", "", _persist("papa", "semilla", 50_500)),
    _Case("ocho mil novecientos noventa", "tomate", "malla", "8990 pesos", "", _persist("tomate", "malla", 8_990)),
    _Case("ciento veinte mil de abono", "papa", "abono", "$120.000", "", _persist("papa", "abono", 120_000)),
    _Case("cuarenta y cinco mil", "trigo", "semilla", "45,000", "", _persist("trigo", "semilla", 45_000)),
    _Case("dos mil de clavos", "general", "clavos", "2.000", "", _persist("general", "clavos", 2_000)),
    # ── Números hablados sin normalizar por el LLM ──
    _Case(
        "gasté treinta mil en fertilizante",
        "avena",
        "fertilizante",
        "treinta mil",
        "",
        _persist("avena", "fertilizante", 30_000),
    ),
    _Case("veinte lucas de flete", "papa", "flete", "veinte lucas", "", _persist("papa", "flete", 20_000)),
    _Case(
        "cien mil de arriendo", "general", "arriendo", "cien mil pesos", "", _persist("general", "arriendo", 100_000)
    ),
    _Case(
        "treinta y cinco mil de mano de obra",
        "trigo",
        "mano de obra",
        "treinta y cinco mil",
        "",
        _persist("trigo", "mano de obra", 35_000),
    ),
    _Case(
        "doscientos mil en maquinaria",
        "general",
        "maquinaria",
        "doscientos mil",
        "",
        _persist("general", "maquinaria", 200_000),
    ),
    _Case("quince lucas de riego", "tomate", "riego", "quince lucas", "", _persist("tomate", "riego", 15_000)),
    _Case(
        "veinte mil quinientos de semilla",
        "papa",
        "semilla",
        "veinte mil quinientos",
        "",
        _persist("papa", "semilla", 20_500),
    ),
    _Case("mil quinientos de cordel", "general", "cordel", "mil quinientos", "", _persist("general", "cordel", 1_500)),
    # ── Fechas ──
    _Case(
        "ayer compré la semilla", "papa", "semilla", "$50.000", "ayer", _persist("papa", "semilla", 50_000, _YESTERDAY)
    ),
    _Case(
        "el veinte de julio",
        "papa",
        "semilla",
        "$50.000",
        "2026-07-20",
        _persist("papa", "semilla", 50_000, datetime.date(2026, 7, 20)),
    ),
    _Case(
        "el 20/07",
        "papa",
        "semilla",
        "$50.000",
        "20/07/2026",
        _persist("papa", "semilla", 50_000, datetime.date(2026, 7, 20)),
    ),
    _Case(
        "el 20-07",
        "papa",
        "semilla",
        "$50.000",
        "20-07-2026",
        _persist("papa", "semilla", 50_000, datetime.date(2026, 7, 20)),
    ),
    _Case("hoy compré abono", "papa", "abono", "$30.000", "hoy", _persist("papa", "abono", 30_000)),
    # ── Normalización de texto ──
    _Case(
        "semilla nueva para la papa",
        " Papa \n",
        "  Semilla\tnueva ",
        "$50.000",
        "",
        _persist("papa", "semilla nueva", 50_000),
    ),
    # ── Rechazos esperados ──
    _Case("compré semilla, no me acuerdo cuánto", "papa", "semilla", "", "", None),
    _Case("gasté harto", "papa", "semilla", "harto", "", None),
    _Case("dijo la cantidad, no el monto", "papa", "semilla", "dos sacos", "", None),
    _Case("frase con dos multiplicadores", "papa", "semilla", "dos mil lucas", "", None),
    _Case("no gasté nada", "papa", "semilla", "0", "", None),
    _Case("monto negativo por error del LLM", "papa", "semilla", "-5000", "", None),
    _Case("monto absurdo fuera de dominio", "papa", "semilla", "1000000001", "", None),
    _Case("mañana voy a comprar", "papa", "semilla", "$50.000", "30/07/2026", None),
    _Case("fecha que el LLM no supo mapear", "papa", "semilla", "$50.000", "fecha rara", None),
    _Case("no dijo para qué producto", "", "semilla", "$50.000", "", None),
    _Case("no dijo en qué gastó", "papa", "", "$50.000", "", None),
)


@pytest.fixture
def _expenses_enabled(db: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    """Habilita el gate, fija el reloj y crea la identidad consentida."""
    monkeypatch.setattr(settings, "expense_tracking_enabled", True)
    monkeypatch.setattr(settings, "expense_retention_days", 180)
    monkeypatch.setattr("app.services.expense_service._today_santiago", lambda: _TODAY)
    monkeypatch.setattr("app.services.expense_service._utcnow_naive", lambda: _NOW)
    db.add(UserPrefs(phone_hash=_PHONE_HASH, comuna="Traiguén", expense_consent=True))
    db.commit()
    return db


def _run_case(db: Session, case: _Case) -> tuple[str, str, int, datetime.date] | None:
    """Ejecuta un caso aislado y devuelve lo persistido, o None si rechazó."""
    db.execute(delete(Expense))
    db.commit()

    register_expense_for_llm(
        db,
        producto=case.producto,
        concepto=case.concepto,
        monto=case.monto,
        phone_hash=_PHONE_HASH,
        fecha=case.fecha,
    )

    stored = db.scalar(select(Expense))
    if stored is None:
        return None
    return (stored.producto, stored.concepto, stored.amount_clp, stored.occurred_on)


def test_extraccion_supera_el_umbral_de_170(_expenses_enabled: Session) -> None:
    """La tasa de extracción correcta debe superar el 90% exigido por #170."""
    db = _expenses_enabled
    misses: list[str] = []

    for case in _CORPUS:
        actual = _run_case(db, case)
        if actual != case.expected:
            misses.append(f"{case.utterance!r}: esperado={case.expected} actual={actual}")

    accuracy = (len(_CORPUS) - len(misses)) / len(_CORPUS)
    detalle = "\n".join(misses) or "sin fallos"
    assert accuracy > _MIN_ACCURACY, (
        f"Extracción {accuracy:.1%} sobre {len(_CORPUS)} casos, bajo el umbral "
        f"de #170 ({_MIN_ACCURACY:.0%}).\n{detalle}"
    )


def test_corpus_cubre_persistencia_y_rechazo() -> None:
    """Un corpus solo de rechazos o solo de aciertos no mide extracción."""
    persistidos = sum(1 for case in _CORPUS if case.expected is not None)
    rechazos = len(_CORPUS) - persistidos

    assert persistidos >= 20, "El corpus necesita casos suficientes que sí extraen"
    assert rechazos >= 5, "El corpus necesita casos adversos que deben rechazarse"
