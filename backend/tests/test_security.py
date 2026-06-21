"""Tests de los middlewares de seguridad."""

import time
import warnings

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from starlette.responses import Response as StarletteResponse

from app.core.config import Settings, settings
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

    Verifica que dispatch() aísla IPs correctamente: una IP que excede
    el límite no bloquea a otras IPs. Usa Request con scopes mock
    para simular distintas IPs sin depender de ASGITransport.
    """
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)

    app_test = FastAPI()
    middleware = RateLimitMiddleware(app_test)

    # Mock call_next: simula el resto de la cadena de middlewares + router.
    # Retorna 200 OK sin leer el body del request.
    async def call_next(request: Request) -> StarletteResponse:
        return StarletteResponse(
            content='{"status":"ok"}',
            status_code=200,
            media_type="application/json",
        )

    # Scope base para construir Request con distintas IPs.
    # client[0] es la IP que dispatch() extrae vía request.client.host.
    base_scope: dict[str, object] = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/health",
        "scheme": "http",
        "server": ("testserver", 80),
        "headers": [],
    }

    # IP 10.0.0.1: 2 requests (llega al límite pero no lo excede)
    scope_ip1 = {**base_scope, "client": ("10.0.0.1", 12345)}
    for _ in range(2):
        request_ip1 = Request(scope_ip1)
        response = await middleware.dispatch(request_ip1, call_next)
        assert response.status_code == 200

    # 3er request de 10.0.0.1: debe ser bloqueada (429)
    request_ip1 = Request(scope_ip1)
    response = await middleware.dispatch(request_ip1, call_next)
    assert response.status_code == 429
    # response.body puede ser bytes | memoryview[int].
    # bytes() convierte ambos a bytes para el assert.
    datos = bytes(response.body).decode()
    assert "Demasiadas solicitudes" in datos

    # IP 10.0.0.2: contador independiente — no debe ser bloqueada
    scope_ip2 = {**base_scope, "client": ("10.0.0.2", 54321)}
    request_ip2 = Request(scope_ip2)
    response = await middleware.dispatch(request_ip2, call_next)
    assert response.status_code == 200

    # IP 10.0.0.2: 2do request (límite es 2) — aún debe pasar
    response = await middleware.dispatch(request_ip2, call_next)
    assert response.status_code == 200

    # IP 10.0.0.2: 3er request — ahora sí debe ser bloqueada
    response = await middleware.dispatch(request_ip2, call_next)
    assert response.status_code == 429

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


def test_phone_hash_pepper_dev_con_warning() -> None:
    """En development, el pepper default emite RuntimeWarning (avisa que debe cambiarse)."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        s = Settings(app_env="development", phone_hash_pepper="agrovoz-dev-pepper")
        s.validate_pepper_not_default()
        assert len(w) == 1
        assert issubclass(w[0].category, RuntimeWarning)
        assert "PHONE_HASH_PEPPER" in str(w[0].message)


def test_phone_hash_pepper_vacio_prod_lanza_error() -> None:
    """Pepper vacío en producción lanza ValueError (Docker sin variable)."""
    s = Settings(app_env="production", phone_hash_pepper="")
    with pytest.raises(ValueError, match="PHONE_HASH_PEPPER está vacío"):
        s.validate_pepper_not_default()


def test_phone_hash_pepper_vacio_test_emite_warning() -> None:
    """Pepper vacío en test/CI emite RuntimeWarning."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        s = Settings(app_env="test", phone_hash_pepper="")
        s.validate_pepper_not_default()
        assert len(w) == 1
        assert issubclass(w[0].category, RuntimeWarning)
        assert "PHONE_HASH_PEPPER está vacío" in str(w[0].message)


def test_phone_hash_pepper_prod_sin_setear_lanza_error() -> None:
    """En production, el pepper default lanza ValueError (bloquea arranque)."""
    s = Settings(app_env="production", phone_hash_pepper="agrovoz-dev-pepper")
    with pytest.raises(ValueError, match="PHONE_HASH_PEPPER"):
        s.validate_pepper_not_default()


def test_phone_hash_pepper_test_sin_setear_emite_warning() -> None:
    """En test/CI, el pepper default emite RuntimeWarning (no bloquea tests)."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        s = Settings(app_env="test", phone_hash_pepper="agrovoz-dev-pepper")
        s.validate_pepper_not_default()
        assert len(w) == 1
        assert issubclass(w[0].category, RuntimeWarning)
        assert "PHONE_HASH_PEPPER" in str(w[0].message)


def test_phone_hash_pepper_prod_personalizado_sin_error() -> None:
    """En production con pepper propio, no lanza error ni warning."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        s = Settings(
            app_env="production",
            phone_hash_pepper="pepper-secreto-produccion-real",
        )
        s.validate_pepper_not_default()
        assert len(w) == 0
