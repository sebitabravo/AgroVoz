"""Endpoint de identificación visual para el panel PWA."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.core.config import settings
from app.schemas.vision import VisionAlternativeResponse, VisionIdentifyResponse
from app.services.vision_service import (
    VisionDisabledError,
    VisionInferenceError,
    VisionInvalidImageError,
    VisionModelUnavailableError,
    VisionService,
)

router = APIRouter(prefix="/vision", tags=["vision"])

_ALLOWED_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_image_file_dep = File(..., description="Fotografía de una hoja o cultivo.")


def get_vision_service() -> VisionService:
    """Factory separada para que FastAPI no inspeccione el clasificador interno."""
    return VisionService()


_vision_service_dep = Depends(get_vision_service)


async def _read_image(upload: UploadFile) -> bytes:
    """Lee solo el límite configurado y evita conservar la imagen en disco."""
    image_bytes = await upload.read(settings.vision_max_image_bytes + 1)
    if len(image_bytes) > settings.vision_max_image_bytes:
        raise HTTPException(
            status_code=413,
            detail="La imagen supera el tamaño máximo permitido.",
        )
    if not image_bytes:
        raise HTTPException(status_code=400, detail="La imagen está vacía.")
    return image_bytes


@router.post("/identify", response_model=VisionIdentifyResponse)
async def identify_image(
    image: UploadFile = _image_file_dep,
    cultivo: str = Query(default="", max_length=50, description="Cultivo conocido, si el productor lo indica."),
    vision_service: VisionService = _vision_service_dep,
) -> VisionIdentifyResponse:
    """Recibe una foto, la clasifica localmente y devuelve la cita INIA."""
    try:
        if not settings.vision_enabled:
            raise HTTPException(
                status_code=503,
                detail="La identificación visual todavía no está disponible.",
            )

        content_type = (image.content_type or "").split(";", maxsplit=1)[0].lower()
        if content_type not in _ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=415, detail="Formato de imagen no compatible.")

        image_bytes = await _read_image(image)
        result = await asyncio.to_thread(vision_service.identify, image_bytes, cultivo.strip())
    except VisionDisabledError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except VisionModelUnavailableError as exc:
        raise HTTPException(status_code=503, detail="El modelo de visión no está disponible.") from exc
    except VisionInvalidImageError as exc:
        raise HTTPException(status_code=400, detail="La imagen no se pudo leer.") from exc
    except VisionInferenceError as exc:
        raise HTTPException(status_code=503, detail="No se pudo analizar la imagen.") from exc
    finally:
        # UploadFile puede usar un archivo temporal interno; se cierra en el
        # mismo request para que ninguna foto quede retenida en el VPS.
        await image.close()

    return VisionIdentifyResponse(
        status=result.status,
        classification=result.classification,
        detected_label=result.detected_label,
        confidence=result.confidence,
        alternatives=[
            VisionAlternativeResponse(label=item.label, confidence=item.confidence)
            for item in result.alternatives
        ],
        message=result.message,
        rule=result.rule,
        source=result.source,
        source_url=result.source_url,
        verified_on=result.verified_on,
    )
