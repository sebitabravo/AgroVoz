"""Endpoint admin para provisionar links de la console de agrónomos (C4).

Un agrónomo PRODESAL no es un contacto de WhatsApp propio en AgroVoz — no
puede pedir su link por voz como el agricultor pide el suyo (C3). El equipo
genera el link desde acá y se lo entrega por fuera del sistema (email,
WhatsApp interno, en persona).

Requiere header X-Admin-Key, igual que el resto de app/api/admin/.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.admin.deps import require_admin_key
from app.core.config import settings
from app.services.agronomist_console_service import (
    AgronomistLinkError,
    generate_agronomist_token,
    validate_group_label,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/agronomos",
    tags=["admin-agronomos"],
    dependencies=[Depends(require_admin_key)],
)


class AgronomistLinkRequest(BaseModel):
    """Body del POST /admin/agronomos/links."""

    group_label: str = Field(
        min_length=1,
        max_length=100,
        description="Código de grupo PRODESAL (ej: 'prodesal-traiguen-norte').",
    )


class AgronomistLinkResponse(BaseModel):
    """Link firmado listo para entregar al agrónomo."""

    link: str
    expires_in_hours: int


@router.post("/links", response_model=AgronomistLinkResponse)
def create_agronomist_link(body: AgronomistLinkRequest) -> AgronomistLinkResponse:
    """Genera un link firmado de la console para un group_label.

    Retorna 503 si la console está deshabilitada, 422 si el group_label no
    tiene formato de slug válido, y 500 si la clave de firma no está
    configurada de forma segura (no debería ocurrir con la validación de
    arranque, pero no se asume).
    """
    if not settings.agronomist_console_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La console de agrónomos todavía no está disponible.",
        )
    if not validate_group_label(body.group_label):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="group_label debe ser un slug 'prodesal-...' válido.",
        )

    try:
        token = generate_agronomist_token(body.group_label)
    except AgronomistLinkError:
        logger.error("No se pudo firmar el link de agrónomo — clave insegura")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo generar el link de forma segura.",
        ) from None

    base_url = settings.agronomist_base_url.rstrip("/") if settings.agronomist_base_url else ""
    link = f"{base_url}/{token}" if base_url else token
    return AgronomistLinkResponse(link=link, expires_in_hours=settings.agronomist_link_ttl_hours)
