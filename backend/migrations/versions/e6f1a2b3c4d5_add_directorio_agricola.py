"""Agrega el directorio público de sedes agrícolas por comuna.

Revision ID: e6f1a2b3c4d5
Revises: b3f8e2a91c47
Create Date: 2026-08-03
"""

import datetime
from pathlib import Path
from typing import cast

import sqlalchemy as sa
import yaml
from alembic import op

revision: str = "e6f1a2b3c4d5"
down_revision: str | None = "f6a1c2d3e4b5"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def _snapshot_rows() -> list[dict[str, object]]:
    """Carga el snapshot versionado que acompaña a esta migración."""
    snapshot_path = Path(__file__).resolve().parents[2] / "corpus" / "directorio_agricola.yaml"
    raw: object = yaml.safe_load(snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("snapshot de directorio inválido")

    verified_raw = raw.get("verificado_el")
    sedes_raw = raw.get("sedes")
    if not isinstance(verified_raw, str) or not isinstance(sedes_raw, list):
        raise RuntimeError("snapshot de directorio incompleto")

    verified_on = datetime.date.fromisoformat(verified_raw)
    rows: list[dict[str, object]] = []
    for sede_raw in sedes_raw:
        if not isinstance(sede_raw, dict):
            raise RuntimeError("sede inválida en snapshot de directorio")
        sede = cast(dict[str, object], sede_raw)
        rows.append(
            {
                "comuna": sede["comuna"],
                "tipo": sede["tipo"],
                "nombre": sede["nombre"],
                "direccion": sede.get("direccion"),
                "telefono": sede.get("telefono"),
                "horario": sede.get("horario"),
                "fuente": sede["fuente"],
                "fuente_url": sede["fuente_url"],
                "fecha_fuente": sede.get("fecha_fuente"),
                "verificado_el": verified_on,
            }
        )
    return rows


def upgrade() -> None:
    """Crea la tabla y carga el snapshot oficial local."""
    op.create_table(
        "directorio_agricola",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("comuna", sa.String(length=100), nullable=False),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("nombre", sa.String(length=200), nullable=False),
        sa.Column("direccion", sa.String(length=300), nullable=True),
        sa.Column("telefono", sa.String(length=80), nullable=True),
        sa.Column("horario", sa.String(length=300), nullable=True),
        sa.Column("fuente", sa.String(length=150), nullable=False),
        sa.Column("fuente_url", sa.Text(), nullable=False),
        sa.Column("fecha_fuente", sa.String(length=20), nullable=True),
        sa.Column("verificado_el", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.CheckConstraint(
            "tipo IN ('indap', 'prodesal', 'cooperativa')",
            name="ck_directorio_agricola_tipo",
        ),
        sa.CheckConstraint(
            "length(comuna) BETWEEN 1 AND 100",
            name="ck_directorio_agricola_comuna_length",
        ),
        sa.CheckConstraint(
            "length(nombre) BETWEEN 1 AND 200",
            name="ck_directorio_agricola_nombre_length",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_directorio_agricola_comuna", "directorio_agricola", ["comuna"])
    op.create_index("ix_directorio_agricola_tipo", "directorio_agricola", ["tipo"])

    directorio = sa.table(
        "directorio_agricola",
        sa.column("comuna", sa.String()),
        sa.column("tipo", sa.String()),
        sa.column("nombre", sa.String()),
        sa.column("direccion", sa.String()),
        sa.column("telefono", sa.String()),
        sa.column("horario", sa.String()),
        sa.column("fuente", sa.String()),
        sa.column("fuente_url", sa.Text()),
        sa.column("fecha_fuente", sa.String()),
        sa.column("verificado_el", sa.Date()),
    )
    op.bulk_insert(directorio, _snapshot_rows())


def downgrade() -> None:
    """Elimina el directorio y sus índices."""
    op.drop_index("ix_directorio_agricola_tipo", table_name="directorio_agricola")
    op.drop_index("ix_directorio_agricola_comuna", table_name="directorio_agricola")
    op.drop_table("directorio_agricola")
