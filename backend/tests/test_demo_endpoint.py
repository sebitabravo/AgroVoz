"""Tests para el endpoint demo de la landing page.

Issue #119 — cubre happy path, rate limiting y flag de deshabilitacion.
No requiere modelos reales: todos los servicios se mockean.
"""

import pytest
from httpx import AsyncClient

from app.core import config
from app.core.rate_limiter import SlidingWindowRateLimiter


@pytest.fixture
def demo_limiter() -> SlidingWindowRateLimiter:
    """Retorna el limiter de demo y lo limpia al finalizar."""
    from app.core.rate_limiter import _demo_limiter

    _demo_limiter.reset()
    yield _demo_limiter
    _demo_limiter.reset()


@pytest.fixture
def enable_demo_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Habilita el endpoint demo para tests que lo necesiten."""
    monkeypatch.setattr(config.settings, "demo_endpoint_enabled", True)


@pytest.fixture
def mock_demo_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mockea process_demo_request a nivel del router (donde esta importada)."""
    from app.schemas.demo import DemoRespuestaResponse

    async def fake_process(_request: object) -> DemoRespuestaResponse:
        return DemoRespuestaResponse(
            texto="La papa esta a 480 pesos el kilo en Lo Valledor.",
            audio_base64="ZmFrZS1hdWRpbw==",
            intent="precio",
            latency_ms=1234,
        )

    monkeypatch.setattr("app.api.demo.process_demo_request", fake_process)


@pytest.mark.asyncio
async def test_demo_preguntar_retorna_respuesta_texto_y_audio(
    client: AsyncClient,
    enable_demo_endpoint: None,
    mock_demo_process: None,
    demo_limiter: SlidingWindowRateLimiter,
) -> None:
    """POST /demo/preguntar retorna texto, audio base64, intent y latencia."""
    response = await client.post(
        "/api/v1/demo/preguntar",
        json={"texto": "a cuanto esta la papa?"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "480" in data["texto"]
    assert data["audio_base64"] == "ZmFrZS1hdWRpbw=="
    assert data["intent"] == "precio"
    assert data["latency_ms"] == 1234


@pytest.mark.asyncio
async def test_demo_preguntar_acepta_texto_vacio_con_audio(
    client: AsyncClient,
    enable_demo_endpoint: None,
    monkeypatch: pytest.MonkeyPatch,
    demo_limiter: SlidingWindowRateLimiter,
) -> None:
    """El endpoint acepta audio base64 cuando el texto esta vacio."""
    from app.schemas.demo import DemoRespuestaResponse

    async def fake_process_audio(_request: object) -> DemoRespuestaResponse:
        return DemoRespuestaResponse(
            texto="En Traiguen habra lluvia manana.",
            audio_base64="ZmFrZS1hdWRpbw==",
            intent="clima",
            latency_ms=900,
        )

    monkeypatch.setattr("app.api.demo.process_demo_request", fake_process_audio)

    response = await client.post(
        "/api/v1/demo/preguntar",
        json={"texto": "", "audio_base64": "bXktZmFrZS1hdWRpbw=="},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "clima"


@pytest.mark.asyncio
async def test_demo_preguntar_rechaza_consulta_vacia(
    client: AsyncClient,
    enable_demo_endpoint: None,
    demo_limiter: SlidingWindowRateLimiter,
) -> None:
    """POST /demo/preguntar retorna 400 si faltan texto y audio."""
    response = await client.post(
        "/api/v1/demo/preguntar",
        json={"texto": ""},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_demo_rate_limit_bloquea_requests_excedidas(
    client: AsyncClient,
    enable_demo_endpoint: None,
    mock_demo_process: None,
    monkeypatch: pytest.MonkeyPatch,
    demo_limiter: SlidingWindowRateLimiter,
) -> None:
    """El endpoint bloquea con 429 cuando se excede demo_rate_limit_per_minute."""
    monkeypatch.setattr(config.settings, "demo_rate_limit_per_minute", 2)

    for _ in range(2):
        response = await client.post(
            "/api/v1/demo/preguntar",
            json={"texto": "a cuanto esta la papa?"},
        )
        assert response.status_code == 200

    response = await client.post(
        "/api/v1/demo/preguntar",
        json={"texto": "a cuanto esta la papa?"},
    )
    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert int(response.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_demo_preguntar_rechaza_cuando_disabled(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    demo_limiter: SlidingWindowRateLimiter,
) -> None:
    """El endpoint retorna 503 cuando DEMO_ENDPOINT_ENABLED es False."""
    monkeypatch.setattr(config.settings, "demo_endpoint_enabled", False)

    response = await client.post(
        "/api/v1/demo/preguntar",
        json={"texto": "a cuanto esta la papa?"},
    )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_demo_status_rechaza_cuando_disabled(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    demo_limiter: SlidingWindowRateLimiter,
) -> None:
    """GET /demo/status retorna 503 cuando DEMO_ENDPOINT_ENABLED es False."""
    monkeypatch.setattr(config.settings, "demo_endpoint_enabled", False)

    response = await client.get("/api/v1/demo/status")

    assert response.status_code == 503
