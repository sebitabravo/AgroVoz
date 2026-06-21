"""Tests del endpoint de health check."""

import pytest
from httpx import ASGITransport, AsyncClient


async def test_health_liveness_retorna_200_y_status_ok(client: AsyncClient) -> None:
    """GET /api/v1/health?probe=liveness debe retornar 200 con status=ok y version presente."""
    response = await client.get("/api/v1/health?probe=liveness")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert len(data["version"]) > 0


async def test_health_readiness_ffmpeg_disponible(
    monkeypatch: pytest.MonkeyPatch, client: AsyncClient
) -> None:
    """Readiness con ffmpeg disponible debe retornar 200, status=ok, ffmpeg=available.

    Usa monkeypatch para forzar el path deterministicamente, sin depender
    de si ffmpeg está instalado en el entorno (CI vs dev local).
    """
    monkeypatch.setattr("app.api.health._check_ffmpeg", lambda: True)

    response = await client.get("/api/v1/health?probe=readiness")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"
    assert data["ffmpeg"] == "available"
    assert "version" in data


async def test_health_readiness_ffmpeg_faltante(
    monkeypatch: pytest.MonkeyPatch, client: AsyncClient
) -> None:
    """Readiness con ffmpeg faltante debe retornar 503, status=degraded, ffmpeg=missing.

    Usa monkeypatch para forzar el path deterministicamente.
    """
    monkeypatch.setattr("app.api.health._check_ffmpeg", lambda: False)

    response = await client.get("/api/v1/health?probe=readiness")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "degraded"
    assert data["database"] == "connected"
    assert data["ffmpeg"] == "missing"
    assert "version" in data


async def test_health_readiness_degraded_db_down(monkeypatch: pytest.MonkeyPatch, client: AsyncClient) -> None:
    """Cuando la DB no responde, readiness debe retornar 503 con status=degraded y database=unavailable."""
    # Simular DB caída
    monkeypatch.setattr("app.api.health._check_db", lambda: False)

    response = await client.get("/api/v1/health?probe=readiness")
    assert response.status_code == 503

    data = response.json()
    assert data["status"] == "degraded"
    assert data["database"] == "unavailable"
    assert "version" in data


async def test_health_retorna_content_type_json(client: AsyncClient) -> None:
    """El endpoint de health debe retornar Content-Type application/json.

    Usa probe=liveness para evitar depender de ffmpeg/DB en CI.
    """
    response = await client.get("/api/v1/health?probe=liveness")

    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]


async def test_docs_disponibles_en_development(client: AsyncClient) -> None:
    """En desarrollo, /docs debe ser accesible (Swagger UI)."""
    response = await client.get("/docs")

    assert response.status_code == 200


async def test_docs_oculto_en_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """/docs debe retornar 404 cuando app_env != development en la app real."""
    from importlib import reload

    import app.main as app_main
    from app.core import config

    original_env = config.settings.app_env

    try:
        monkeypatch.setattr(config.settings, "app_env", "production")
        reload(app_main)

        # La app real con app_env="production" debe tener docs_url=None
        assert app_main.app.docs_url is None

        async with AsyncClient(
            transport=ASGITransport(app=app_main.app), base_url="http://testserver"
        ) as c:
            # Health debe seguir funcionando en cualquier entorno.
            # Usamos probe=liveness para no depender de ffmpeg/DB en CI.
            health_response = await c.get("/api/v1/health?probe=liveness")
            assert health_response.status_code == 200

            # /docs debe retornar 404
            docs_response = await c.get("/docs")
            assert docs_response.status_code == 404
    finally:
        monkeypatch.setattr(config.settings, "app_env", original_env)
        reload(app_main)


async def test_health_tiene_security_headers(client: AsyncClient) -> None:
    """El endpoint de health debe incluir headers de seguridad.

    Usa probe=liveness para evitar depender de ffmpeg/DB en CI.
    """
    response = await client.get("/api/v1/health?probe=liveness")

    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    # HSTS solo se envía sobre HTTPS (browsers lo ignoran en HTTP plano).
    # En tests usamos ASGITransport con http://, así que no debe estar presente.
    assert response.headers.get("content-security-policy") is not None
    assert response.headers.get("permissions-policy") is not None
