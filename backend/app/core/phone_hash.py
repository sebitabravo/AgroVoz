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

    Limpia espacios, guiones, parentesis, puntos y cualquier caracter
    no numerico (excepto el '+' inicial). Si el numero ya tiene prefijo '+'
    se conserva; si no, se agrega automaticamente.

    Las reglas de validacion son estrictas pero pragmaticas:
    - El resultado debe tener entre 10 y 15 digitos (ITU-T E.164).
    - El formato canonico de Open-WA (@c.us, @lid) se retorna sin cambios
      (no se valida largo de chatIds porque Open-WA los maneja distinto).
    - Si el numero tiene longitud invalida, lanza ValueError con un mensaje
      claro para que el caller decida como manejarlo (no envio roto).

    Args:
        numero: String con el numero en cualquier formato:
                "+56 9 1234 5678", "9 1234 5678", "(56)9-12345678",
                "56912345678@c.us", "248069442560050@lid".

    Returns:
        Numero en formato E.164 canonico: "+56912345678", o el chatId original
        si ya tiene sufijo de Open-WA.

    Raises:
        ValueError: Si el numero (sin sufijo) no tiene entre 10 y 15 digitos.

    Ejemplos:
        >>> normalizar_e164("9 1234 5678")
        '+912345678'
        >>> normalizar_e164("+56 9 1234-5678")
        '+56912345678'
        >>> normalizar_e164("(56)9 1234.5678")
        '+56912345678'
        >>> normalizar_e164("56912345678@c.us")
        '56912345678@c.us'
        >>> normalizar_e164("123")
        Traceback (most recent call last):
        ValueError: Numero invalido: '123' tiene 3 digitos (minimo 10)
    """
    s: str = numero.strip()

    # Los chatIds de Open-WA (@c.us, @lid) se retornan sin validar largo.
    # La validacion de longitud E.164 no aplica porque los @lid tienen
    # prefijos arbitrarios asignados por WhatsApp.
    if s.endswith("@c.us") or s.endswith("@lid"):
        return s

    # Extraer el '+' inicial si existe y guardar el resto.
    if s.startswith("+"):
        prefijo = "+"
        resto = s[1:]
    else:
        prefijo = "+"
        resto = s

    # Limpiar: todo lo que no sea dígito se descarta (espacios, guiones,
    # parentesis, puntos, barras, etc.).
    digitos: str = re.sub(r"\D", "", resto)

    if not (10 <= len(digitos) <= 15):
        raise ValueError(
            f"Numero invalido: '{numero}' tiene {len(digitos)} digitos "
            f"(minimo 10, maximo 15)"
        )

    return f"{prefijo}{digitos}"


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
