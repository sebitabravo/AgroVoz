"""Modelos de procedencia y hechos públicos del Data Hub.

Estas tablas contienen únicamente metadata de fuentes y hechos derivados de
snapshots públicos. Nunca se mezclan con conversaciones, audios, teléfonos,
parcelas o gastos de agricultores.
"""

import datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DataSource(Base):
    """Fuente institucional y estado operacional de su integración."""

    __tablename__ = "data_sources"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('live', 'snapshot', 'database')",
            name="ck_data_sources_mode",
        ),
        CheckConstraint(
            "status IN ('healthy', 'stale', 'error', 'disabled', 'not_connected', 'not_synced')",
            name="ck_data_sources_status",
        ),
        CheckConstraint(
            "record_count >= 0",
            name="ck_data_sources_record_count_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    organization: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    license: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_policy: Mapped[str] = mapped_column(String(160), nullable=False)
    coverage: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_synced")
    verified_on: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    review_before: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    last_attempt_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    valid_until: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class DataFact(Base):
    """Hecho textual normalizado, deduplicado y atribuible a una fuente."""

    __tablename__ = "data_facts"
    __table_args__ = (
        CheckConstraint("length(source_key) BETWEEN 1 AND 80", name="ck_data_facts_source_key"),
        CheckConstraint("length(domain) BETWEEN 1 AND 40", name="ck_data_facts_domain"),
        CheckConstraint("length(text) BETWEEN 1 AND 20000", name="ck_data_facts_text"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(160), nullable=False)
    location: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    product: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_date: Mapped[str | None] = mapped_column(String(40), nullable=True)
    verified_on: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    review_before: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    fact_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )
