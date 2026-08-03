"""Tests de integración del endpoint de visión para el panel PWA."""

from __future__ import annotations

import base64
import io
from collections.abc import Generator

import pytest
from httpx import AsyncClient
from PIL import Image

from app import main as app_main
from app.api import vision as vision_api
from app.core.config import settings
from app.services.vision_service import (
    VisionIdentification,
    VisionImageError,
    VisionInferenceError,
    VisionModelUnavailableError,
    VisionPrediction,
)


def _image_bytes() -> bytes:
    """Genera una imagen pequeña sin leer archivos externos ni usar red."""
    image = Image.new("RGB", (8, 4), color=(80, 120, 40))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _StubVisionService:
    """Servicio inyectable para no cargar un modelo ONNX en la suite."""

    def __init__(self, result: VisionIdentification | Exception) -> None:
        self.result = result
        self.received: bytes | None = None

    def identify(self, image_bytes: bytes) -> VisionIdentification:
        """Retorna el resultado preparado o reproduce una falla controlada."""
        self.received = image_bytes
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _identification(confidence: float = 0.94) -> VisionIdentification:
    """Construye una identificación estable para las pruebas HTTP."""
    prediction = VisionPrediction(
        label="Papa___tizon_tardio",
        confidence=confidence,
        crop="Papa",
        disease="tizon tardio",
        rank=1,
    )
    high_confidence = confidence >= settings.vision_confidence_threshold
    return VisionIdentification(
        prediction=prediction,
        response=(
            "Identificación visual preliminar."
            if high_confidence
            else "No pude identificar la plaga con suficiente certeza."
        ),
        rule=(
            "Según INIA. Fuente verificada el 30/07/2026: "
            "https://enfermedadespapa.inia.cl/tizonTardio.php"
            if high_confidence
            else ""
        ),
        annotated_image=b"annotated-image",
    )


@pytest.fixture
def service_override() -> Generator[_StubVisionService, None, None]:
    """Inyecta un clasificador determinista y deja limpio el estado global."""
    service = _StubVisionService(_identification())
    app_main.app.dependency_overrides[vision_api.get_vision_service] = lambda: service  # type: ignore[assignment]
    try:
        yield service
    finally:
        app_main.app.dependency_overrides.pop(vision_api.get_vision_service, None)


@pytest.mark.asyncio
async def test_identify_vision_retorna_clasificacion_y_fuente(
    client: AsyncClient,
    service_override: _StubVisionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una imagen válida retorna confianza, cita INIA y anotación visual."""
    monkeypatch.setattr(settings, "vision_enabled", True)

    response = await client.post(
        "/api/v1/vision/identify",
        files={"image": ("captura.jpg", _image_bytes(), "image/jpeg")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["enfermedad"] == "tizon tardio"
    assert payload["confianza"] == pytest.approx(0.94)
    assert payload["identificada"] is True
    assert payload["fuente_inia"].startswith("Según INIA")
    assert payload["fuente_url"].startswith("https://enfermedadespapa.inia.cl/")
    assert payload["fecha_fuente"] == "30/07/2026"
    assert base64.b64decode(payload["imagen_anotada"].split(",", 1)[1]) == b"annotated-image"
    assert service_override.received == _image_bytes()


@pytest.mark.asyncio
async def test_identify_vision_gate_apagado_retorna_503(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El endpoint queda cerrado hasta provisionar y validar el modelo local."""
    monkeypatch.setattr(settings, "vision_enabled", False)

    response = await client.post(
        "/api/v1/vision/identify",
        files={"image": ("captura.jpg", _image_bytes(), "image/jpeg")},
    )

    assert response.status_code == 503
    assert "todavía no está habilitada" in response.json()["detail"]


@pytest.mark.asyncio
async def test_identify_vision_rechaza_tipo_no_imagen(
    client: AsyncClient,
    service_override: _StubVisionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El endpoint rechaza archivos que el navegador no declaró como imagen."""
    monkeypatch.setattr(settings, "vision_enabled", True)

    response = await client.post(
        "/api/v1/vision/identify",
        files={"image": ("captura.txt", b"texto", "text/plain")},
    )

    assert response.status_code == 400
    assert "debe ser una imagen" in response.json()["detail"]
    assert service_override.received is None


@pytest.mark.asyncio
async def test_identify_vision_baja_confianza_no_cita_regla(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una predicción bajo el umbral se entrega como no concluyente."""
    monkeypatch.setattr(settings, "vision_enabled", True)
    service = _StubVisionService(_identification(confidence=0.79))
    app_main.app.dependency_overrides[vision_api.get_vision_service] = lambda: service  # type: ignore[assignment]
    try:
        response = await client.post(
            "/api/v1/vision/identify",
            files={"image": ("captura.jpg", _image_bytes(), "image/jpeg")},
        )
    finally:
        app_main.app.dependency_overrides.pop(vision_api.get_vision_service, None)

    assert response.status_code == 200
    assert response.json()["identificada"] is False
    assert response.json()["fuente_inia"] is None
    assert "suficiente" in response.json()["mensaje"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [VisionImageError("invalid"), VisionModelUnavailableError("missing"), VisionInferenceError("failed")],
)
async def test_identify_vision_mapea_fallas_del_servicio(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    """Las fallas del modelo no filtran detalles internos al navegador."""
    monkeypatch.setattr(settings, "vision_enabled", True)
    service = _StubVisionService(error)
    app_main.app.dependency_overrides[vision_api.get_vision_service] = lambda: service  # type: ignore[assignment]
    try:
        response = await client.post(
            "/api/v1/vision/identify",
            files={"image": ("captura.jpg", _image_bytes(), "image/jpeg")},
        )
    finally:
        app_main.app.dependency_overrides.pop(vision_api.get_vision_service, None)

    expected_status = 400 if isinstance(error, VisionImageError) else 503
    assert response.status_code == expected_status
    assert "invalid" not in response.text
    assert "missing" not in response.text
    assert "failed" not in response.text
