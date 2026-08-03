"""Tests de integración del endpoint POST /api/v1/vision/identify."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import settings
from app.services.panel_service import generate_panel_token
from app.services.vision_service import VisionAlternative, VisionIdentification, VisionService

_PHONE_HASH = "a" * 64


@pytest.fixture(autouse=True)
def _enable_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enciende el gate para probar el contrato HTTP."""
    monkeypatch.setattr(settings, "vision_enabled", True)
    monkeypatch.setattr(settings, "vision_max_image_bytes", 5 * 1024 * 1024)
    monkeypatch.setattr(settings, "panel_link_secret", SecretStr("x" * 32))


def _panel_token() -> str:
    """Genera la credencial corta que autoriza el endpoint del panel."""
    return generate_panel_token(_PHONE_HASH)


def _identified_result() -> VisionIdentification:
    """Resultado fijo para no cargar un modelo ni depender de una imagen real."""
    return VisionIdentification(
        status="identified",
        classification="Tizón tardío de la papa",
        detected_label="Potato___Late_blight",
        confidence=0.94,
        alternatives=(VisionAlternative(label="Potato___Late_blight", confidence=0.94),),
        message="Identificación orientativa: Tizón tardío de la papa (94%).",
        rule="Regla INIA vigente.",
        source="INIA Chile — Enfermedades de la papa: Tizón tardío",
        source_url="https://enfermedadespapa.inia.cl/tizonTardio.php",
        verified_on="2026-07-30",
    )


@pytest.mark.asyncio
async def test_endpoint_recibe_imagen_y_devuelve_clasificacion(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El contrato devuelve nombre, confianza y cita para la PWA."""
    seen: list[tuple[bytes, str]] = []

    def fake_identify(self: VisionService, image_bytes: bytes, cultivo: str = "") -> VisionIdentification:
        seen.append((image_bytes, cultivo))
        return _identified_result()

    monkeypatch.setattr(VisionService, "identify", fake_identify)

    response = await client.post(
        f"/api/v1/vision/identify?token={_panel_token()}&cultivo=papa",
        files={"image": ("hoja.jpg", b"jpeg-de-prueba", "image/jpeg")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["classification"] == "Tizón tardío de la papa"
    assert data["confidence"] == 0.94
    assert data["source"].startswith("INIA")
    assert data["source_url"] == "https://enfermedadespapa.inia.cl/tizonTardio.php"
    assert seen == [(b"jpeg-de-prueba", "papa")]


@pytest.mark.asyncio
async def test_endpoint_rechaza_formato_no_imagen(client: AsyncClient) -> None:
    """El endpoint no procesa archivos que no son imágenes permitidas."""
    response = await client.post(
        f"/api/v1/vision/identify?token={_panel_token()}",
        files={"image": ("hoja.txt", b"texto", "text/plain")},
    )

    assert response.status_code == 415


@pytest.mark.asyncio
async def test_endpoint_gate_apagado_retorna_503(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """La capacidad opcional queda cerrada por defecto."""
    monkeypatch.setattr(settings, "vision_enabled", False)

    response = await client.post(
        f"/api/v1/vision/identify?token={_panel_token()}",
        files={"image": ("hoja.jpg", b"jpeg-de-prueba", "image/jpeg")},
    )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_endpoint_rechaza_peticion_sin_token_de_panel(client: AsyncClient) -> None:
    """La inferencia costosa no queda expuesta como endpoint público."""
    response = await client.post(
        "/api/v1/vision/identify",
        files={"image": ("hoja.jpg", b"jpeg-de-prueba", "image/jpeg")},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_endpoint_rechaza_token_vencido(client: AsyncClient) -> None:
    """Un link vencido no permite seguir consumiendo inferencias ONNX."""
    expired = generate_panel_token(_PHONE_HASH, now=1)

    response = await client.post(
        f"/api/v1/vision/identify?token={expired}",
        files={"image": ("hoja.jpg", b"jpeg-de-prueba", "image/jpeg")},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_endpoint_limita_tamano_de_imagen(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """El límite de bytes evita consumir la RAM del VPS con una carga abusiva."""
    monkeypatch.setattr(settings, "vision_max_image_bytes", 4)

    response = await client.post(
        f"/api/v1/vision/identify?token={_panel_token()}",
        files={"image": ("hoja.jpg", b"12345", "image/jpeg")},
    )

    assert response.status_code == 413
