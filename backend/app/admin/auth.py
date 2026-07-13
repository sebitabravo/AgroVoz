"""Autenticación del dashboard admin (HTML SSR).

Flujo de sesión con cookie firmada (itsdangerous URLSafeTimedSerializer):
  1. GET /admin/login → formulario.
  2. POST /admin/login → valida admin_api_key; si OK, setea cookie
     firmada con TTL configurable y redirige a /admin/.
  3. Middleware valida cookie en cada /admin/* (excepto login).
  4. POST /admin/logout → borra cookie.

La cookie es opaca (no expone el key): solo dice "sesión válida hasta X".
Firmada con admin_session_secret → no falsificable sin el secreto.

Diferencia con deps.py: deps.require_admin_key valida el header X-Admin-Key
para APIs JSON programáticas. Acá validamos una cookie de navegador.
"""

import hmac
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

from app.core.config import settings

# Nombre de la cookie de sesión admin.
COOKIE_NAME = "agrovoz_admin"
# Salt del serializer: separa namespaces de firmas dentro del mismo secreto.
_SALT = "admin-session-v1"

# Paths que NO requieren cookie válida (login se autentica con el formulario).
# El manifest y el service worker de la PWA deben ser publicos para que el
# navegador pueda evaluar la instalabilidad incluso antes del login.
_PUBLIC_PATHS = {
    "/admin/login",
    "/admin/manifest.json",
    "/admin/sw.js",
}


def _serializer() -> URLSafeTimedSerializer:
    """Serializer con el secreto de sesión. Uno por call (stateless)."""
    return URLSafeTimedSerializer(settings.admin_session_secret, salt=_SALT)


def create_session_cookie() -> str:
    """Genera un token firmado de sesión. Payload mínimo opaco."""
    return _serializer().dumps({"v": 1})


def verify_session_cookie(value: str | None) -> bool:
    """True si la cookie firma y TTL son válidos. False si ausente/expirada/tampered."""
    if not value:
        return False
    try:
        _serializer().loads(value, max_age=settings.admin_session_ttl)
        return True
    except (BadSignature, SignatureExpired):
        return False


def is_valid_login(key: str) -> bool:
    """Compara el key del formulario con admin_api_key en tiempo constante."""
    esperado = settings.admin_api_key
    return bool(esperado) and hmac.compare_digest(key, esperado)


def set_session_cookie(response: Response) -> None:
    """Adjunta la cookie de sesión a una respuesta (login OK)."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=create_session_cookie(),
        max_age=settings.admin_session_ttl,
        httponly=True,  # JS del navegador no la lee (XSS no la roba).
        secure=settings.app_env == "production",  # HTTPS-only en prod.
        samesite="lax",  # CSRF: la cookie no se envía en requests cross-site.
        path="/admin",
    )


def clear_session_cookie(response: Response) -> None:
    """Borra la cookie de sesión (logout)."""
    response.delete_cookie(key=COOKIE_NAME, path="/admin")


class AdminAuthMiddleware(BaseHTTPMiddleware):
    """Middleware que protege /admin/* excepto las rutas públicas.

    Sin cookie válida → 303 redirect a /admin/login para navegadores.
    Las rutas /admin/login son públicas (ahí se autentica).
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[StarletteResponse]],
    ) -> StarletteResponse:
        path = request.url.path
        if path.startswith("/admin") and not path.startswith(tuple(_PUBLIC_PATHS)):
            cookie = request.cookies.get(COOKIE_NAME)
            if not verify_session_cookie(cookie):
                return RedirectResponse("/admin/login", status_code=303)
        return await call_next(request)
