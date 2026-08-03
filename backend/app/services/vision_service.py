"""Clasificación local de imágenes y resolución de citas INIA.

El servicio no envía imágenes a terceros ni permite que un modelo genere
recomendaciones. El modelo ONNX solo entrega etiquetas y confianza; cualquier
texto agronómico se resuelve desde el corpus determinista y vigente.
"""

from __future__ import annotations

import io
import json
import logging
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final, Protocol, cast

import numpy as np
import onnxruntime as ort
from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.services.agronomic_rules_service import get_agronomic_rule_citation

logger = logging.getLogger(__name__)

_IMAGE_SIZE: Final[int] = 224
_TOP_K: Final[int] = 3


class VisionError(RuntimeError):
    """Error base del servicio de visión."""


class VisionDisabledError(VisionError):
    """El gate de visión permanece apagado."""


class VisionModelUnavailableError(VisionError):
    """El modelo o su catálogo de etiquetas no están provisionados."""


class VisionInvalidImageError(VisionError):
    """Los bytes no representan una imagen decodificable."""


class VisionInferenceError(VisionError):
    """La inferencia local falló y no se debe presentar un diagnóstico."""


@dataclass(frozen=True, slots=True)
class VisionAlternative:
    """Etiqueta alternativa producida por el clasificador local."""

    label: str
    confidence: float


@dataclass(frozen=True, slots=True)
class VisionPrediction:
    """Salida cruda normalizada del modelo ONNX."""

    label: str
    confidence: float
    alternatives: tuple[VisionAlternative, ...]


@dataclass(frozen=True, slots=True)
class VisionIdentification:
    """Respuesta segura para la API y el panel PWA."""

    status: str
    classification: str | None
    detected_label: str
    confidence: float
    alternatives: tuple[VisionAlternative, ...]
    message: str
    rule: str | None
    source: str | None
    source_url: str | None
    verified_on: str | None


class _ImageClassifier(Protocol):
    """Contrato mínimo que permite probar el servicio sin ejecutar ONNX."""

    def predict(self, image_bytes: bytes) -> VisionPrediction:
        """Clasifica una imagen ya validada."""


def _normalizar_etiqueta(label: str) -> str:
    """Normaliza etiquetas PlantVillage sin alterar su significado."""
    decomposed = unicodedata.normalize("NFKD", label.casefold())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.replace("_", " ").replace("-", " ").split())


def _display_and_rule_input(label: str) -> tuple[str, str, str]:
    """Mapea una etiqueta conocida a nombre visible y síntoma con regla."""
    normalized = _normalizar_etiqueta(label)
    if "late blight" in normalized or "tizon tardio" in normalized:
        crop = "papa" if "potato" in normalized or "papa" in normalized else ""
        display = "Tizón tardío de la papa" if crop == "papa" else "Tizón tardío"
        return display, crop, "manchas marrones en las hojas"
    return label.strip(), "", ""


def _load_labels(labels_path: Path) -> tuple[str, ...]:
    """Carga y valida el catálogo local de etiquetas del modelo."""
    try:
        raw: object = json.loads(labels_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VisionModelUnavailableError("No se pudo leer el catálogo de etiquetas.") from exc

    if isinstance(raw, dict):
        raw = raw.get("labels")
    if not isinstance(raw, list) or not raw:
        raise VisionModelUnavailableError("El catálogo de etiquetas está vacío o es inválido.")

    labels = tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())
    if len(labels) != len(raw):
        raise VisionModelUnavailableError("El catálogo contiene etiquetas vacías o inválidas.")
    return labels


def _preprocess_image(image_bytes: bytes) -> np.ndarray:
    """Decodifica y adapta la imagen al contrato 224x224 RGB del modelo."""
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            rgb_image = image.convert("RGB")
            resized = rgb_image.resize((_IMAGE_SIZE, _IMAGE_SIZE), Image.Resampling.BILINEAR)
            pixels = np.asarray(resized, dtype=np.float32) / 255.0
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise VisionInvalidImageError("La imagen no se pudo decodificar.") from exc

    # El modelo recibe NCHW y un solo frame para mantener el consumo acotado.
    return cast(np.ndarray, np.transpose(pixels, (2, 0, 1))[np.newaxis, ...])


def _softmax(scores: np.ndarray) -> np.ndarray:
    """Convierte logits en confianzas estables y acotadas a 0..1."""
    shifted = scores - np.max(scores)
    exponentials = np.exp(shifted)
    total = float(exponentials.sum())
    if total <= 0.0 or not np.isfinite(total):
        raise VisionInferenceError("El modelo devolvió scores inválidos.")
    return cast(np.ndarray, exponentials / total)


class _OnnxImageClassifier:
    """Adaptador CPU para un modelo ONNX local y su catálogo de etiquetas."""

    def __init__(self, model_path: Path, labels_path: Path) -> None:
        if not model_path.is_file() or not labels_path.is_file():
            raise VisionModelUnavailableError("El modelo de visión no está provisionado.")
        self._labels = _load_labels(labels_path)
        try:
            session_options = ort.SessionOptions()
            session_options.intra_op_num_threads = 1
            session_options.inter_op_num_threads = 1
            session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            self._session = ort.InferenceSession(
                str(model_path),
                sess_options=session_options,
                providers=["CPUExecutionProvider"],
            )
            inputs = self._session.get_inputs()
            if not inputs:
                raise VisionModelUnavailableError("El modelo de visión no tiene entrada.")
            self._input_name = inputs[0].name
        except VisionModelUnavailableError:
            raise
        except Exception as exc:
            logger.warning("No se pudo cargar el modelo de visión — error=%s", type(exc).__name__)
            raise VisionModelUnavailableError("No se pudo cargar el modelo de visión.") from exc

    def predict(self, image_bytes: bytes) -> VisionPrediction:
        """Ejecuta inferencia local y conserva las tres mejores etiquetas."""
        tensor = _preprocess_image(image_bytes)
        try:
            outputs = self._session.run(None, {self._input_name: tensor})
            scores = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        except (IndexError, TypeError, ValueError, RuntimeError) as exc:
            raise VisionInferenceError("El modelo no pudo procesar la imagen.") from exc

        if scores.size == 0 or not np.isfinite(scores).all() or scores.size > len(self._labels):
            raise VisionInferenceError("El modelo devolvió una salida incompatible.")
        probabilities = _softmax(scores)
        indices = np.argsort(probabilities)[::-1][:_TOP_K]
        alternatives = tuple(
            VisionAlternative(label=self._labels[int(index)], confidence=float(probabilities[int(index)]))
            for index in indices
        )
        best = alternatives[0]
        return VisionPrediction(label=best.label, confidence=best.confidence, alternatives=alternatives)


@lru_cache(maxsize=2)
def _get_classifier(model_path: str, labels_path: str) -> _OnnxImageClassifier:
    """Cachea una sesión ONNX por rutas para no recargarla por request."""
    return _OnnxImageClassifier(Path(model_path), Path(labels_path))


class VisionService:
    """Orquesta inferencia local y reglas citadas sin persistir la imagen."""

    def __init__(self, classifier: _ImageClassifier | None = None) -> None:
        """Permite inyectar un clasificador pequeño y determinista en tests."""
        self._classifier = classifier

    def _load_classifier(self) -> _ImageClassifier:
        """Resuelve la sesión ONNX configurada en el entorno."""
        if not settings.vision_model_path or not settings.vision_labels_path:
            raise VisionModelUnavailableError("Faltan las rutas del modelo de visión.")
        return _get_classifier(settings.vision_model_path, settings.vision_labels_path)

    def identify(self, image_bytes: bytes, cultivo: str = "") -> VisionIdentification:
        """Clasifica una imagen y devuelve solo una regla INIA vigente si existe."""
        if not settings.vision_enabled:
            raise VisionDisabledError("La identificación visual todavía no está habilitada.")

        classifier = self._classifier or self._load_classifier()
        prediction = classifier.predict(image_bytes)
        if not 0.0 <= prediction.confidence <= 1.0:
            raise VisionInferenceError("El modelo devolvió una confianza inválida.")

        display_label, inferred_crop, symptom = _display_and_rule_input(prediction.label)
        normalized_crop = cultivo.strip() or inferred_crop
        confidence_percent = round(prediction.confidence * 100)
        if prediction.confidence < settings.vision_confidence_threshold:
            return VisionIdentification(
                status="uncertain",
                classification=None,
                detected_label=display_label,
                confidence=prediction.confidence,
                alternatives=prediction.alternatives,
                message=(
                    "No pude identificar la enfermedad con suficiente certeza "
                    f"(confianza {confidence_percent}%)."
                ),
                rule=None,
                source=None,
                source_url=None,
                verified_on=None,
            )

        citation = get_agronomic_rule_citation(symptom, normalized_crop) if symptom else None
        if citation is None:
            message = (
                f"Identificación orientativa: {display_label} ({confidence_percent}%). "
                "No tengo una regla INIA vigente para esta etiqueta; no doy recomendaciones."
            )
        else:
            message = f"Identificación orientativa: {display_label} ({confidence_percent}%)."
        return VisionIdentification(
            status="identified",
            classification=display_label,
            detected_label=display_label,
            confidence=prediction.confidence,
            alternatives=prediction.alternatives,
            message=message,
            rule=citation.text if citation else None,
            source=citation.source if citation else None,
            source_url=citation.source_url if citation else None,
            verified_on=citation.verified_on.isoformat() if citation else None,
        )
