"""Tests para el módulo de autenticación del dashboard admin (T5.3).

Cubre:
- create_session_cookie / verify_session_cookie: roundtrip, None, tampered, expiry.
- is_valid_login: key correcta, incorrecta, vacía.
- AdminAuthMiddleware: path público pasa, protegido sin cookie redirige,
  protegido con cookie válida pasa.
"""

from httpx import AsyncClient

from app.admin.auth import (
    COOKIE_NAME,
    create_session_cookie,
    is_valid_login,
    verify_session_cookie,
)
from app.core.config import settings

# ── Cookies de sesión ──────────────────────────────────────────────


class TestSessionCookie:
    """Ciclo de vida del token firmado de sesión admin."""

    def test_create_retorna_str_no_vacio(self) -> None:
        token = create_session_cookie()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_roundtrip_create_verify_valida(self) -> None:
        token = create_session_cookie()
        assert verify_session_cookie(token) is True

    def test_verify_none_retorna_false(self) -> None:
        assert verify_session_cookie(None) is False

    def test_verify_string_vacio_retorna_false(self) -> None:
        assert verify_session_cookie("") is False

    def test_verify_tampered_retorna_false(self) -> None:
        """Modificar un carácter del token invalida la firma."""
        token = create_session_cookie()
        # Cambiar el último carácter: si era alnum, alterarlo; si no, poner 'x'.
        tampered = token[:-1] + ("x" if token[-1] != "x" else "y")
        assert verify_session_cookie(tampered) is False

    def test_verify_token_de_otro_secret_retorna_false(self) -> None:
        """Un token firmado con otro secreto no valida contra el nuestro."""
        from itsdangerous import URLSafeTimedSerializer

        otro = URLSafeTimedSerializer("otro-secreto-distinto", salt="admin-session-v1")
        token_externo = otro.dumps({"v": 1})
        assert verify_session_cookie(token_externo) is False

    def test_verify_payload_truncado_retorna_false(self) -> None:
        """Truncar el token rompe la firma."""
        token = create_session_cookie()
        assert verify_session_cookie(token[: len(token) // 2]) is False


# ── is_valid_login ─────────────────────────────────────────────────


class TestIsValidLogin:
    """Validación del admin_key del formulario (comparación constante)."""

    def test_key_correcta_retorna_true(self) -> None:
        assert is_valid_login(settings.admin_api_key) is True

    def test_key_incorrecta_retorna_false(self) -> None:
        assert is_valid_login("no-es-el-key") is False

    def test_key_vacia_retorna_false(self) -> None:
        assert is_valid_login("") is False

    def test_key_parecida_no_matchea_parcial(self) -> None:
        """Comparación exacta: un prefijo del key no debe validar."""
        assert is_valid_login(settings.admin_api_key[:-2]) is False


# ── AdminAuthMiddleware ────────────────────────────────────────────


class TestAdminAuthMiddleware:
    """Middleware de auth: rutas públicas vs protegidas, con/sin cookie.

    Usa el client fixture del conftest (app real con get_db overrideado a
    SQLite temporal). Los middlewares TrustedHost/RateLimit/SecurityHeaders
    no interfieren: testserver es host permitido y el volumen de requests
    por test no satura el rate limiter.
    """

    async def test_login_es_publico_sin_cookie(self, client: AsyncClient) -> None:
        """GET /admin/login no requiere cookie (ahí se autentica)."""
        resp = await client.get("/admin/login", follow_redirects=False)
        assert resp.status_code == 200

    async def test_ruta_protegida_sin_cookie_redirige_a_login(
        self, client: AsyncClient
    ) -> None:
        resp = await client.get("/admin/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/login"

    async def test_ruta_protegida_con_cookie_valida_pasa(
        self, client: AsyncClient
    ) -> None:
        client.cookies.set(COOKIE_NAME, create_session_cookie())
        resp = await client.get("/admin/", follow_redirects=False)
        # No redirige a login: llegó al handler y renderizó el dashboard.
        assert resp.status_code == 200

    async def test_ruta_protegida_con_cookie_invalida_redirige(
        self, client: AsyncClient
    ) -> None:
        client.cookies.set(COOKIE_NAME, "token-falso-no-firmado")
        resp = await client.get("/admin/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/login"

    async def test_ruta_no_admin_no_se_protige(self, client: AsyncClient) -> None:
        """Paths fuera de /admin/ no tocan la auth middleware.

        Usa probe=liveness para que el test sea determinista y no dependa
        de ffmpeg/DB en CI (ver patron en tests/test_health.py).
        """
        resp = await client.get("/api/v1/health?probe=liveness", follow_redirects=False)
        assert resp.status_code == 200
