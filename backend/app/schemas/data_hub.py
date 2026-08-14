"""Contratos públicos y administrativos del Data Hub."""

import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class DataSourceResponse(BaseModel):
    """Metadata segura de una fuente, apta para exponerla públicamente."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    name: str
    organization: str
    category: str
    mode: str
    url: HttpUrl
    refresh_policy: str
    coverage: str
    status: str
    enabled: bool
    verified_on: datetime.date
    review_before: datetime.date
    last_success_at: datetime.datetime | None = None
    record_count: int = Field(ge=0)


class DataHubStatusResponse(BaseModel):
    """Resumen operativo sin claves, errores internos ni PII."""

    sources: list[DataSourceResponse]
    total_sources: int = Field(ge=0)
    healthy_sources: int = Field(ge=0)
    stale_sources: int = Field(ge=0)
    not_connected_sources: int = Field(ge=0)
    total_facts: int = Field(ge=0)


class DataHubSyncResponse(BaseModel):
    """Resultado de sincronizar corpus local y, opcionalmente, fuentes remotas."""

    sources_synced: int = Field(ge=0)
    facts_synced: int = Field(ge=0)
    stale_sources: list[str]
    not_connected_sources: list[str]
    remote_sources_synced: int = Field(default=0, ge=0)
    remote_source_errors: list[str] = Field(default_factory=list)


class DataHubSearchResult(BaseModel):
    """Resultado de búsqueda con procedencia completa."""

    text: str
    title: str
    source: str
    source_url: HttpUrl | None = None
    date: str | None = None
    verified_on: datetime.date | None = None
    review_before: datetime.date | None = None
    score: float = Field(ge=0, le=1)


class DataHubSearchResponse(BaseModel):
    """Respuesta de búsqueda pública sobre documentos vigentes."""

    query: str
    results: list[DataHubSearchResult]
    message: str | None = None
