"""Tests de normalizacion E.164 y hashing de telefonos.

Cubre normalizar_e164 (valores validos, edge cases, errores) e integracion
con phone_to_chat_id de openwa_service.
"""

from __future__ import annotations

import pytest

from app.core.phone_hash import hash_phone, normalizar_e164, validate_phone_hash
from app.services.openwa_service import phone_to_chat_id


# ── normalizar_e164: casos validos ──────────────────────────────

VALIDOS = pytest.mark.parametrize(
    "entrada,esperado",
    [
        # E.164 canonico ya normalizado
        ("+56912345678", "+56912345678"),
        # Con espacios
        ("+56 9 1234 5678", "+56912345678"),
        # Con guiones
        ("+56-9-1234-5678", "+56912345678"),
        ("+1-555-123-4567", "+15551234567"),
        # Con parentesis
        ("(56)9 1234 5678", "+56912345678"),
        ("+1 (555) 123-4567", "+15551234567"),
        # Con puntos (formato internacional)
        ("+56.9.1234.5678", "+56912345678"),
        # Sin prefijo '+' (se agrega automaticamente)
        ("56912345678", "+56912345678"),
        ("1234567890", "+1234567890"),
        # Numero largo (maximo 15 digitos: + y 15 digitos)
        ("+123456789012345", "+123456789012345"),
        # Numero corto (minimo 10 digitos)
        ("+1234567890", "+1234567890"),
        # Caracteres mixtos con guiones bajos y barras (no comunes pero defensivos)
        ("+56_9/1234_5678", "+56912345678"),
    ],
)


@VALIDOS
def test_normalizar_e164_valido(entrada: str, esperado: str) -> None:
    """normalizar_e164 limpia formato y retorna E.164 canonico."""
    assert normalizar_e164(entrada) == esperado


# ── normalizar_e164: chatIds de Open-WA (sin tocar) ─────────────

CHAT_IDS = pytest.mark.parametrize(
    "entrada,esperado",
    [
        # ChatId @c.us — no validar largo (ya tiene el sufijo)
        ("56912345678@c.us", "56912345678@c.us"),
        ("123@c.us", "123@c.us"),  # corto pero es chatId, no se valida
        # ChatId @lid — no validar largo
        ("248069442560050@lid", "248069442560050@lid"),
        ("123@lid", "123@lid"),
        # ChatId con espacios (no deberia pasar, pero es defensivo: strip() lo limpia)
        (" 56912345678@c.us ", "56912345678@c.us"),
    ],
)


@CHAT_IDS
def test_normalizar_e164_chat_id_sin_cambios(entrada: str, esperado: str) -> None:
    """ChatIds de Open-WA (@c.us, @lid) se retornan sin validar largo."""
    assert normalizar_e164(entrada) == esperado


# ── normalizar_e164: errores (largo invalido) ───────────────────

INVALIDOS = pytest.mark.parametrize(
    "entrada,mensaje_esperado",
    [
        # Muy corto (menos de 10 digitos)
        ("123", "3 digitos"),
        ("+1", "1 digitos"),
        ("+569", "3 digitos"),
        ("9 1234 5678", "9 digitos"),
        ("912345678", "9 digitos"),
        # Muy largo (mas de 15 digitos)
        ("+1234567890123456", "16 digitos"),
        ("12345678901234567890", "20 digitos"),
        # Solo caracteres no numericos
        ("+--() ...", "0 digitos"),
        # String vacio
        ("", "0 digitos"),
    ],
)


@INVALIDOS
def test_normalizar_e164_invalido_lanza_value_error(
    entrada: str, mensaje_esperado: str,
) -> None:
    """Numeros con largo fuera de 10-15 digitos lanzan ValueError."""
    with pytest.raises(ValueError, match=mensaje_esperado):
        normalizar_e164(entrada)


# ── phone_to_chat_id: integracion con normalizar_e164 ───────────

PHONE_TO_CHAT_ID_CASES = pytest.mark.parametrize(
    "entrada,esperado",
    [
        # E.164 limpio
        ("+56912345678", "56912345678@c.us"),
        # Con espacios, guiones, parentesis (debe normalizar primero)
        ("+56 9 1234 5678", "56912345678@c.us"),
        ("+56-9-1234-5678", "56912345678@c.us"),
        ("(56)9 1234 5678", "56912345678@c.us"),
        # ChatIds se retornan sin cambios
        ("56912345678@c.us", "56912345678@c.us"),
        ("248069442560050@lid", "248069442560050@lid"),
    ],
)


@PHONE_TO_CHAT_ID_CASES
def test_phone_to_chat_id_normaliza_con_e164(entrada: str, esperado: str) -> None:
    """phone_to_chat_id normaliza con E.164 y agrega @c.us si es numero."""
    assert phone_to_chat_id(entrada) == esperado


def test_phone_to_chat_id_numero_invalido_lanza_value_error() -> None:
    """Si el numero tiene largo E.164 invalido, phone_to_chat_id propaga ValueError."""
    with pytest.raises(ValueError, match="3 digitos"):
        phone_to_chat_id("123")


# ── hash_phone / validate_phone_hash (regresion) ────────────────

def test_hash_phone_consistente() -> None:
    """Mismo numero + mismo pepper = mismo hash."""
    h1 = hash_phone("+56912345678", "pepper-secreto")
    h2 = hash_phone("+56912345678", "pepper-secreto")
    assert h1 == h2
    assert len(h1) == 64


def test_hash_phone_diferente_pepper_diferente_hash() -> None:
    """Pepper distinto produce hash distinto para el mismo numero."""
    h1 = hash_phone("+56912345678", "pepper-a")
    h2 = hash_phone("+56912345678", "pepper-b")
    assert h1 != h2


def test_hash_phone_pepper_vacio_lanza_value_error() -> None:
    """Pepper vacio lanza ValueError (no hashear sin clave)."""
    with pytest.raises(ValueError, match="phone_hash_pepper"):
        hash_phone("+56912345678", "")


def test_validate_phone_hash_valido() -> None:
    """Valida un hash SHA-256 hexadecimal de 64 caracteres."""
    h = hash_phone("+56912345678", "pepper")
    assert validate_phone_hash(h) is True


def test_validate_phone_hash_invalido() -> None:
    """Rechaza strings que no son 64 caracteres hexadecimales."""
    assert validate_phone_hash("abc123") is False
    assert validate_phone_hash("g" * 64) is False  # 'g' no es hex
    assert validate_phone_hash("") is False
    assert validate_phone_hash(123) is False  # type: ignore[arg-type] — no string
