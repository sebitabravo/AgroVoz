"""Pruebas de upgrade y downgrade de las tablas del Data Hub."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"
_MIGRATIONS_DIR = _BACKEND_DIR / "migrations"
_PREVIOUS_REVISION = "e6f1a2b3c4d5"
_CURRENT_REVISION = "a7b8c9d0e1f2"


def _alembic_config(database_path: Path) -> Config:
    """Construye Alembic contra una base temporal aislada."""
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    return config


def test_upgrade_crea_tablas_e_indices_del_data_hub(tmp_path: Path) -> None:
    """El upgrade crea el catálogo y los hechos desde el head anterior."""
    database_path = tmp_path / "data_hub_upgrade.db"
    config = _alembic_config(database_path)
    engine = create_engine(f"sqlite:///{database_path}")

    command.upgrade(config, _PREVIOUS_REVISION)
    command.upgrade(config, _CURRENT_REVISION)

    database_inspector = inspect(engine)
    assert {"data_sources", "data_facts"} <= set(database_inspector.get_table_names())
    assert "fact_hash" in {column["name"] for column in database_inspector.get_columns("data_facts")}
    assert "ix_data_facts_fact_hash" in {index["name"] for index in database_inspector.get_indexes("data_facts")}

    engine.dispose()


def test_downgrade_elimina_solo_tablas_del_data_hub(tmp_path: Path) -> None:
    """El downgrade revierte ambas tablas y conserva el esquema anterior."""
    database_path = tmp_path / "data_hub_downgrade.db"
    config = _alembic_config(database_path)
    engine = create_engine(f"sqlite:///{database_path}")

    command.upgrade(config, _CURRENT_REVISION)
    command.downgrade(config, _PREVIOUS_REVISION)

    table_names = set(inspect(engine).get_table_names())
    assert "data_sources" not in table_names
    assert "data_facts" not in table_names
    assert "directorio_agricola" in table_names

    engine.dispose()
