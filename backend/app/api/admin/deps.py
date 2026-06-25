"""Dependencias de autenticación para endpoints admin (API JSON).

Header ``X-Admin-Key`` comparado en tiempo constante (hmac.compare_digest)
contra ``settings.admin_api_key`` para evitar timing attacks.

El dashboard HTML (app/admin) usa cookie firmada, no este header. Ambos
caminos validan contra la misma clave configurada.
"""

import hmac

from fastapi import Header, HTTPException, status

from app.core.config import settings


def require_admin_key(x_admin_key: str = Header(default="", alias="X-Admin-Key")) -> None:
    """Valida que el request traiga X-Admin-Key correcto.

    Comparación en tiempo constante para no filtrar cuántos caracteres del
    key coinciden. 401 si falta o no coincide (mismo status para no revelar
    cuál falló).
    """
    clave_enviada = x_admin_key or ""
    clave_esperada = settings.admin_api_key
    if not clave_esperada or not hmac.compare_digest(clave_enviada, clave_esperada):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key admin inválida o ausente.",
            headers={"WWW-Authenticate": "X-Admin-Key"},
        )
