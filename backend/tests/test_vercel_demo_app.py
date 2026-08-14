"""Tests de la app slim para el deploy público en Vercel (app/vercel_demo.py).

Verifica el contrato que hace posible el deploy free: sin los ~2,5 GB de
modelos locales instalados, sin admin/webhooks/panel montados, y sirviendo
la demo pública contra una DB de solo lectura. Ejecutar en aislamiento
(`uv run pytest tests/test_vercel_demo_app.py -v`) para que la verificación
de sys.modules sea representativa del bundle real de Vercel — dentro de la
suite completa, otro test puede haber importado un paquete pesado antes.
"""

import subprocess
import sys
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_HEAVY_MODULES = ("llama_cpp", "faster_whisper", "piper", "onnxruntime", "PIL", "reportlab")


def test_import_no_carga_dependencias_pesadas() -> None:
    """El entrypoint slim no debe traer al proceso ninguna dependencia de [heavy].

    Esas seis librerías son las que exceden el límite de bundle de Vercel
    (500 MB); si alguna aparece tras importar vercel_demo, algo del código
    slim dejó de ser lazy y el deploy dejaría de caber.

    Corre en un subproceso limpio a propósito: dentro del mismo proceso de
    pytest, `tests/conftest.py` ya hizo `from app.main import app`, y
    `app.main` SÍ monta el router de visión (onnxruntime/PIL). Medir
    sys.modules en ese proceso daría un falso positivo — la pregunta real es
    si `app.vercel_demo`, importado solo, arrastra algo pesado.
    """
    probe = (
        "import sys; import app.vercel_demo; "
        f"loaded = [m for m in {_HEAVY_MODULES!r} if m in sys.modules]; "
        "sys.exit(1) if loaded else sys.exit(0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"vercel_demo.py cargó una dependencia pesada en un import limpio.\n{result.stderr}"
    )


def test_no_monta_admin_ni_webhooks_ni_panel() -> None:
    """La app slim expone solo el subconjunto público documentado."""
    from app.vercel_demo import app

    paths = set(app.openapi()["paths"].keys())
    for forbidden in ("/admin", "/api/v1/webhook/whatsapp", "/panel", "/agronomo", "/mcp"):
        assert not any(p.startswith(forbidden) for p in paths), f"ruta prohibida montada: {forbidden}"

    assert "/api/v1/demo/preguntar" in paths
    assert "/api/v1/health" in paths


@pytest_asyncio.fixture
async def slim_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AsyncClient, None]:
    """Cliente ASGI contra la app slim, con DB temporal aislada."""
    from app.core.database import get_db as original_get_db
    from app.vercel_demo import app

    db_path = tmp_path / "vercel_demo_test.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    from app.core.database import Base
    from app.models import DataFact, DataSource, DirectorioAgricola, OdepaPrice  # noqa: F401

    Base.metadata.create_all(test_engine)
    test_session_local = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_db() -> Generator[Session, None, None]:
        db = test_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[original_get_db] = override_get_db
    monkeypatch.setattr("app.core.config.settings.demo_endpoint_enabled", True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()
    test_engine.dispose()


@pytest.mark.asyncio
async def test_health_responde_ok_sin_chequear_ffmpeg(slim_client: AsyncClient) -> None:
    """El health slim no reporta 'degraded' por falta de ffmpeg — nunca lo usa."""
    response = await slim_client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "ffmpeg" not in body


@pytest.mark.asyncio
async def test_demo_preguntar_responde_sin_modelos_locales(
    slim_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El chat público responde 200 aunque no exista Qwen/Whisper/Piper.

    Fuerza el camino sin LLM (fast-path por clima) para no depender de red
    hacia OpenRouter en el test; la garantía que importa acá es que la app
    slim no intenta importar los servicios pesados para contestar.
    """
    from app.schemas.demo import DemoRespuestaResponse

    async def fake_process(_request: object) -> DemoRespuestaResponse:
        return DemoRespuestaResponse(
            texto="Hace 18°C en Traiguén.", audio_base64="", intent="clima", latency_ms=42
        )

    monkeypatch.setattr("app.api.demo.process_demo_request", fake_process)

    response = await slim_client.post(
        "/api/v1/demo/preguntar", json={"texto": "¿Qué temperatura hay en Traiguén?"}
    )

    assert response.status_code == 200
    assert response.json()["texto"] == "Hace 18°C en Traiguén."
