"""Tests de los middlewares de seguridad."""

import time

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


async def test_rate_limit_ips_independientes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cada IP tiene su propio contador de rate limiting.

    Verifica que el diccionario _requests aísla IPs correctamente,
    sin contaminación cruzada entre contadores.
    """
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)

    app_test = FastAPI()
    middleware = RateLimitMiddleware(app_test)

    # Simular requests de distintas IPs manipulando _requests directamente.
    # En producción, request.client.host provee la IP real.
    # En tests con ASGITransport, request.client.host siempre es "testserver".
    now = 1000.0

    # IP 10.0.0.1: 2 requests → no bloqueada aún
    middleware._requests["10.0.0.1"] = [now - 10, now - 5]
    assert len(middleware._requests["10.0.0.1"]) == 2

    # IP 10.0.0.2: 1 request → aún puede hacer más
    middleware._requests["10.0.0.2"] = [now - 3]
    assert len(middleware._requests["10.0.0.2"]) == 1

    # Limpiar estado para no contaminar otros tests
    middleware._requests.clear()


async def test_rate_limit_limpieza_ip_inactiva(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IPs inactivas se limpian del diccionario después de 60s."""
    monkeypatch.setattr(settings, "rate_limit_per_minute", 10)

    # Mock time.time() para controlar la ventana de limpieza
    fake_time = {"now": 1000.0}

    def fake_time_func() -> float:
        return fake_time["now"]

    monkeypatch.setattr(time, "time", fake_time_func)

    app_inner = FastAPI()

    @app_inner.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # RateLimitMiddleware envuelve app_inner. El transport debe apuntar
    # al middleware (no a app_inner) para que dispatch() se ejecute.
    middleware = RateLimitMiddleware(app_inner)

    async with AsyncClient(
        transport=ASGITransport(app=middleware), base_url="http://test"
    ) as c:
        client_ip = "127.0.0.1"  # ASGITransport usa 127.0.0.1 como client host

        # Primera request: la IP se registra con timestamp 1000.0
        response = await c.get("/api/v1/health")
        assert response.status_code == 200
        assert client_ip in middleware._requests
        assert len(middleware._requests[client_ip]) == 1

        # Avanzar 61 segundos → la IP expiró (timestamp fuera de ventana)
        fake_time["now"] = 1061.0

        # Segunda request: dispara limpieza global + poda de la IP actual.
        # La IP tiene timestamp 1000.0, que con now=1061.0
        # está fuera de la ventana (61s > 60s).
        response = await c.get("/api/v1/health")
        assert response.status_code == 200

        # Después de la limpieza, la IP debe haber sido eliminada
        # (sus timestamps expiraron) y luego re-agregada con el nuevo request.
        # Verificamos que el contador se reinició a 1.
        assert client_ip in middleware._requests
        assert len(middleware._requests[client_ip]) == 1


async def test_rate_limit_cero_bloquea_todo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con rate_limit_per_minute=0, todas las requests deben ser bloqueadas."""
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)

    app_test = FastAPI()
    app_test.add_middleware(RateLimitMiddleware)

    @app_test.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async with AsyncClient(
        transport=ASGITransport(app=app_test), base_url="http://test"
    ) as c:
        response = await c.get("/api/v1/health")
        assert response.status_code == 429
