"""Genera el snapshot público sin PII para el deploy slim de Vercel.

Construye `app/data/demo.db` con el mismo schema que producción (aplica todas
las migraciones Alembic, así que las tablas con PII existen pero quedan
vacías) y copia SOLO datos públicos:

- `odepa_prices` — precios de los últimos N días (default 90).
- `directorio_agricola` — snapshot completo (ya es público por diseño).
- `data_sources` / `data_facts` — catálogo del Data Hub, sin PII por diseño.

Ninguna fila de `consultations`, `user_prefs`, `expenses`,
`consultation_history` ni `parcelas` viaja a este archivo: son las tablas que
guardan `phone_hash` u otro dato personal seudonimizado.

Uso:
    uv run python scripts/build_demo_db.py
    uv run python scripts/build_demo_db.py --source ../data/agrovoz.db --days 90
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"
_MIGRATIONS_DIR = _BACKEND_DIR / "migrations"
_DEFAULT_SOURCE = _BACKEND_DIR / "data" / "agrovoz.db"
_DEFAULT_TARGET = _BACKEND_DIR / "app" / "data" / "demo.db"

# Tablas públicas que sí viajan al snapshot, en orden de copia. `odepa_prices`
# lleva filtro de fecha; el resto se copia completo porque ya es público por
# diseño (directorio institucional, catálogo y hechos del Data Hub).
_PUBLIC_TABLES_FULL = ("directorio_agricola", "data_sources", "data_facts")


def _apply_migrations(target: Path) -> None:
    """Aplica `alembic upgrade head` sobre una DB target vacía."""
    if not _ALEMBIC_INI.exists():
        raise FileNotFoundError(f"No existe alembic.ini en {_ALEMBIC_INI}")

    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{target}")
    command.upgrade(config, "head")


def _copy_public_data(source: Path, target: Path, days: int) -> dict[str, int]:
    """Copia solo tablas/columnas públicas de source a target vía ATTACH."""
    conn = sqlite3.connect(target)
    counts: dict[str, int] = {}
    try:
        conn.execute("ATTACH DATABASE ? AS src", (str(source),))

        # Algunas migraciones siembran filas de bootstrap (ids fijos) para que
        # la app arranque con datos mínimos sin depender de un sync externo.
        # La DB fuente es la autoridad real: se limpia el seed antes de copiar
        # para no chocar con esos ids ni dejar filas de bootstrap mezcladas.
        conn.execute("DELETE FROM odepa_prices")
        conn.execute(
            """
            INSERT INTO odepa_prices
                (id, producto, mercado, precio_kg, unidad, fecha, fuente,
                 created_at, updated_at)
            SELECT id, producto, mercado, precio_kg, unidad, fecha, fuente,
                   created_at, updated_at
            FROM src.odepa_prices
            WHERE fecha >= date('now', ?)
            """,
            (f"-{days} days",),
        )
        counts["odepa_prices"] = conn.execute("SELECT COUNT(*) FROM odepa_prices").fetchone()[0]

        for table in _PUBLIC_TABLES_FULL:
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
            column_list = ", ".join(columns)
            conn.execute(f"DELETE FROM {table}")
            conn.execute(f"INSERT INTO {table} ({column_list}) SELECT {column_list} FROM src.{table}")
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

        conn.commit()
        conn.execute("DETACH DATABASE src")
        conn.execute("VACUUM")
    finally:
        conn.close()
    return counts


def build_demo_db(source: Path, target: Path, days: int) -> dict[str, int]:
    """Construye el snapshot público desde cero en `target`."""
    if not source.exists():
        raise FileNotFoundError(f"No existe la DB fuente en {source}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)

    _apply_migrations(target)
    counts = _copy_public_data(source, target, days)

    size_kb = target.stat().st_size / 1024
    logger.info(
        "demo.db generado — filas=%s tamaño=%.0f KB destino=%s",
        counts,
        size_kb,
        target,
    )
    return counts


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=_DEFAULT_SOURCE, help="DB completa de origen")
    parser.add_argument("--target", type=Path, default=_DEFAULT_TARGET, help="DB pública de destino")
    parser.add_argument("--days", type=int, default=90, help="Ventana de precios ODEPA a incluir")
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = _parse_args()
    try:
        build_demo_db(args.source, args.target, args.days)
    except (CommandError, sqlite3.Error, FileNotFoundError, OSError) as exc:
        logger.error("Error generando demo.db: %s", exc)
        sys.exit(1)
