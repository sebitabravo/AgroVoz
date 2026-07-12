"""Entorno de migraciones Alembic para AgroVoz.

Configura la conexión SQLAlchemy desde pydantic-settings e importa
los modelos automáticamente para que --autogenerate detecte cambios.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Base de Alembic: lee la config desde alembic.ini
config = context.config

# Logging desde alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Metadata de modelos ─────────────────────────────────────────
# Importar Base y TODOS los modelos para que Base.metadata los incluya.
# Si un modelo no se importa aquí, --autogenerate NO lo detecta.
from app.core.config import settings  # noqa: E402 — import después de fileConfig
from app.core.database import Base  # noqa: E402
from app.models import Consultation, OdepaPrice, UserPrefs  # noqa: E402, F401 — necesario para metadata

target_metadata = Base.metadata

# ── URL desde pydantic-settings ─────────────────────────────────
# La URL en alembic.ini es un placeholder. La fuente de verdad es
# settings.database_url (desde .env o variable de entorno).
# Si un test ya sobreescribió la URL vía set_main_option (DB temporal
# en tmp_path), respetamos ese valor: la URL del test no coincide con
# el placeholder.
_ALEMBIC_INI_PLACEHOLDER = "sqlite:///data/agrovoz.db"
if config.get_main_option("sqlalchemy.url") == _ALEMBIC_INI_PLACEHOLDER:
    config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    """Ejecuta migraciones en modo 'offline' (genera SQL sin conectarse a la DB).

    Útil para revisar el SQL generado antes de aplicarlo.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite requiere batch para ALTER TABLE
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Ejecuta migraciones conectándose a la base de datos.

    Modo normal de operación: aplica las migraciones directamente.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite: batch mode para ALTER TABLE
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
