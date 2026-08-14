"""Crea catálogo y hechos normalizados del Data Hub.

Revision ID: a7b8c9d0e1f2
Revises: e6f1a2b3c4d5
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "e6f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea tablas separadas de metadata pública y hechos sin PII."""
    op.create_table(
        "data_sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("organization", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("license", sa.Text(), nullable=False),
        sa.Column("refresh_policy", sa.String(length=160), nullable=False),
        sa.Column("coverage", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("verified_on", sa.Date(), nullable=False),
        sa.Column("review_before", sa.Date(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.CheckConstraint(
            "mode IN ('live', 'snapshot', 'database')",
            name="ck_data_sources_mode",
        ),
        sa.CheckConstraint(
            "status IN ('healthy', 'stale', 'error', 'disabled', 'not_connected', 'not_synced')",
            name="ck_data_sources_status",
        ),
        sa.CheckConstraint(
            "record_count >= 0",
            name="ck_data_sources_record_count_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_index("ix_data_sources_key", "data_sources", ["key"], unique=False)
    op.create_index("ix_data_sources_category", "data_sources", ["category"], unique=False)

    op.create_table(
        "data_facts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_key", sa.String(length=80), nullable=False),
        sa.Column("domain", sa.String(length=40), nullable=False),
        sa.Column("subject", sa.String(length=160), nullable=False),
        sa.Column("location", sa.String(length=120), nullable=True),
        sa.Column("product", sa.String(length=100), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_date", sa.String(length=40), nullable=True),
        sa.Column("verified_on", sa.Date(), nullable=False),
        sa.Column("review_before", sa.Date(), nullable=True),
        sa.Column("fact_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.CheckConstraint("length(source_key) BETWEEN 1 AND 80", name="ck_data_facts_source_key"),
        sa.CheckConstraint("length(domain) BETWEEN 1 AND 40", name="ck_data_facts_domain"),
        sa.CheckConstraint("length(text) BETWEEN 1 AND 20000", name="ck_data_facts_text"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fact_hash"),
    )
    op.create_index("ix_data_facts_source_key", "data_facts", ["source_key"], unique=False)
    op.create_index("ix_data_facts_domain", "data_facts", ["domain"], unique=False)
    op.create_index("ix_data_facts_location", "data_facts", ["location"], unique=False)
    op.create_index("ix_data_facts_product", "data_facts", ["product"], unique=False)
    op.create_index("ix_data_facts_fact_hash", "data_facts", ["fact_hash"], unique=False)


def downgrade() -> None:
    """Elimina el catálogo y los hechos sin tocar los datos de dominio."""
    op.drop_index("ix_data_facts_fact_hash", table_name="data_facts")
    op.drop_index("ix_data_facts_product", table_name="data_facts")
    op.drop_index("ix_data_facts_location", table_name="data_facts")
    op.drop_index("ix_data_facts_domain", table_name="data_facts")
    op.drop_index("ix_data_facts_source_key", table_name="data_facts")
    op.drop_table("data_facts")
    op.drop_index("ix_data_sources_category", table_name="data_sources")
    op.drop_index("ix_data_sources_key", table_name="data_sources")
    op.drop_table("data_sources")
