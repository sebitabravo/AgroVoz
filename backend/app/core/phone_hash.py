"""Anonimización de números de teléfono con HMAC-SHA256 + pepper key.

SHA-256 simple es vulnerable a rainbow tables para espacios de búsqueda
pequeños (~10^8 combinaciones para números chilenos +56 9 XXXX XXXX).
HMAC-SHA256 con pepper key secreta hace el hash irreversible sin la clave,
incluso para un atacante con acceso a la tabla consultations.

Uso:
    from app.core.phone_hash import hash_phone, validate_phone_hash
    from app.core.config import settings

    hashed = hash_phone("+56912345678", settings.phone_hash_pepper)
    assert validate_phone_hash(hashed)  # True
"""

import hashlib
import hmac
import re


def hash_phone(phone: str, pepper: str) -> str:
    """Devuelve HMAC-SHA256 hex digest del número de teléfono.

    Args:
        phone: Número en formato E.164 (ej: "+56912345678").
        pepper: Clave secreta desde settings.phone_hash_pepper.

    Returns:
        64 caracteres hexadecimales en minúscula.

    Raises:
        ValueError: Si pepper está vacío (no hashear sin clave).
    """
    if not pepper:
        raise ValueError("phone_hash_pepper no puede estar vacío")
    return hmac.new(
        pepper.encode("utf-8"),
        phone.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def normalizar_e164(numero: str) -> str:
    """Normaliza un numero de telefono a formato E.164 canonico (+<digitos>).

    Limpia SOLO separadores de formato conocidos: espacios, guiones, parentesis,
    puntos, barras y guiones bajos. Rechaza caracteres no permitidos (letras,
    dígitos unicode, "+", etc). Valida que el resultado sea E.164 puro:
    entre 10 y 15 digitos ASCII, sin cero inicial (prohibido en E.164).

    El prefijo "+" es SIEMPRE agregado en la salida. Si la entrada tiene "+",
    solo se descarta el primer "+".

    Regla chilena (issue #126): un numero local de 9 digitos que empieza con 9
    y NO trae "+" (celular escrito a mano, ej: "9 1234 5678") se autocompleta
    con el codigo de pais 56. Con "+" explicito no se autocompleta.

    Args:
        numero: String con el numero en cualquier formato:
                "+56 9 1234 5678", "9 1234 5678", "(56)9-12345678", etc.
                Los chatIds de Open-WA (@c.us, @lid) NO son entrada válida
                de esta función; manéjalos en el caller (phone_to_chat_id).

    Returns:
        Numero en formato E.164 canonico: "+56912345678" (10-15 digitos ASCII).

    Raises:
        ValueError: Si el numero contiene caracteres no permitidos, tiene
                    longitud invalida (fuera de 10-15), o empieza con 0
                    (E.164 prohibe country code 0).

    Ejemplos:
        >>> normalizar_e164("+56 9 1234 5678")
        '+56912345678'
        >>> normalizar_e164("56 9 1234 5678")
        '+56912345678'
        >>> normalizar_e164("9 1234 5678")
        '+56912345678'
        >>> normalizar_e164("(56)9 1234.5678")
        '+56912345678'
        >>> normalizar_e164("+56_9/1234_5678")
        '+56912345678'
        >>> normalizar_e164("123")
        Traceback (most recent call last):
        ValueError: Numero invalido: '123' tiene 3 digitos (minimo 10, maximo 15)
        >>> normalizar_e164("56abc912345678")
        Traceback (most recent call last):
        ValueError: Numero invalido: '56abc912345678' contiene caracteres no permitidos
    """
    s: str = numero.strip()

    # Descartar SOLO el primer "+" si existe. Se recuerda si venia, porque
    # un numero CON "+" declara formato internacional y no se autocompleta.
    tiene_prefijo_internacional = s.startswith("+")
    resto = s[1:] if tiene_prefijo_internacional else s

    # Limpiar SOLO separadores de formato conocidos. La clase de caracteres
    # debe estar exacta: sin caracteres unicode invisibles.
    digitos: str = re.sub(r"[\s\-()./_]", "", resto)

    # Chequeo 1: ¿hay caracteres no permitidos? (NO dígitos ASCII).
    # Va primero para que el mensaje de largo no cuente letras como "digitos".
    # La cadena vacía se salta: se reporta como "0 digitos" en el chequeo 2.
    if digitos and not re.fullmatch(r"[0-9]+", digitos):
        raise ValueError(f"Numero invalido: '{numero}' contiene caracteres no permitidos")

    # Celular chileno escrito en formato local (9 digitos empezando con 9,
    # sin "+"): se autocompleta el codigo de pais 56 (issue #126). Heuristica
    # segura porque el MVP es Chile-only; con "+" explicito no se toca.
    if not tiene_prefijo_internacional and len(digitos) == 9 and digitos.startswith("9"):
        digitos = f"56{digitos}"

    # Chequeo 2: ¿tiene largo E.164? (10-15 dígitos).
    if not (10 <= len(digitos) <= 15):
        raise ValueError(f"Numero invalido: '{numero}' tiene {len(digitos)} digitos (minimo 10, maximo 15)")

    # Chequeo 3: ¿empieza con 0? (E.164 prohibe country code 0).
    if digitos.startswith("0"):
        raise ValueError(f"Numero invalido: '{numero}' no puede empezar con 0 (E.164 prohibe country code 0)")

    return f"+{digitos}"


def validate_phone_hash(value: str) -> bool:
    """Valida que un string tenga formato de hash SHA-256 hexadecimal.

    Args:
        value: String a validar.

    Returns:
        True si son exactamente 64 caracteres hexadecimales en minúscula.

    Usa regex para evitar que int(value, 16) acepte prefijos +, -, y whitespace.
    """
    if not isinstance(value, str):
        return False
    return bool(re.fullmatch(r"[0-9a-f]{64}", value))
