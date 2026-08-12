"""Tests para el endpoint demo de la landing page.

Issue #119 — cubre happy path, rate limiting y flag de deshabilitacion.
No requiere modelos reales: todos los servicios se mockean.
"""

import logging

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.core import config
from app.core.rate_limiter import SlidingWindowRateLimiter


def test_demo_provider_y_deadline_global_por_defecto_y_valida_opciones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings prioriza OpenRouter y conserva un deadline permisivo de 2 s."""
    monkeypatch.delenv("LLM_PRIMARY_PROVIDER", raising=False)
    monkeypatch.delenv("OPENROUTER_PRIMARY_TIMEOUT_SECONDS", raising=False)
    settings = config.Settings(_env_file=None)

    assert settings.llm_primary_provider == "openrouter"
    assert settings.openrouter_primary_timeout_seconds == 2.0
    with pytest.raises(ValidationError):
        config.Settings(_env_file=None, llm_primary_provider="otro")


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


@pytest.mark.asyncio
async def test_demo_no_registra_consulta_ni_error_llm(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Una caída del LLM no expone la consulta ni el mensaje de excepción."""
    from app.services.demo_service import _generate_demo_response

    consulta = "SECRETO-DEMO mi dato privado"
    caplog.set_level(logging.INFO, logger="app.services.demo_service")

    async def answer_falla(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError(f"error con {consulta} teléfono 56912345678")

    monkeypatch.setattr("app.services.llm_service.answer", answer_falla)

    response, intent = await _generate_demo_response(consulta)

    assert "problema" in response
    assert intent == "desconocido"
    assert "SECRETO-DEMO" not in caplog.text
    assert "dato privado" not in caplog.text
    assert "56912345678" not in caplog.text


@pytest.mark.asyncio
async def test_demo_openrouter_no_llama_al_responder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El proveedor remoto de demo no cae al Qwen local."""
    from app.services.demo_service import _generate_demo_response

    monkeypatch.setattr(config.settings, "llm_primary_provider", "openrouter")
    monkeypatch.setattr(config.settings, "openrouter_api_key", "sk-or-test")
    captured: dict[str, object] = {}

    async def fake_openrouter(*args: object, **kwargs: object) -> str:
        captured.update(kwargs)
        return "Respuesta remota de prueba."

    async def local_must_not_run(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("la demo remota no debe invocar answer() local")

    monkeypatch.setattr("app.services.llm_service.answer_via_openrouter", fake_openrouter)
    monkeypatch.setattr("app.services.llm_service.answer", local_must_not_run)
    monkeypatch.setattr(config.settings, "openrouter_max_output_tokens", 96)
    monkeypatch.setattr(
        "app.services.demo_service.AgroVozPipeline._puede_usar_fast_path",
        staticmethod(lambda *_args: False),
    )

    response, intent = await _generate_demo_response("cuéntame algo de mi cultivo")

    assert response == "Respuesta remota de prueba."
    assert intent == "desconocido"
    assert captured["max_tokens"] == 96


@pytest.mark.asyncio
async def test_demo_openrouter_fallido_saltea_al_llm_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La caída remota pasa al Qwen local como segundo proveedor."""
    from app.services.demo_service import _generate_demo_response

    monkeypatch.setattr(config.settings, "llm_primary_provider", "openrouter")
    monkeypatch.setattr(config.settings, "openrouter_api_key", "sk-or-test")

    async def remote_unavailable(*_args: object, **_kwargs: object) -> None:
        return None

    async def local_response(*_args: object, **_kwargs: object) -> str:
        return "Respuesta local de respaldo."

    monkeypatch.setattr("app.services.llm_service.answer_via_openrouter", remote_unavailable)
    monkeypatch.setattr("app.services.llm_service.answer", local_response)
    monkeypatch.setattr(
        "app.services.demo_service.AgroVozPipeline._puede_usar_fast_path",
        staticmethod(lambda *_args: False),
    )

    response, intent = await _generate_demo_response("cuéntame algo de mi cultivo")

    assert response == "Respuesta local de respaldo."
    assert intent == "desconocido"


@pytest.mark.asyncio
async def test_demo_fast_path_omite_openrouter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una consulta rápida mantiene el dato determinístico y no usa API."""
    from app.services.demo_service import _generate_demo_response

    monkeypatch.setattr(config.settings, "llm_primary_provider", "openrouter")

    async def remote_must_not_run(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("el fast-path no debe consultar OpenRouter")

    async def deterministic_response(*_args: object, **_kwargs: object) -> str:
        return "La papa está a 600 pesos el kilo según ODEPA."

    monkeypatch.setattr("app.services.llm_service.answer_via_openrouter", remote_must_not_run)
    monkeypatch.setattr(
        "app.services.demo_service.AgroVozPipeline._puede_usar_fast_path",
        staticmethod(lambda *_args: True),
    )
    monkeypatch.setattr("app.services.demo_service._force_keyword_tool", deterministic_response)

    response, intent = await _generate_demo_response("a cuanto esta la papa")

    assert "600" in response
    assert intent == "precio"


@pytest.mark.asyncio
async def test_demo_saludo_usa_copy_chileno_y_acentos() -> None:
    """El saludo directo de la demo no usa voseo rioplatense ni texto mutilado."""
    from app.services.demo_service import _generate_demo_response

    response, intent = await _generate_demo_response("hola")

    assert intent == "saludo"
    assert response == (
        "¡Hola! Pregúntame por el precio de algún producto o por el clima de Traiguén."
    )
