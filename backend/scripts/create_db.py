"""Crea e inicializa la base de datos aplicando migraciones Alembic.

Ejecuta `alembic upgrade head` programáticamente (sin subprocess) para que
funcione igual local, en Docker y en el VPS. La URL de conexión sale de
settings.database_url (configurada por migrations/env.py).

Uso:
    uv run python scripts/create_db.py            # local
    docker compose run --rm backend python scripts/create_db.py   # Docker
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

# Resuelve rutas desde este archivo: backend/scripts/create_db.py
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"
_MIGRATIONS_DIR = _BACKEND_DIR / "migrations"


def create_db() -> None:
    """Aplica todas las migraciones pendientes hasta `head`.

    Construye el Config de Alembic apuntando al alembic.ini y migrations/
    del backend (independiente del CWD), y delega la URL a migrations/env.py,
    que lee settings.database_url.

    Raises:
        CommandError: Si Alembic no puede aplicar una migración.
        SQLAlchemyError: Si la base de datos responde con error.
        FileNotFoundError: Si alembic.ini o migrations/ no existen.
    """
    if not _ALEMBIC_INI.exists():
        raise FileNotFoundError(f"No existe alembic.ini en {_ALEMBIC_INI}")

    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    command.upgrade(config, "head")
    logger.info("Migraciones aplicadas — base de datos lista")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        create_db()
    except (CommandError, SQLAlchemyError, FileNotFoundError, OSError) as exc:
        logger.error("Error creando base de datos: %s", exc)
        sys.exit(1)
