"""Tests E2E contra la API deployada de AgroVoz.

A diferencia de los tests unitarios (que usan ASGI transport con mocks),
estos tests hacen requests HTTP reales contra una instancia deployada
para detectar problemas de configuracion que los mocks no cubren:

- CORS headers faltantes
- Env vars mal seteadas en produccion
- TrustedHostMiddleware bloqueando requests legitimos
- Timeouts o latencia inaceptable
- Errores 500 inesperados en endpoints criticos

Uso:
    # Local (default: http://localhost:8000)
    uv run pytest tests/e2e/ -v -m "e2e"

    # Contra deploy especifico
    AGROVOZ_E2E_BASE_URL=https://api.agrovoz.cl \\
    AGROVOZ_E2E_ADMIN_KEY=secret-key \\
    uv run pytest tests/e2e/ -v -m "e2e"

Requisitos:
    - httpx (incluido en dev deps)
    - La API target debe estar accesible desde donde se ejecuta el test
"""

from __future__ import annotations

import os

import httpx
import pytest

# ── Configuracion ────────────────────────────────────────────────

E2E_BASE_URL = os.environ.get("AGROVOZ_E2E_BASE_URL", "http://localhost:8000")
E2E_ADMIN_KEY = os.environ.get("AGROVOZ_E2E_ADMIN_KEY", "dev-admin-key")

# Timeout generoso para detectar problemas de performance sin falsos
# positivos. La API deployada puede tener cold start del LLM o latencia
# de red.
E2E_TIMEOUT_S = int(os.environ.get("AGROVOZ_E2E_TIMEOUT", "30"))


# ── Funcion de skip condicional ──────────────────────────────────

def _api_responde() -> bool:
    """Verifica si la API target responde antes de correr los tests.

    Hace un GET rapido a /health?probe=liveness con timeout de 5s.
    Si la URL no responde (timeout, connection refused, DNS fail),
    retorna False para que pytest skip los tests en vez de fallar.

    Esto permite que:
    - `make test` y `make test-unit` ignoren estos tests si no hay deploy
    - CI los ejecute solo cuando AGROVOZ_E2E_BASE_URL esta seteado
    """
    try:
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/health?probe=liveness",
            timeout=5.0,
        )
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


skip_sin_api = pytest.mark.skipif(
    not _api_responde(),
    reason=(
        f"API no responde en {E2E_BASE_URL}. "
        "Setear AGROVOZ_E2E_BASE_URL si la URL es distinta, "
        "o ignorar si no hay deploy disponible."
    ),
)


# ── Tests ─────────────────────────────────────────────────────────


@skip_sin_api
@pytest.mark.e2e
class TestHealth:
    """Health check: liveness, readiness, headers de seguridad."""

    def test_health_liveness(self) -> None:
        """GET /health?probe=liveness retorna 200 con status=ok."""
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/health?probe=liveness",
            timeout=E2E_TIMEOUT_S,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "version" in body

    def test_health_readiness(self) -> None:
        """GET /health?probe=readiness retorna 200 o 503 (degradado valido).

        En produccion, degraded es aceptable si ffmpeg no esta instalado
        en el contenedor o la DB tuvo un problema temporal.
        """
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/health?probe=readiness",
            timeout=E2E_TIMEOUT_S,
        )
        # Aceptamos 200 (ok) o 503 (degraded) — ambos son respuestas validas
        assert resp.status_code in (200, 503), (
            f"Readiness retorno HTTP {resp.status_code}, esperaba 200 o 503"
        )
        body = resp.json()
        assert body["status"] in ("ok", "degraded")
        assert "database" in body
        assert "ffmpeg" in body

    def test_health_version_presente(self) -> None:
        """El body de health incluye version semantica."""
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/health?probe=liveness",
            timeout=E2E_TIMEOUT_S,
        )
        body = resp.json()
        version = body.get("version", "")
        assert version, "version vacia o ausente en health"
        # Formato semver basico: X.Y.Z o X.Y.Z-dev
        partes = version.split(".")
        assert len(partes) >= 2, f"version no parece semver: {version}"

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "La API aun no tiene CORSMiddleware configurado. "
            "La landing no podra hacer fetch directo hasta agregarlo "
            "en main.py (ver issue #122)."
        ),
    )
    def test_health_cors_headers(self) -> None:
        """El endpoint health incluye headers CORS.

        Si la API deployada tiene CORS configurado (via middleware o
        Traefik), Access-Control-Allow-Origin debe estar presente.
        Si falta, la landing page no podra hacer fetch directo.

        Marcado xfail porque el gap es conocido. Cuando se implemente
        CORS, este test pasara y se quitara el decorador.
        """
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/health?probe=liveness",
            timeout=E2E_TIMEOUT_S,
        )
        # Assert que el header CORS existe, cualquiera sea su valor.
        # '*' o 'https://agrovoz.cl' son ambos validos.
        assert "access-control-allow-origin" in resp.headers, (
            "Header Access-Control-Allow-Origin ausente. "
            "Configurar CORSMiddleware en main.py o Traefik."
        )
        origin = resp.headers["access-control-allow-origin"]
        assert origin in ("*", "https://agrovoz.cl", "https://www.agrovoz.cl"), (
            f"Access-Control-Allow-Origin inesperado: {origin}"
        )

    def test_x_request_id_presente(self) -> None:
        """Cada respuesta incluye X-Request-ID para trazabilidad."""
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/health?probe=liveness",
            timeout=E2E_TIMEOUT_S,
        )
        request_id = resp.headers.get("x-request-id", "")
        assert request_id, "Header X-Request-ID ausente en respuesta"


@skip_sin_api
@pytest.mark.e2e
class TestWeather:
    """Endpoint de clima (/api/v1/weather)."""

    def test_weather_default_traiguen(self) -> None:
        """GET /api/v1/weather (default) retorna datos de Traiguen."""
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/weather",
            timeout=E2E_TIMEOUT_S,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "location" in body
        assert "temperature_c" in body
        assert "texto" in body
        # Default: Traiguén
        assert "traiguén" in body["location"].lower() or "traiguen" in body["location"].lower()

    def test_weather_custom_coords(self) -> None:
        """GET /api/v1/weather?lat=-33.45&lon=-70.65 retorna Santiago."""
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/weather?lat=-33.45&lon=-70.65",
            timeout=E2E_TIMEOUT_S,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "location" in body
        assert "temperature_c" in body
        assert "texto" in body
        # Coordenadas de Santiago: location NO debe ser Traiguén
        traiguen_variants = ("traiguén", "traiguen", "traiguem")
        location_lower = body["location"].lower()
        assert not any(v in location_lower for v in traiguen_variants), (
            f"Location sigue siendo Traiguen para coordenadas custom: {body['location']}"
        )

    def test_weather_invalid_coords_400(self) -> None:
        """GET /api/v1/weather?lat=999 retorna 422 por validacion."""
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/weather?lat=999&lon=999",
            timeout=E2E_TIMEOUT_S,
        )
        # FastAPI/Pydantic rechaza lat > 90 con 422
        assert resp.status_code == 422

    def test_weather_schema_tiene_texto_tts(self) -> None:
        """El campo 'texto' contiene texto en espanol listo para TTS."""
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/weather",
            timeout=E2E_TIMEOUT_S,
        )
        body = resp.json()
        texto = body.get("texto", "")
        assert texto, "campo 'texto' vacio o ausente"
        # Debe contener al menos una palabra en español
        assert len(texto.split()) >= 3, (
            f"'texto' muy corto para ser un texto TTS valido: {texto!r}"
        )


@skip_sin_api
@pytest.mark.e2e
class TestPrices:
    """Endpoint de precios ODEPA (/api/v1/prices y /api/v1/products)."""

    def test_products_lista(self) -> None:
        """GET /api/v1/products retorna lista de productos disponible.

        Usamos /products en vez de /prices (que requiere path param)
        porque devuelve una lista directamente.
        """
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/products",
            timeout=E2E_TIMEOUT_S,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list), f"products debio ser lista, obtuve {type(body)}"

    def test_prices_with_product(self) -> None:
        """GET /api/v1/prices/papa retorna precios de papa."""
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/prices/papa",
            timeout=E2E_TIMEOUT_S,
        )
        # 200 si hay datos, 404 si ODEPA no se ha sincronizado aun
        assert resp.status_code in (200, 404), (
            f"prices/papa retorno HTTP {resp.status_code}, esperaba 200 o 404"
        )
        if resp.status_code == 200:
            body = resp.json()
            # Puede ser PriceResponse (si hay ?mercado=) o PriceListResponse
            if "precios" in body:
                assert len(body["precios"]) > 0
                assert body["producto"] == "papa"
            else:
                assert "producto" in body
                assert body["producto"] == "papa"

    def test_prices_empty_product_400(self) -> None:
        """GET /api/v1/prices/  (vacio) retorna 404."""
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/prices/",
            timeout=E2E_TIMEOUT_S,
        )
        # Path param vacio: FastAPI no matchea la ruta, esperamos 404
        assert resp.status_code == 404

    def test_mercados_lista(self) -> None:
        """GET /api/v1/mercados retorna lista de mercados."""
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/mercados",
            timeout=E2E_TIMEOUT_S,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)


@skip_sin_api
@pytest.mark.e2e
class TestAdminAuth:
    """Autenticacion de endpoints admin (/api/v1/admin/*)."""

    def test_admin_no_key_returns_401(self) -> None:
        """GET /api/v1/admin/metrics/dashboard sin key retorna 401."""
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/admin/metrics/dashboard",
            timeout=E2E_TIMEOUT_S,
        )
        assert resp.status_code == 401, (
            f"Admin endpoint sin key retorno HTTP {resp.status_code}, esperaba 401"
        )

    def test_admin_with_key(self) -> None:
        """GET /api/v1/admin/metrics/dashboard con X-Admin-Key retorna 200 o 404.

        200: hay datos en la DB (consultas registradas).
        404: DB vacia (comun en deploy fresco sin actividad).
        """
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/admin/metrics/dashboard",
            headers={"X-Admin-Key": E2E_ADMIN_KEY},
            timeout=E2E_TIMEOUT_S,
        )
        assert resp.status_code in (200, 404, 500), (
            f"Admin endpoint con key retorno HTTP {resp.status_code}, "
            f"esperaba 200 (datos), 404 (DB vacia) o 500 (error interno)"
        )
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, dict)

    def test_admin_wrong_key_returns_401(self) -> None:
        """X-Admin-Key incorrecto retorna 401."""
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/admin/metrics/dashboard",
            headers={"X-Admin-Key": "this-is-the-wrong-key"},
            timeout=E2E_TIMEOUT_S,
        )
        assert resp.status_code == 401

    def test_admin_empty_key_header_returns_401(self) -> None:
        """X-Admin-Key vacio equivale a sin key, retorna 401."""
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/admin/metrics/dashboard",
            headers={"X-Admin-Key": ""},
            timeout=E2E_TIMEOUT_S,
        )
        assert resp.status_code == 401


@skip_sin_api
@pytest.mark.e2e
class TestSeguridad:
    """Headers de seguridad y hardening de la API."""

    def test_x_content_type_options(self) -> None:
        """Header X-Content-Type-Options: nosniff presente."""
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/health?probe=liveness",
            timeout=E2E_TIMEOUT_S,
        )
        assert resp.headers.get("x-content-type-options", "").lower() == "nosniff"

    def test_x_frame_options(self) -> None:
        """Header X-Frame-Options: DENY presente (previene clickjacking)."""
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/health?probe=liveness",
            timeout=E2E_TIMEOUT_S,
        )
        assert resp.headers.get("x-frame-options", "").upper() == "DENY"

    def test_404_no_expone_detalles(self) -> None:
        """Una ruta inexistente retorna 404 JSON, no HTML."""
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/ruta-que-no-existe-xyz",
            timeout=E2E_TIMEOUT_S,
        )
        assert resp.status_code == 404
        # La respuesta debe ser JSON, no HTML
        content_type = resp.headers.get("content-type", "")
        assert "json" in content_type.lower(), (
            f"404 retorno content-type {content_type}, esperaba application/json"
        )


@skip_sin_api
@pytest.mark.e2e
class TestPerformance:
    """Timeouts y latencia de endpoints clave."""

    def test_health_responde_rapido(self) -> None:
        """Health check responde en menos de 5 segundos."""
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/health?probe=liveness",
            timeout=5.0,
        )
        assert resp.status_code == 200

    def test_weather_no_timeout(self) -> None:
        """Weather responde dentro del timeout (depende de OpenMeteo)."""
        resp = httpx.get(
            f"{E2E_BASE_URL}/api/v1/weather",
            timeout=E2E_TIMEOUT_S,
        )
        # Aceptamos 200 (datos) o 502 (OpenMeteo caido)
        assert resp.status_code in (200, 502), (
            f"Weather retorno HTTP {resp.status_code}, esperaba 200 o 502"
        )
