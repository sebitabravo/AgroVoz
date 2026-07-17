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
        # Celular chileno local (9 digitos, empieza con 9, sin "+"):
        # se autocompleta el codigo de pais 56 (issue #126)
        ("9 1234 5678", "+56912345678"),
        ("912345678", "+56912345678"),
        ("9-1234-5678", "+56912345678"),
    ],
)


@VALIDOS
def test_normalizar_e164_valido(entrada: str, esperado: str) -> None:
    """normalizar_e164 limpia formato y retorna E.164 canonico."""
    assert normalizar_e164(entrada) == esperado


# ── normalizar_e164: chatIds de Open-WA (SIN SOPORTAR) ──────────

# Los chatIds de Open-WA (@c.us, @lid) NO son soportados en normalizar_e164().
# El caller (phone_to_chat_id) es quien debe chequear y pasar chatIds sin cambios.
# Tests de pass-through de chatIds están en PHONE_TO_CHAT_ID_CASES.


# ── normalizar_e164: errores (caracteres no permitidos) ────────

CARACTERES_INVALIDOS = pytest.mark.parametrize(
    "entrada,mensaje_esperado",
    [
        # Digitos arabes (Unicode no-ASCII)
        ("٥٦٩١٢٣٤٥٦٧٨", "contiene caracteres no permitidos"),
        # Digitos fullwidth (Unicode no-ASCII)
        ("５６９１２３４５６７８９", "contiene caracteres no permitidos"),  # noqa: RUF001
        # Letras en el numero
        ("+56abc912345678", "contiene caracteres no permitidos"),
        ("56 abc 912345678", "contiene caracteres no permitidos"),
        # "+" en posicion intermedia (luego del primer caracter)
        ("56+912345678", "contiene caracteres no permitidos"),
        ("5+6912345678", "contiene caracteres no permitidos"),
    ],
)


@CARACTERES_INVALIDOS
def test_normalizar_e164_caracteres_no_permitidos_lanza_value_error(
    entrada: str,
    mensaje_esperado: str,
) -> None:
    """Numeros con caracteres no permitidos (letras, Unicode) lanzan ValueError."""
    with pytest.raises(ValueError, match=mensaje_esperado):
        normalizar_e164(entrada)


# ── normalizar_e164: errores (largo invalido) ───────────────────

INVALIDOS = pytest.mark.parametrize(
    "entrada,mensaje_esperado",
    [
        # Muy corto (menos de 10 digitos)
        ("123", "3 digitos"),
        ("+1", "1 digitos"),
        ("+569", "3 digitos"),
        # 9 digitos que NO empiezan con 9: la heuristica chilena no aplica
        ("812345678", "9 digitos"),
        # 9 digitos CON "+" explicito: formato internacional incompleto,
        # no se autocompleta 56 (podria ser otro pais truncado)
        ("+912345678", "9 digitos"),
        # Muy largo (mas de 15 digitos)
        ("+1234567890123456", "16 digitos"),
        ("12345678901234567890", "20 digitos"),
        # Solo caracteres no numericos (resulta en 0 digitos)
        ("+--() ...", "0 digitos"),
        # String vacio
        ("", "0 digitos"),
    ],
)


@INVALIDOS
def test_normalizar_e164_largo_invalido_lanza_value_error(
    entrada: str,
    mensaje_esperado: str,
) -> None:
    """Numeros con largo fuera de 10-15 digitos lanzan ValueError."""
    with pytest.raises(ValueError, match=mensaje_esperado):
        normalizar_e164(entrada)


# ── normalizar_e164: errores (cero inicial) ──────────────────────

CERO_INICIAL = pytest.mark.parametrize(
    "entrada,mensaje_esperado",
    [
        # Cero inicial rechazado (E.164 prohibe country code 0)
        ("0912345678", "no puede empezar con 0"),
        ("+0912345678", "no puede empezar con 0"),
        ("0056912345678", "no puede empezar con 0"),
    ],
)


@CERO_INICIAL
def test_normalizar_e164_cero_inicial_lanza_value_error(
    entrada: str,
    mensaje_esperado: str,
) -> None:
    """Numeros que empiezan con 0 (country code 0 prohibido en E.164) lanzan ValueError."""
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
        # Los 3 formatos del criterio de aceptacion de la issue #126
        # deben producir el MISMO chatId valido
        ("9 1234 5678", "56912345678@c.us"),
        ("+56 9 1234-5678", "56912345678@c.us"),
        ("(569)12345678", "56912345678@c.us"),
        # ChatIds se retornan sin cambios (incluso sin validar largo)
        ("56912345678@c.us", "56912345678@c.us"),
        ("123@c.us", "123@c.us"),  # corto pero es chatId, se retorna como-esta
        ("248069442560050@lid", "248069442560050@lid"),
        # ChatIds con espacios alrededor (strip los limpia)
        (" 56912345678@c.us ", "56912345678@c.us"),
        (" 56912345678@lid ", "56912345678@lid"),
    ],
)


@PHONE_TO_CHAT_ID_CASES
def test_phone_to_chat_id_normaliza_con_e164(entrada: str, esperado: str) -> None:
    """phone_to_chat_id normaliza con E.164 y agrega @c.us si es numero."""
    assert phone_to_chat_id(entrada) == esperado


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


# ── phone_to_chat_id: propagacion de ValueError ──────────────────


def test_phone_to_chat_id_numero_invalido_propaga_value_error() -> None:
    """Si normalizar_e164 lanza ValueError, phone_to_chat_id lo propaga."""
    # Numero invalido: "123" es solo 3 digitos (minimo 10 requerido)
    with pytest.raises(ValueError, match="3 digitos"):
        phone_to_chat_id("123")

    # Caracter no permitido: letras en el numero
    with pytest.raises(ValueError, match="contiene caracteres no permitidos"):
        phone_to_chat_id("56abc912345678")

    # Cero inicial rechazado
    with pytest.raises(ValueError, match="no puede empezar con 0"):
        phone_to_chat_id("0912345678")
