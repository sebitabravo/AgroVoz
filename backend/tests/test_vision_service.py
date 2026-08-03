"""Tests deterministas del clasificador visual y su cita INIA."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.vision_service import (
    VisionAlternative,
    VisionDisabledError,
    VisionPrediction,
    VisionService,
)


class _StubClassifier:
    """Clasificador controlado para probar el orquestador sin ONNX real."""

    def __init__(self, prediction: VisionPrediction) -> None:
        self._prediction = prediction

    def predict(self, image_bytes: bytes) -> VisionPrediction:
        assert image_bytes == b"imagen-de-prueba"
        return self._prediction


@pytest.fixture(autouse=True)
def _enable_vision_and_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Activa ambos gates solo durante estos tests de la capacidad post-MVP."""
    monkeypatch.setattr(settings, "vision_enabled", True)
    monkeypatch.setattr(settings, "vision_confidence_threshold", 0.8)
    monkeypatch.setattr(settings, "agronomic_rules_enabled", True)


def _prediction(label: str, confidence: float) -> VisionPrediction:
    """Construye una salida de modelo pequeña y reproducible."""
    return VisionPrediction(
        label=label,
        confidence=confidence,
        alternatives=(VisionAlternative(label=label, confidence=confidence),),
    )


def test_identificacion_con_confianza_suficiente_cita_regla_inia() -> None:
    """Una etiqueta conocida devuelve enfermedad, confianza y fuente vigente."""
    service = VisionService(_StubClassifier(_prediction("Potato___Late_blight", 0.94)))

    result = service.identify(b"imagen-de-prueba")

    assert result.status == "identified"
    assert result.classification == "Tizón tardío de la papa"
    assert result.confidence == 0.94
    assert result.source is not None
    assert "INIA" in result.source
    assert result.source_url == "https://enfermedadespapa.inia.cl/tizonTardio.php"
    assert result.verified_on == "2026-07-30"


def test_confianza_baja_no_presenta_diagnostico() -> None:
    """Bajo el umbral la respuesta es honesta y no agrega una regla."""
    service = VisionService(_StubClassifier(_prediction("Potato___Late_blight", 0.79)))

    result = service.identify(b"imagen-de-prueba")

    assert result.status == "uncertain"
    assert result.classification is None
    assert result.detected_label == "Tizón tardío de la papa"
    assert result.rule is None
    assert result.source is None
    assert "79%" in result.message


def test_etiqueta_sin_regla_no_inventa_fuente() -> None:
    """Una etiqueta no cubierta por el corpus nunca obtiene una cita inventada."""
    service = VisionService(_StubClassifier(_prediction("Tomato___healthy", 0.96)))

    result = service.identify(b"imagen-de-prueba")

    assert result.classification == "Tomato___healthy"
    assert result.rule is None
    assert result.source is None
    assert "no doy recomendaciones" in result.message


def test_gate_apagado_falla_cerrado(monkeypatch: pytest.MonkeyPatch) -> None:
    """El servicio también respeta el gate si se invoca sin pasar por HTTP."""
    monkeypatch.setattr(settings, "vision_enabled", False)
    service = VisionService(_StubClassifier(_prediction("Potato___Late_blight", 0.94)))

    with pytest.raises(VisionDisabledError):
        service.identify(b"imagen-de-prueba")
