"""Tests deterministas del servicio de visión local."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import httpx
import numpy as np
import pytest
from PIL import Image

from app.core.config import settings
from app.services.vision_service import (
    VisionClassification,
    VisionImageError,
    VisionService,
)


class _FakeInput:
    """Entrada mínima compatible con la sesión ONNX."""

    name = "imagen"
    shape = (1, 3, 224, 224)


class _FakeSession:
    """Sesión inyectable para evitar cargar un modelo binario en pytest."""

    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.tensor: object | None = None

    def get_inputs(self) -> list[_FakeInput]:
        return [_FakeInput()]

    def run(self, _output_names: list[str] | None, input_feed: dict[str, object]) -> list[object]:
        self.tensor = input_feed["imagen"]
        return [np.asarray([self.scores], dtype=np.float32)]


def _image_bytes() -> bytes:
    """Genera una imagen RGB pequeña sin leer fixtures externos."""
    image = Image.new("RGB", (8, 4), color=(80, 120, 40))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_classify_redimensiona_y_retorna_top3_ordenado(tmp_path: Path) -> None:
    """La inferencia entrega tres clases ordenadas y un tensor 224x224."""
    path = tmp_path / "labels.json"
    path.write_text(
        json.dumps(["Papa___tizon_tardio", "Papa___tizon_temprano", "Papa___sana"]),
        encoding="utf-8",
    )
    session = _FakeSession([0.2, 0.7, 0.1])

    result = VisionService(session=session, labels_path=path).classify(_image_bytes())

    assert isinstance(result, VisionClassification)
    assert [prediction.label for prediction in result.top3] == [
        "Papa___tizon_temprano",
        "Papa___tizon_tardio",
        "Papa___sana",
    ]
    assert result.top.confidence == pytest.approx(0.7)
    tensor = cast(np.ndarray, session.tensor)
    assert tensor.shape == (1, 3, 224, 224)
    assert tensor.dtype == np.float32


def test_build_response_bajo_umbral_no_afirma_diagnostico(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una confianza menor a 80% responde honestamente y no busca una regla."""
    settings.vision_confidence_threshold = 0.8
    session = _FakeSession([0.79, 0.11, 0.10])
    service = VisionService(session=session)

    def fail_rule(*_args: str, **_kwargs: object) -> str:
        raise AssertionError("no debe buscar regla bajo el umbral")

    monkeypatch.setattr("app.services.vision_service.get_agronomic_rule_for_llm", fail_rule)
    response = service.build_response(service.classify(_image_bytes()))

    assert "No pude identificar" in response


def test_build_response_alcanzar_umbral_cita_regla_y_fuente(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Con 80% exacto se integra la regla determinística citada."""
    monkeypatch.setattr(settings, "vision_confidence_threshold", 0.8)
    labels_path = tmp_path / "labels.json"
    labels_path.write_text(
        json.dumps(["Papa___tizon_tardio", "Papa___tizon_temprano"]),
        encoding="utf-8",
    )
    session = _FakeSession([0.8, 0.2])
    service = VisionService(session=session, labels_path=labels_path)
    captured: list[tuple[str, str]] = []

    def fake_rule(symptom: str, crop: str) -> str:
        captured.append((symptom, crop))
        return "Fuente INIA verificada el 01/01/2026: https://inia.cl/regla"

    monkeypatch.setattr("app.services.vision_service.get_agronomic_rule_for_llm", fake_rule)
    response = service.build_response(service.classify(_image_bytes()))

    assert captured == [("tizon tardio", "Papa")]
    assert "80%" in response
    assert "Fuente INIA" in response


def test_classify_rechaza_imagen_vacia() -> None:
    """El servicio nunca intenta inferir bytes vacíos."""
    service = VisionService(session=_FakeSession([1.0]))

    with pytest.raises(VisionImageError):
        service.classify(b"")


@pytest.mark.asyncio
async def test_process_whatsapp_image_descarga_clasifica_y_envia(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """El flujo Open-WA → visión → respuesta libera la imagen en memoria."""
    monkeypatch.setattr(settings, "vision_enabled", True)
    monkeypatch.setattr(settings, "vision_confidence_threshold", 0.8)
    labels_path = tmp_path / "labels.json"
    labels_path.write_text(
        json.dumps(["Papa___tizon_tardio", "Papa___tizon_temprano"]),
        encoding="utf-8",
    )
    session = _FakeSession([0.9, 0.1])
    gateway = AsyncMock()
    gateway.download_image.return_value = _image_bytes()
    gateway.send_text.return_value = {"status": "sent"}
    service = VisionService(session=session, labels_path=labels_path)
    monkeypatch.setattr(
        "app.services.vision_service.get_agronomic_rule_for_llm",
        lambda *_args, **_kwargs: "Fuente INIA verificada el 01/01/2026: https://inia.cl/regla",
    )

    response = await service.process_whatsapp_image(
        message_id="false_56912345678@c.us_image",
        chat_id="56912345678@c.us",
        request_id="req-1",
        openwa=gateway,
    )

    assert response is not None
    gateway.download_image.assert_awaited_once_with("false_56912345678@c.us_image")
    gateway.send_text.assert_awaited_once()
    assert "90%" in cast(str, gateway.send_text.await_args.args[1])


@pytest.mark.asyncio
async def test_process_whatsapp_image_usa_respuesta_honesta_si_falla_descarga(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un error REST no expone detalles del gateway al agricultor."""
    monkeypatch.setattr(settings, "vision_enabled", True)
    gateway = AsyncMock()
    gateway.download_image.side_effect = httpx.ConnectError("gateway down")
    gateway.send_text.return_value = {"status": "sent"}

    response = await VisionService(session=_FakeSession([1.0])).process_whatsapp_image(
        message_id="image-1",
        chat_id="56912345678@c.us",
        request_id="req-2",
        openwa=gateway,
    )

    assert response is None
    sent = cast(str, gateway.send_text.await_args.args[1])
    assert "No pude descargar" in sent
    assert "gateway down" not in sent
