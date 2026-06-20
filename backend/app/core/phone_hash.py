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


def validate_phone_hash(value: str) -> bool:
    """Valida que un string tenga formato de hash SHA-256 hexadecimal.

    Args:
        value: String a validar.

    Returns:
        True si son exactamente 64 caracteres hexadecimales en minúscula.
    """
    if len(value) != 64:
        return False
    # islower() retorna False para strings sin letras (ej: "0" * 64).
    # value != value.lower() cubre el caso de dígitos puros correctamente.
    if value != value.lower():
        return False
    try:
        int(value, 16)
        return True
    except ValueError:
        return False
