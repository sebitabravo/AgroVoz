"""Registro consentido de gastos agrícolas con TTL y borrado (#170).

La feature permanece apagada por defecto. Cuando se habilite, exige además
``expense_consent=True`` para la identidad seudonimizada. Nunca persiste el
número de WhatsApp, texto libre completo ni centavos.
"""

from __future__ import annotations

import datetime
import logging
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Result, delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.phone_hash import validate_phone_hash
from app.models.expense import Expense
from app.models.user_prefs import UserPrefs

logger = logging.getLogger(__name__)

_SANTIAGO = ZoneInfo("America/Santiago")
_AMOUNT_TOKEN = re.compile(r"\d[\d.,]*")
_THOUSANDS_GROUPING = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")
_MULTIPLIER_WORDS = re.compile(r"\b(?:luca|lucas|mil)\b", re.IGNORECASE)
_MAX_EXPENSE_CLP = 1_000_000_000

# Números hablados sin tilde: Whisper transcribe "treinta mil" en palabras y el
# LLM no siempre los normaliza a dígitos antes de llamar la tool.
_SPOKEN_NUMBERS: dict[str, int] = {
    "un": 1,
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
    "veintiun": 21,
    "veintiuno": 21,
    "veintidos": 22,
    "veintitres": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "veintiseis": 26,
    "veintisiete": 27,
    "veintiocho": 28,
    "veintinueve": 29,
    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
    "sesenta": 60,
    "setenta": 70,
    "ochenta": 80,
    "noventa": 90,
    "cien": 100,
    "ciento": 100,
    "doscientos": 200,
    "trescientos": 300,
    "cuatrocientos": 400,
    "quinientos": 500,
    "seiscientos": 600,
    "setecientos": 700,
    "ochocientos": 800,
    "novecientos": 900,
}
# Tokens que acompañan al número sin aportar valor propio.
_SPOKEN_FILLER = frozenset({"y", "de", "como", "unos", "unas", "pesos", "peso"})


class ExpenseOperationError(RuntimeError):
    """La operación no pudo confirmarse en SQLite."""


def _affected_rows(result: Result[Any]) -> int:
    """Lee ``rowcount`` de un DELETE sin depender del tipo concreto del driver.

    ``Session.execute`` está tipado como ``Result``; solo el ``CursorResult`` que
    devuelven las sentencias DML expone ``rowcount``.
    """
    return int(getattr(result, "rowcount", 0) or 0)


def _today_santiago() -> datetime.date:
    """Entrega la fecha civil del productor en Chile."""
    return datetime.datetime.now(_SANTIAGO).date()


def _utcnow_naive() -> datetime.datetime:
    """Entrega UTC sin tzinfo, formato estable para SQLite."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _finalize_amount(amount: Decimal) -> int | None:
    """Redondea a peso entero y aplica el dominio válido de montos."""
    rounded = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if rounded <= 0 or rounded > _MAX_EXPENSE_CLP:
        return None
    return int(rounded)


def _strip_accents(text: str) -> str:
    """Quita tildes para comparar contra el vocabulario de números hablados."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _sum_spoken_group(tokens: list[str]) -> int | None:
    """Suma un grupo hablado de hasta tres cifras ("treinta y cinco", "cien")."""
    total = 0
    for token in tokens:
        if token in _SPOKEN_FILLER:
            continue
        value = _SPOKEN_NUMBERS.get(token)
        if value is None:
            # Un token desconocido vuelve ambigua la frase completa.
            return None
        total += value
    return total


def _parse_spoken_amount(text: str) -> Decimal | None:
    """Interpreta un monto hablado con multiplicador ("veinte mil quinientos").

    El multiplicador solo afecta a lo dicho antes de él: "veinte mil
    quinientos" son 20.500 y no 520.000. Solo se invoca cuando la frase trae
    "mil" o "lucas"; sin multiplicador, leer palabras sueltas confundiría
    cantidades de sacos con pesos.
    """
    tokens = [token for token in re.split(r"[\s-]+", _strip_accents(text)) if token]
    multiplier_positions = [index for index, token in enumerate(tokens) if _MULTIPLIER_WORDS.fullmatch(token)]
    if len(multiplier_positions) != 1:
        # "dos mil lucas" y similares no tienen lectura única.
        return None

    split_at = multiplier_positions[0]
    head = _sum_spoken_group(tokens[:split_at])
    tail = _sum_spoken_group(tokens[split_at + 1 :])
    if head is None or tail is None:
        return None

    # "mil quinientos" omite el uno: sin cabeza explícita el multiplicador vale 1.
    total = (head or 1) * 1000 + tail
    if total <= 0:
        return None
    return Decimal(total)


def _parse_amount_clp(raw_amount: str) -> int | None:
    """Convierte pesos escritos en formato chileno a un entero sin centavos.

    Acepta ejemplos como ``$50.000``, ``50,000``, ``50 lucas``, ``50,5 lucas``
    y números hablados con multiplicador como ``treinta mil`` o ``veinte
    lucas``. Rechaza cero, negativos, montos ausentes y valores sobre el
    límite técnico para evitar registros accidentales abusivos.

    Limitación conocida: si la frase mezcla dígitos y palabras ("20 mil
    quinientos"), gana el dígito y se pierde la cola hablada (20.000). El LLM
    normaliza el monto en la mayoría de los casos; documentado en #170.
    """
    text = str(raw_amount).strip().lower()
    if re.search(r"-\s*\d", text):
        return None
    multiplier = Decimal("1000") if _MULTIPLIER_WORDS.search(text) else Decimal("1")
    token_match = _AMOUNT_TOKEN.search(text)
    if token_match is None:
        spoken = _parse_spoken_amount(text) if multiplier != Decimal("1") else None
        if spoken is None:
            return None
        return _finalize_amount(spoken)

    token = token_match.group(0)

    if _THOUSANDS_GROUPING.fullmatch(token):
        normalized = token.replace(".", "").replace(",", "")
    elif "." in token and "," in token:
        # Formato chileno típico: punto de miles y coma decimal.
        normalized = token.replace(".", "").replace(",", ".")
    elif "," in token:
        normalized = token.replace(",", ".")
    else:
        normalized = token

    try:
        return _finalize_amount(Decimal(normalized) * multiplier)
    except InvalidOperation:
        return None


def _normalize_required_text(value: str, *, max_length: int) -> str | None:
    """Colapsa whitespace/control y aplica un tope antes de persistir."""
    cleaned = " ".join(str(value).split()).strip().lower()
    if not cleaned or len(cleaned) > max_length:
        return None
    return cleaned


def _parse_expense_date(raw_date: str) -> datetime.date | None:
    """Interpreta fecha ISO/chilena, ``hoy`` o ``ayer`` sin aceptar futuro."""
    today = _today_santiago()
    text = str(raw_date).strip().lower()
    if not text or text == "hoy":
        return today
    if text == "ayer":
        return today - datetime.timedelta(days=1)

    parsed: datetime.date | None = None
    for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            parsed = datetime.datetime.strptime(text, date_format).date()
            break
        except ValueError:
            continue
    if parsed is None or parsed > today:
        return None
    return parsed


def _has_expense_consent(session: Session, phone_hash: str) -> bool:
    """Comprueba el opt-in específico sin cargar otros datos personales."""
    return session.scalar(select(UserPrefs.expense_consent).where(UserPrefs.phone_hash == phone_hash)) is True


def register_expense_for_llm(
    session: Session,
    producto: str,
    concepto: str,
    monto: str,
    phone_hash: str = "",
    fecha: str = "",
) -> str:
    """Registra un gasto solo con gate, identidad válida y consentimiento.

    Args:
        session: Sesión SQLAlchemy exclusiva de la tool.
        producto: Cultivo asociado o ``general``.
        concepto: Categoría breve del gasto.
        monto: Pesos chilenos, con formato numérico o ``lucas``.
        phone_hash: Identidad HMAC-SHA256, nunca teléfono en claro.
        fecha: Fecha ISO/chilena, ``hoy``, ``ayer`` o vacío para hoy.

    Returns:
        Confirmación o explicación de por qué no se guardó.
    """
    if not settings.expense_tracking_enabled:
        return "El registro de gastos todavía no está habilitado. No guardé el monto que indicaste."
    if not validate_phone_hash(phone_hash):
        return "No pude asociar el gasto de forma segura. No guardé el monto que indicaste."
    if not _has_expense_consent(session, phone_hash):
        return "No tengo tu consentimiento para guardar gastos. No guardé el monto que indicaste."

    normalized_product = _normalize_required_text(producto, max_length=100)
    normalized_concept = _normalize_required_text(concepto, max_length=120)
    amount_clp = _parse_amount_clp(monto)
    occurred_on = _parse_expense_date(fecha)

    if normalized_product is None:
        return "No entendí para qué producto fue el gasto. ¿Podrías repetirlo?"
    if normalized_concept is None:
        return "No entendí en qué gastaste. ¿Podrías repetir el concepto?"
    if amount_clp is None:
        return "No entendí el monto gastado. ¿Podrías repetir cuánto fue?"
    if occurred_on is None:
        return "No entendí la fecha del gasto o está en el futuro. ¿Podrías repetirla?"

    expires_at = _utcnow_naive() + datetime.timedelta(days=settings.expense_retention_days)
    session.add(
        Expense(
            phone_hash=phone_hash,
            producto=normalized_product,
            concepto=normalized_concept,
            amount_clp=amount_clp,
            occurred_on=occurred_on,
            expires_at=expires_at,
        )
    )
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.error("No se pudo persistir el gasto — error de base de datos")
        return "Tuve un problema al guardar el gasto. ¿Probamos de nuevo?"

    amount_spoken = f"{amount_clp:,}".replace(",", ".")
    return (
        f"Listo. Registré {normalized_concept} por {amount_spoken} pesos "
        f"para {normalized_product}, con fecha {occurred_on.strftime('%d/%m/%Y')}. "
        f"Se eliminará automáticamente en {settings.expense_retention_days} días."
    )


def get_active_expense_total(
    session: Session,
    phone_hash: str,
    producto: str,
    *,
    as_of: datetime.date | None = None,
    now: datetime.datetime | None = None,
) -> int:
    """Suma gastos vigentes del producto solo si gate y consentimiento siguen activos."""
    if (
        not settings.expense_tracking_enabled
        or not validate_phone_hash(phone_hash)
        or not _has_expense_consent(session, phone_hash)
    ):
        return 0

    normalized_product = _normalize_required_text(producto, max_length=100)
    if normalized_product is None:
        return 0

    effective_date = as_of or _today_santiago()
    effective_now = now or _utcnow_naive()
    total = session.scalar(
        select(func.coalesce(func.sum(Expense.amount_clp), 0)).where(
            Expense.phone_hash == phone_hash,
            func.lower(Expense.producto) == normalized_product,
            Expense.occurred_on <= effective_date,
            Expense.expires_at > effective_now,
        )
    )
    return int(total or 0)


def delete_expenses_for_subject(
    phone_hash: str,
    *,
    session: Session | None = None,
) -> int:
    """Borra todos los gastos del sujeto, incluso si el feature gate está apagado."""
    if not validate_phone_hash(phone_hash):
        raise ValueError("phone_hash inválido")

    owns_session = session is None
    db = session or SessionLocal()
    try:
        result = db.execute(delete(Expense).where(Expense.phone_hash == phone_hash))
        records_deleted = int(_affected_rows(result))
        db.commit()
        logger.info("Gastos eliminados a pedido — registros=%d", records_deleted)
        return records_deleted
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("No se pudo confirmar el borrado de gastos")
        raise ExpenseOperationError("expense_deletion_failed") from exc
    finally:
        if owns_session:
            db.close()


def purge_expired_expenses(
    *,
    session: Session | None = None,
    now: datetime.datetime | None = None,
) -> int:
    """Elimina filas vencidas sin depender del gate de nuevas escrituras."""
    cutoff = now or _utcnow_naive()
    owns_session = session is None
    db = session or SessionLocal()
    try:
        result = db.execute(delete(Expense).where(Expense.expires_at <= cutoff))
        records_deleted = int(_affected_rows(result))
        db.commit()
        logger.info("Purga TTL de gastos finalizada — registros=%d", records_deleted)
        return records_deleted
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("No se pudo confirmar la purga TTL de gastos")
        raise ExpenseOperationError("expense_purge_failed") from exc
    finally:
        if owns_session:
            db.close()
