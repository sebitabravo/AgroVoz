"""Endpoints públicos read-only del catálogo y buscador del Data Hub."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.data_hub import (
    DataHubSearchResponse,
    DataHubSearchResult,
    DataSourceResponse,
)
from app.services.data_hub_service import DataHubValidationError, get_data_hub_catalog
from app.services.rag_service import rag_corpus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["data-hub"])


@router.get("/data/sources", response_model=list[DataSourceResponse])
def list_data_sources(
    db: Session = Depends(get_db),  # noqa: B008 — FastAPI dependency injection pattern
) -> list[DataSourceResponse]:
    """Lista fuentes, cobertura y estado sin exponer configuración sensible."""
    try:
        rows = get_data_hub_catalog(db)
    except DataHubValidationError as exc:
        logger.error("Catálogo Data Hub inválido — error=%s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El catálogo de fuentes no está disponible.",
        ) from exc
    return [DataSourceResponse.model_validate(row) for row in rows]


@router.get("/data/search", response_model=DataHubSearchResponse)
def search_data_hub(
    q: str = Query(min_length=1, max_length=300, description="Consulta factual agrícola"),
    top_k: int = Query(default=3, ge=1, le=5, description="Máximo de resultados"),
) -> DataHubSearchResponse:
    """Busca snapshots vigentes y devuelve procedencia completa."""
    results = rag_corpus.search(q, top_k=top_k)
    response_results = [
        DataHubSearchResult.model_validate(
            {
                "text": str(result["text"]),
                "title": str(result["title"]),
                "source": str(result["source"]),
                "source_url": str(result["source_url"]) if result.get("source_url") else None,
                "date": str(result["date"]) if result.get("date") else None,
                "verified_on": str(result["verified_on"]) if result.get("verified_on") else None,
                "review_before": str(result["review_before"]) if result.get("review_before") else None,
                "score": float(result["score"]),
            }
        )
        for result in results
    ]
    message = None
    if not response_results:
        message = "No se encontró un dato vigente en las fuentes integradas."
    return DataHubSearchResponse(query=q, results=response_results, message=message)
