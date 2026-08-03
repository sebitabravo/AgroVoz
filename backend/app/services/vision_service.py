"""Clasificación local de enfermedades y plagas en imágenes de WhatsApp.

El servicio usa un modelo ONNX local y no envía imágenes a terceros. La imagen
solo vive en memoria durante la inferencia; así se evita crear una copia
persistente que luego pueda quedar fuera de la retención máxima de 24 horas.
La identificación es preliminar y nunca reemplaza una regla agronómica citada.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Protocol, cast

import httpx
import numpy as np
import onnxruntime as ort
from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.services.agronomic_rules_service import get_agronomic_rule_for_llm
from app.services.openwa_service import OpenWAService

logger = logging.getLogger(__name__)

_IMAGE_SIZE = 224
_MAX_IMAGE_PIXELS = 20_000_000
_IMAGE_MEAN = (0.485, 0.456, 0.406)
_IMAGE_STD = (0.229, 0.224, 0.225)
_MAX_PREDICTIONS = 3

# PlantVillage usa etiquetas técnicas, mientras el corpus INIA resuelve
# descripciones observables. Solo se traducen combinaciones verificadas; una
# etiqueta sin mapping falla cerrado en vez de improvisar un síntoma.
_RULE_SYMPTOMS: dict[tuple[str, str], str] = {
    ("papa", "tizon tardio"): "manchas marrones en las hojas",
}

_LOW_CONFIDENCE_TEXT = (
    "No pude identificar la plaga o enfermedad con suficiente certeza. "
    "Prueba con una foto más cercana, enfocada y con buena luz."
)
_MODEL_UNAVAILABLE_TEXT = (
    "No pude analizar la imagen porque el modelo de visión no está disponible. "
    "No tengo un diagnóstico confiable para esta foto."
)
_DOWNLOAD_ERROR_TEXT = "No pude descargar la imagen desde WhatsApp. Prueba enviarla nuevamente."


class VisionError(RuntimeError):
    """Error controlado del servicio de visión."""


class VisionModelUnavailableError(VisionError):
    """El modelo ONNX no existe o no pudo cargarse."""


class VisionImageError(VisionError):
    """La imagen está vacía, excede el límite o no es un formato válido."""


class VisionInferenceError(VisionError):
    """El modelo no entregó una salida utilizable."""


def get_regla_agronomica(enfermedad: str, cultivo: str) -> str:
    """Resuelve la regla citada existente con nombres del dominio visual."""
    return get_agronomic_rule_for_llm(enfermedad, cultivo)


class _ModelInput(Protocol):
    name: str
    shape: Sequence[object]


class _InferenceSession(Protocol):
    """Subset de la sesión ONNX que necesita este servicio."""

    def get_inputs(self) -> Sequence[_ModelInput]:
        """Devuelve las entradas declaradas por el modelo."""

    def run(self, output_names: list[str] | None, input_feed: dict[str, object]) -> Sequence[object]:
        """Ejecuta una inferencia con el tensor preparado."""


@dataclass(frozen=True, slots=True)
class VisionPrediction:
    """Predicción individual ordenada por confianza descendente."""

    label: str
    confidence: float
    crop: str
    disease: str
    rank: int

    @property
    def clase(self) -> str:
        """Alias en español para consumidores del dominio."""
        return self.label

    @property
    def confianza(self) -> float:
        """Alias en español para consumidores del dominio."""
        return self.confidence


@dataclass(frozen=True, slots=True)
class VisionClassification:
    """Resultado top-3 de la inferencia visual."""

    predictions: tuple[VisionPrediction, ...]

    @property
    def top(self) -> VisionPrediction:
        """Predicción con mayor confianza."""
        if not self.predictions:
            raise VisionInferenceError("El modelo no devolvió predicciones")
        return self.predictions[0]

    @property
    def top3(self) -> tuple[VisionPrediction, ...]:
        """Las tres mejores predicciones o menos si el modelo tiene pocas clases."""
        return self.predictions[:_MAX_PREDICTIONS]


def _humanize_label(label: str) -> str:
    """Convierte una etiqueta técnica en texto legible sin cambiar su sentido."""
    return " ".join(label.replace("___", " ").replace("__", " ").replace("_", " ").split())


def _split_label(label: str) -> tuple[str, str]:
    """Extrae cultivo y enfermedad de etiquetas PlantVillage o equivalentes."""
    if "___" in label:
        crop, disease = label.split("___", 1)
        return _humanize_label(crop), _humanize_label(disease)
    return "", _humanize_label(label)


def _softmax_or_probabilities(raw_output: object) -> list[float]:
    """Normaliza logits o probabilidades a una lista estable de confianzas."""
    scores = np.asarray(raw_output, dtype=np.float32).reshape(-1)
    if scores.size == 0 or not np.all(np.isfinite(scores)):
        raise VisionInferenceError("El modelo devolvió scores inválidos")

    total = float(np.sum(scores))
    if np.all(scores >= 0) and np.isclose(total, 1.0, atol=1e-3):
        probabilities = scores
    else:
        shifted = scores - np.max(scores)
        exponentials = np.exp(shifted)
        probabilities = exponentials / np.sum(exponentials)
    return [float(score) for score in probabilities]


def _read_label_value(raw: object) -> tuple[str, ...]:
    """Valida una lista de etiquetas obtenida desde JSON o metadata ONNX."""
    if isinstance(raw, dict):
        raw = raw.get("labels")
    if not isinstance(raw, list):
        return ()
    labels = tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())
    return labels


class VisionService:
    """Carga el modelo ONNX y procesa una imagen sin persistirla."""

    _session_cache: ClassVar[dict[str, _InferenceSession]] = {}
    _cache_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        model_path: str | Path | None = None,
        labels_path: str | Path | None = None,
        session: _InferenceSession | None = None,
    ) -> None:
        """Inicializa el servicio, permitiendo inyectar una sesión en tests."""
        self._model_path = Path(model_path or settings.vision_model_path)
        self._labels_path = Path(labels_path or settings.vision_labels_path)
        self._session = session
        self._labels: tuple[str, ...] | None = None

    @classmethod
    def clear_cache(cls) -> None:
        """Limpia las sesiones cacheadas para aislar tests o recargas controladas."""
        with cls._cache_lock:
            cls._session_cache.clear()

    def load_model(self) -> None:
        """Carga el modelo una vez y deja lista la sesión CPU para inferencias."""
        self._get_session()

    def _get_session(self) -> _InferenceSession:
        """Obtiene la sesión inyectada o la instancia cacheada del modelo."""
        if self._session is not None:
            return self._session
        cache_key = str(self._model_path.resolve())
        with self._cache_lock:
            cached = self._session_cache.get(cache_key)
            if cached is not None:
                self._session = cached
                return cached
            if not self._model_path.is_file():
                raise VisionModelUnavailableError("No existe el modelo ONNX configurado")
            try:
                loaded = cast(
                    _InferenceSession,
                    ort.InferenceSession(
                        str(self._model_path),
                        providers=["CPUExecutionProvider"],
                    ),
                )
            except (OSError, RuntimeError, ValueError) as exc:
                raise VisionModelUnavailableError("No se pudo cargar el modelo ONNX") from exc
            self._session_cache[cache_key] = loaded
            self._session = loaded
            return loaded

    @staticmethod
    def _preprocess(image_bytes: bytes, input_shape: Sequence[object]) -> object:
        """Decodifica, normaliza y redimensiona la imagen al tensor del modelo."""
        try:
            with Image.open(io.BytesIO(image_bytes)) as source:
                if source.width * source.height > _MAX_IMAGE_PIXELS:
                    raise VisionImageError("La imagen excede el límite de píxeles")
                image = source.convert("RGB").resize(
                    (_IMAGE_SIZE, _IMAGE_SIZE),
                    Image.Resampling.BILINEAR,
                )
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
            raise VisionImageError("La imagen no tiene un formato válido") from exc

        array = np.asarray(image, dtype=np.float32) / 255.0
        normalized_array = (array - np.asarray(_IMAGE_MEAN, dtype=np.float32)) / np.asarray(
            _IMAGE_STD,
            dtype=np.float32,
        )
        if len(input_shape) == 4 and input_shape[-1] == 3 and input_shape[1] != 3:
            return np.expand_dims(normalized_array, axis=0)
        return np.expand_dims(np.transpose(normalized_array, (2, 0, 1)), axis=0)

    def _load_labels(self, session: _InferenceSession) -> tuple[str, ...]:
        """Carga etiquetas desde JSON o metadata sin confiar en contenido libre."""
        if self._labels is not None:
            return self._labels

        labels: tuple[str, ...] = ()
        if self._labels_path.is_file():
            try:
                raw = json.loads(self._labels_path.read_text(encoding="utf-8"))
                labels = _read_label_value(raw)
            except (OSError, json.JSONDecodeError):
                logger.warning("Etiquetas de visión inválidas — se usarán clases genéricas")

        if not labels:
            metadata = getattr(session, "get_modelmeta", lambda: None)()
            custom_metadata = getattr(metadata, "custom_metadata_map", {})
            if isinstance(custom_metadata, dict):
                raw_metadata = custom_metadata.get("labels")
                if isinstance(raw_metadata, str):
                    try:
                        labels = _read_label_value(json.loads(raw_metadata))
                    except json.JSONDecodeError:
                        labels = ()

        self._labels = labels
        return labels

    def classify(self, image_bytes: bytes) -> VisionClassification:
        """Clasifica una imagen y retorna sus tres clases más probables."""
        if not image_bytes:
            raise VisionImageError("La imagen está vacía")
        if len(image_bytes) > settings.vision_image_max_bytes:
            raise VisionImageError("La imagen excede el tamaño máximo permitido")

        session = self._get_session()
        inputs = session.get_inputs()
        if not inputs or not inputs[0].name:
            raise VisionInferenceError("El modelo no declara una entrada válida")

        tensor = self._preprocess(image_bytes, inputs[0].shape)
        try:
            outputs = session.run(None, {inputs[0].name: tensor})
        except (RuntimeError, ValueError, OSError) as exc:
            raise VisionInferenceError("La inferencia ONNX falló") from exc
        if not outputs:
            raise VisionInferenceError("El modelo no devolvió resultados")

        probabilities = _softmax_or_probabilities(outputs[0])
        labels = self._load_labels(session)
        indexes = sorted(range(len(probabilities)), key=lambda index: probabilities[index], reverse=True)
        predictions = tuple(
            self._prediction(index, probabilities[index], labels, rank)
            for rank, index in enumerate(indexes[:_MAX_PREDICTIONS], start=1)
        )
        return VisionClassification(predictions=predictions)

    @staticmethod
    def _prediction(
        index: int,
        confidence: float,
        labels: tuple[str, ...],
        rank: int,
    ) -> VisionPrediction:
        """Construye una predicción y separa cultivo de enfermedad."""
        label = labels[index] if index < len(labels) else f"clase_{index}"
        crop, disease = _split_label(label)
        return VisionPrediction(
            label=label,
            confidence=confidence,
            crop=crop,
            disease=disease,
            rank=rank,
        )

    def build_response(self, classification: VisionClassification) -> str:
        """Redacta una respuesta honesta y cita la regla vigente si corresponde."""
        top = classification.top
        if top.confidence < settings.vision_confidence_threshold:
            return _LOW_CONFIDENCE_TEXT

        identified = _humanize_label(top.label)
        symptom = _RULE_SYMPTOMS.get((top.crop.casefold(), top.disease.casefold()))
        if symptom is None:
            return _LOW_CONFIDENCE_TEXT
        rule = get_regla_agronomica(symptom, top.crop)
        if "Fuente verificada el" not in rule or "https://" not in rule:
            return _LOW_CONFIDENCE_TEXT
        confidence_percent = round(top.confidence * 100)
        return f"Identificación visual preliminar: {identified} ({confidence_percent}%). {rule}"

    async def process_image(
        self,
        image_bytes: bytes,
        chat_id: str,
        request_id: str,
        message_id: str = "",
        openwa: OpenWAService | None = None,
    ) -> str | None:
        """Clasifica una imagen y envía la respuesta por texto a WhatsApp."""
        if not settings.vision_enabled:
            return None

        try:
            classification = await asyncio.to_thread(self.classify, image_bytes)
            response = await asyncio.to_thread(self.build_response, classification)
        except VisionImageError:
            response = _LOW_CONFIDENCE_TEXT
        except (VisionModelUnavailableError, VisionInferenceError):
            response = _MODEL_UNAVAILABLE_TEXT

        sender = openwa or OpenWAService()
        try:
            await sender.send_text(chat_id, response)
        except (httpx.HTTPError, OSError, RuntimeError, ValueError, TypeError):
            logger.warning(
                "Respuesta de visión no enviada — request_id=%s message_id=%s",
                request_id,
                message_id or "sin_id",
            )
            return None
        logger.info(
            "Respuesta de visión enviada — request_id=%s message_id=%s",
            request_id,
            message_id or "sin_id",
        )
        return response

    async def process_whatsapp_image(
        self,
        message_id: str,
        chat_id: str,
        request_id: str,
        openwa: OpenWAService | None = None,
    ) -> str | None:
        """Descarga media con Open-WA, clasifica y libera los bytes al terminar."""
        if not settings.vision_enabled:
            return None
        if not message_id:
            return await self.process_image(b"", chat_id, request_id, message_id, openwa)

        gateway = openwa or OpenWAService()
        try:
            image_bytes = await gateway.download_image(message_id)
        except (httpx.HTTPError, OSError, RuntimeError, ValueError):
            logger.warning(
                "Imagen no descargada desde Open-WA — request_id=%s",
                request_id,
            )
            try:
                await gateway.send_text(chat_id, _DOWNLOAD_ERROR_TEXT)
            except (httpx.HTTPError, OSError, RuntimeError, ValueError, TypeError):
                logger.warning("Error enviando aviso de descarga de visión — request_id=%s", request_id)
            return None

        try:
            return await self.process_image(image_bytes, chat_id, request_id, message_id, gateway)
        finally:
            # No se escribe la imagen a disco; eliminar la referencia garantiza
            # que el buffer temporal no sobreviva al ciclo de procesamiento.
            del image_bytes


def preload_model() -> None:
    """Precalienta el modelo al iniciar la app cuando el gate está activo."""
    if not settings.vision_enabled:
        return
    try:
        VisionService().load_model()
    except VisionModelUnavailableError:
        logger.error("Modelo de visión no disponible — feature degradada con respuesta honesta")
