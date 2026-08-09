"""Endpoint de identificación visual local para el panel PWA."""

from __future__ import annotations

import asyncio
import base64
import logging
import re

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile

from app.core.config import settings
from app.schemas.vision import VisionIdentifyResponse
from app.services.panel_service import verify_panel_token
from app.services.vision_service import (
    VisionError,
    VisionImageError,
    VisionInferenceError,
    VisionModelUnavailableError,
    VisionService,
)

router = APIRouter(tags=["vision"])
logger = logging.getLogger(__name__)


def get_vision_service() -> VisionService:
    """Factory inyectable del servicio de visión."""
    return VisionService()


def _require_vision_enabled() -> None:
    """Mantiene el endpoint apagado hasta provisionar un modelo validado."""
    if not settings.vision_enabled:
        raise HTTPException(
            status_code=503,
            detail="La identificación visual todavía no está habilitada.",
        )


_vision_enabled_dep = Depends(_require_vision_enabled)
_vision_service_dep = Depends(get_vision_service)
_vision_file_dep = File(..., description="Imagen de hoja o cultivo.")
_URL_PATTERN = re.compile(r"https://[^\s]+")
_DATE_PATTERN = re.compile(r"Fuente verificada el\s+([^:]+):")
_MULTIPART_OVERHEAD_BYTES = 64 * 1024


def _parse_citation(rule: str) -> tuple[str | None, str | None, str | None]:
    """Extrae cita, URL y fecha solo desde una regla con formato conocido."""
    if "Fuente verificada el" not in rule:
        return None, None, None
    url_match = _URL_PATTERN.search(rule)
    date_match = _DATE_PATTERN.search(rule)
    url = url_match.group(0).rstrip(".,") if url_match else None
    date = date_match.group(1).strip() if date_match else None
    return rule, url, date


async def _read_image(image: UploadFile) -> bytes:
    """Lee una imagen acotando bytes y liberando el archivo temporal del parser."""
    try:
        content = await image.read(settings.vision_image_max_bytes + 1)
    finally:
        await image.close()
    if len(content) > settings.vision_image_max_bytes:
        raise HTTPException(status_code=413, detail="La imagen excede el tamaño máximo permitido.")
    if not content:
        raise HTTPException(status_code=400, detail="La imagen no puede estar vacía.")
    return content


@router.post("/vision/identify", response_model=VisionIdentifyResponse)
async def identify_vision(
    request: Request,
    image: UploadFile = _vision_file_dep,
    token: str = Query(..., min_length=1, max_length=160, description="Token firmado del panel."),
    _enabled: None = _vision_enabled_dep,
    service: VisionService = _vision_service_dep,
) -> VisionIdentifyResponse:
    """Clasifica una imagen recibida desde la cámara del panel sin persistirla."""
    request_id = getattr(request.state, "request_id", "-")
    if verify_panel_token(token) is None:
        await image.close()
        raise HTTPException(
            status_code=401,
            detail="Link inválido o vencido. Pide uno nuevo por WhatsApp.",
        )
    content_type = image.content_type or ""
    if content_type and not content_type.casefold().startswith("image/"):
        raise HTTPException(status_code=400, detail="El archivo debe ser una imagen.")

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            request_size = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="El tamaño de la solicitud no es válido.") from exc
        # El multipart agrega boundaries y nombres de campo; el límite se
        # aplica a los bytes de la imagen, no a ese overhead pequeño.
        if request_size > settings.vision_image_max_bytes + _MULTIPART_OVERHEAD_BYTES:
            raise HTTPException(status_code=413, detail="La imagen excede el tamaño máximo permitido.")

    image_bytes = await _read_image(image)
    try:
        identification = await asyncio.to_thread(service.identify, image_bytes)
    except VisionImageError as exc:
        logger.info("Imagen visual inválida — request_id=%s error=%s", request_id, type(exc).__name__)
        raise HTTPException(status_code=400, detail="No pude leer la imagen enviada.") from exc
    except (VisionModelUnavailableError, VisionInferenceError) as exc:
        logger.warning("Modelo visual no disponible — request_id=%s error=%s", request_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="El modelo de visión no está disponible.") from exc
    except VisionError as exc:
        logger.warning("Error controlado de visión — request_id=%s error=%s", request_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="No pude analizar la imagen.") from exc
    finally:
        del image_bytes

    source, source_url, source_date = _parse_citation(identification.rule)
    prediction = identification.prediction
    identified = (
        prediction.confidence >= settings.vision_confidence_threshold
        and source is not None
        and source_url is not None
        and source_date is not None
    )
    encoded_image = base64.b64encode(identification.annotated_image).decode("ascii")
    return VisionIdentifyResponse(
        enfermedad=prediction.disease or prediction.label,
        cultivo=prediction.crop,
        confianza=prediction.confidence,
        identificada=identified,
        fuente_inia=source,
        fuente_url=source_url,
        fecha_fuente=source_date,
        imagen_anotada=f"data:image/jpeg;base64,{encoded_image}",
        mensaje=identification.response,
    )
