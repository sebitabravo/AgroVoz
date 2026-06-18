"""Tests de los middlewares de seguridad."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.security import RateLimitMiddleware


async def test_rate_limit_bloquea_despues_de_n_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Después de rate_limit_per_minute requests, la siguiente debe retornar 429.

    Usa una app fresca con RateLimitMiddleware para evitar contaminación
    del estado compartido entre tests.
    """
    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)

    app_test = FastAPI()
    app_test.add_middleware(RateLimitMiddleware)

    @app_test.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async with AsyncClient(
        transport=ASGITransport(app=app_test), base_url="http://test"
    ) as c:
        # Las primeras 3 requests deben pasar (200 OK)
        for _ in range(3):
            response = await c.get("/api/v1/health")
            assert response.status_code == 200

        # La 4ª request debe ser bloqueada (429 Too Many Requests)
        response = await c.get("/api/v1/health")
        assert response.status_code == 429
        data = response.json()
        assert "detail" in data
        assert "Demasiadas solicitudes" in data["detail"]
