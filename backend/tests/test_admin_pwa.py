"""Tests de PWA para el dashboard admin (Issue #92).

Cubre:
- Manifest JSON valido y con iconos requeridos.
- Endpoint del Service Worker accesible con content-type correcto.
- base.html incluye manifest, theme-color y registro del SW.
- Service Worker NO cachea endpoints dinamicos ni datos sensibles.
"""

from httpx import AsyncClient

from app.admin.auth import COOKIE_NAME, create_session_cookie


def _autenticar(client: AsyncClient) -> None:
    """Setea cookie de sesion admin valida en el client."""
    client.cookies.set(COOKIE_NAME, create_session_cookie())


class TestManifestPwa:
    """Validacion del manifest.json del admin."""

    async def test_manifest_json_es_valido(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/manifest.json", follow_redirects=False)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "AgroVoz Admin"
        assert data["short_name"] == "AgroVoz"
        assert data["start_url"] == "/admin/"
        assert data["display"] == "standalone"
        assert "theme_color" in data
        assert "background_color" in data

    async def test_manifest_tiene_iconos(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/manifest.json", follow_redirects=False)
        assert resp.status_code == 200
        icons = resp.json()["icons"]
        assert len(icons) >= 2
        sizes = {icon["sizes"] for icon in icons}
        assert "192x192" in sizes
        assert "512x512" in sizes


class TestServiceWorker:
    """Validacion del endpoint y contenido del Service Worker."""

    async def test_service_worker_endpoint_existe(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/sw.js", follow_redirects=False)
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]
        assert "self.addEventListener" in resp.text

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


class TestBaseHtmlPwa:
    """Validacion de base.html como shell de la PWA."""

    async def test_base_html_incluye_manifest(self, client: AsyncClient) -> None:
        _autenticar(client)
        resp = await client.get("/admin/", follow_redirects=False)
        assert resp.status_code == 200
        assert '<link rel="manifest" href="/admin/manifest.json">' in resp.text

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
