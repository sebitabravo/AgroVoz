"""Router de health check.

Endpoint de monitoreo para load balancers y health checks de Docker.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint.

    Retorna estado de la API. Útil para monitoreo y load balancers.
    """
    return {"status": "ok"}
