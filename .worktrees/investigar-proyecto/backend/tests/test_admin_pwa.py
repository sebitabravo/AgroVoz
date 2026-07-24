"""Tests de PWA para el dashboard admin (Issue #92).

Cubre:
- Manifest JSON estatico valido, con scope e iconos requeridos.
- Endpoint del Service Worker accesible, con content-type y no-cache.
- base.html incluye manifest, theme-color y registro del SW.
- El Service Worker NO cachea el dashboard autenticado ni datos sensibles.
- Respuestas del admin llevan Cache-Control: no-store (regresion privacidad).
- _PUBLIC_PATHS matchea exacto: sin bypass por prefijo (regresion auth).
"""

from httpx import AsyncClient

from app.admin.auth import COOKIE_NAME, create_session_cookie


def _autenticar(client: AsyncClient) -> None:
    """Setea cookie de sesion admin valida en el client."""
    client.cookies.set(COOKIE_NAME, create_session_cookie())


class TestManifestPwa:
    """Validacion del manifest.json estatico del admin."""

    async def test_manifest_json_es_valido(self, client: AsyncClient) -> None:
        resp = await client.get("/static/manifest.json", follow_redirects=False)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "AgroVoz Admin"
        assert data["short_name"] == "AgroVoz"
        assert data["start_url"] == "/admin/"
        assert data["display"] == "standalone"
        assert "theme_color" in data
        assert "background_color" in data

    async def test_manifest_declara_scope_admin(self, client: AsyncClient) -> None:
        # El manifest vive en /static/: sin scope explicito el navegador
        # inferiria /static/ y start_url quedaria fuera (manifest invalido).
        resp = await client.get("/static/manifest.json", follow_redirects=False)
        assert resp.json()["scope"] == "/admin/"

    async def test_manifest_tiene_iconos(self, client: AsyncClient) -> None:
        resp = await client.get("/static/manifest.json", follow_redirects=False)
        assert resp.status_code == 200
        icons = resp.json()["icons"]
        assert len(icons) >= 2
        sizes = {icon["sizes"] for icon in icons}
        assert "192x192" in sizes
        assert "512x512" in sizes

    async def test_iconos_pwa_existen(self, client: AsyncClient) -> None:
        for icono in ("/static/icon-192.png", "/static/icon-512.png"):
            resp = await client.get(icono, follow_redirects=False)
            assert resp.status_code == 200, f"falta {icono}"
            assert resp.headers["content-type"] == "image/png"


class TestServiceWorker:
    """Validacion del endpoint y contenido del Service Worker."""

    async def test_service_worker_endpoint_existe(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/sw.js", follow_redirects=False)
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]
        assert "self.addEventListener" in resp.text

    async def test_service_worker_revalida_siempre(self, client: AsyncClient) -> None:
        # no-cache: los cambios del SW llegan al navegador sin esperar TTL.
        resp = await client.get("/admin/sw.js", follow_redirects=False)
        assert resp.headers["cache-control"] == "no-cache"

    async def test_service_worker_no_cachea_dashboard(self, client: AsyncClient) -> None:
        """Regresion PR #118: '/admin/' en SHELL_ASSETS cacheaba HTML autenticado."""
        resp = await client.get("/admin/sw.js", follow_redirects=False)
        assert resp.status_code == 200
        assert '"/admin/"' not in resp.text

    async def test_service_worker_no_cachea_api(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/sw.js", follow_redirects=False)
        assert resp.status_code == 200
        sw = resp.text
        # Nunca debe cachear endpoints dinamicos, API o datos sensibles.
        assert "/admin/api" not in sw
        assert "/admin/prices/export" not in sw
        assert "/admin/piloto" not in sw
        assert "/admin/revision" not in sw
        assert "/admin/metrics" not in sw
        assert "/admin/activity" not in sw


class TestCacheControlAdmin:
    """El HTML del admin nunca debe persistir en caches (Ley 21.719)."""

    async def test_dashboard_autenticado_es_no_store(self, client: AsyncClient) -> None:
        _autenticar(client)
        resp = await client.get("/admin/", follow_redirects=False)
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "no-store"

    async def test_login_es_no_store(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/login", follow_redirects=False)
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "no-store"


class TestPublicPathsExactos:
    """Regresion PR #118: matching por prefijo dejaba rutas sin proteccion."""

    async def test_manifest_en_admin_ya_no_es_publico(self, client: AsyncClient) -> None:
        # El manifest se movio a /static/: bajo /admin/ requiere sesion.
        resp = await client.get("/admin/manifest.json", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/login"

    async def test_prefijo_de_path_publico_requiere_sesion(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/sw.js.map", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/login"


class TestBaseHtmlPwa:
    """Validacion de base.html como shell de la PWA."""

    async def test_base_html_incluye_manifest(self, client: AsyncClient) -> None:
        _autenticar(client)
        resp = await client.get("/admin/", follow_redirects=False)
        assert resp.status_code == 200
        assert '<link rel="manifest" href="/static/manifest.json">' in resp.text

    async def test_base_html_incluye_theme_color(self, client: AsyncClient) -> None:
        _autenticar(client)
        resp = await client.get("/admin/", follow_redirects=False)
        assert resp.status_code == 200
        assert '<meta name="theme-color" content="#4f7d5a">' in resp.text

    async def test_base_html_registra_service_worker(self, client: AsyncClient) -> None:
        _autenticar(client)
        resp = await client.get("/admin/", follow_redirects=False)
        assert resp.status_code == 200
        # El registro del SW vive en un script externo (CSP: script-src 'self').
        assert '<script src="/static/admin-pwa-register.js"></script>' in resp.text

    async def test_base_html_incluye_apple_touch_icon(self, client: AsyncClient) -> None:
        _autenticar(client)
        resp = await client.get("/admin/", follow_redirects=False)
        assert resp.status_code == 200
        assert '<link rel="apple-touch-icon" href="/static/icon-192.png">' in resp.text
