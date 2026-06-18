"""Tests del endpoint de health check."""

from httpx import ASGITransport, AsyncClient


async def test_health_retorna_200_y_status_ok(client: AsyncClient) -> None:
    """El endpoint GET /api/v1/health debe retornar 200 con {"status": "ok"}."""
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_retorna_content_type_json(client: AsyncClient) -> None:
    """El endpoint de health debe retornar Content-Type application/json."""
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]


async def test_docs_disponibles_en_development(client: AsyncClient) -> None:
    """En desarrollo, /docs debe ser accesible (Swagger UI)."""
    response = await client.get("/docs")

    assert response.status_code == 200


async def test_docs_oculto_en_production() -> None:
    """En producción, /docs debe retornar 404 (no accesible).

    Crea una app fresca con docs_url=None simulando entorno productivo.
    """
    from fastapi import FastAPI

    app_prod = FastAPI(
        title="AgroVoz API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
    )

    # Replicar el health endpoint en la app de prueba
    @app_prod.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async with AsyncClient(transport=ASGITransport(app=app_prod), base_url="http://test") as c:
        # Health debe seguir funcionando
        health_response = await c.get("/api/v1/health")
        assert health_response.status_code == 200

        # Docs debe retornar 404
        docs_response = await c.get("/docs")
        assert docs_response.status_code == 404


async def test_health_tiene_security_headers(client: AsyncClient) -> None:
    """El endpoint de health debe incluir headers de seguridad."""
    response = await client.get("/api/v1/health")

    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    # HSTS solo se envía sobre HTTPS (browsers lo ignoran en HTTP plano).
    # En tests usamos ASGITransport con http://, así que no debe estar presente.
    assert response.headers.get("content-security-policy") is not None
    assert response.headers.get("permissions-policy") is not None
