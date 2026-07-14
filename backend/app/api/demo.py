"""Router demo para la landing page interactiva.

Issue #119 — endpoint POST /api/v1/demo/preguntar que permite probar AgroVoz
desde la web sin WhatsApp. Reusa el LLM y TTS del pipeline real, no usa
Open-WA y no persiste consultas en DB.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.rate_limiter import check_demo_rate_limit
from app.schemas.demo import DemoPreguntaRequest, DemoRespuestaResponse
from app.services.demo_service import process_demo_request

router = APIRouter(tags=["demo"])
logger = logging.getLogger(__name__)

_demo_rate_limit_dep = Depends(check_demo_rate_limit)


def _require_demo_enabled() -> None:
    """Dependencia que bloquea el endpoint si el demo está deshabilitado."""
    if not settings.demo_endpoint_enabled:
        raise HTTPException(
            status_code=503,
            detail="El demo interactivo no está disponible en este entorno.",
        )


_demo_enabled_dep = Depends(_require_demo_enabled)


@router.post("/demo/preguntar", response_model=DemoRespuestaResponse)
async def demo_preguntar(
    request: Request,
    payload: DemoPreguntaRequest,
    _enabled: None = _demo_enabled_dep,
    _rate_limit: None = _demo_rate_limit_dep,
) -> DemoRespuestaResponse:
    """Recibe una pregunta de texto/audio y retorna respuesta de voz/texto."""
    request_id: str = getattr(request.state, "request_id", "-")
    logger.info(
        "Demo consulta recibida — request_id=%s texto_len=%d has_audio=%s",
        request_id,
        len(payload.texto),
        bool(payload.audio_base64),
    )

    try:
        response = await process_demo_request(payload)
    except ValueError as exc:
        logger.warning("Demo consulta inválida — request_id=%s error=%s", request_id, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info(
        "Demo consulta respondida — request_id=%s intent=%s latency_ms=%d",
        request_id,
        response.intent,
        response.latency_ms,
    )
    return response


@router.get("/demo/status")
async def demo_status(
    _enabled: None = _demo_enabled_dep,
) -> JSONResponse:
    """Indica si el endpoint demo está habilitado."""
    return JSONResponse(status_code=200, content={"enabled": True})
