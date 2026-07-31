"""Pruebas de la migración de parcelas y consentimiento específico (C5)."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"
_MIGRATIONS_DIR = _BACKEND_DIR / "migrations"
_PREVIOUS_REVISION = "a7c4e9b2d1f6"
_CURRENT_REVISION = "b3f8e2a91c47"


def _alembic_config(database_path: Path) -> Config:
    """Construye una configuración Alembic aislada."""
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    return config


def _engine(database_path: Path):  # type: ignore[no-untyped-def]
    """Crea SQLite con claves foráneas activas para validar cascada."""
    engine = create_engine(f"sqlite:///{database_path}")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection: object, _record: object) -> None:
        cursor = connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def test_upgrade_crea_consentimiento_y_parcelas_acotadas(tmp_path: Path) -> None:
    """Legacy queda sin opt-in y la tabla aplica integridad y cascada."""
    database_path = tmp_path / "parcelas_upgrade.db"
    config = _alembic_config(database_path)
    engine = _engine(database_path)

    command.upgrade(config, _PREVIOUS_REVISION)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO user_prefs (
                    phone_hash,
                    dataset_consent,
                    alert_consent,
                    history_consent,
                    expense_consent
                )
                VALUES (:phone_hash, 0, 0, 0, 0)
                """
            ),
            {"phone_hash": "a" * 64},
        )

    command.upgrade(config, _CURRENT_REVISION)

    inspector = inspect(engine)
    assert "parcelas" in inspector.get_table_names()
    columns = {column["name"]: column for column in inspector.get_columns("user_prefs")}
    assert columns["parcela_consent"]["nullable"] is False
    assert columns["parcela_consent"]["default"] is not None

    with engine.begin() as connection:
        legacy_consent = connection.execute(
            text("SELECT parcela_consent FROM user_prefs WHERE phone_hash = :phone_hash"),
            {"phone_hash": "a" * 64},
        ).scalar_one()
        assert legacy_consent == 0

        connection.execute(
            text("UPDATE user_prefs SET parcela_consent = 1 WHERE phone_hash = :phone_hash"),
            {"phone_hash": "a" * 64},
        )
        connection.execute(
            text(
                """
                INSERT INTO parcelas (
                    phone_hash,
                    cultivo,
                    superficie_ha,
                    comuna,
                    expires_at
                )
                VALUES (
                    :phone_hash,
                    'papa',
                    2.5,
                    'traiguén',
                    '2027-07-30 00:00:00'
                )
                """
            ),
            {"phone_hash": "a" * 64},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO parcelas (
                    phone_hash,
                    cultivo,
                    superficie_ha,
                    comuna,
                    expires_at
                )
                VALUES (
                    :phone_hash,
                    'papa',
                    0,
                    'traiguén',
                    '2027-07-30 00:00:00'
                )
                """
            ),
            {"phone_hash": "a" * 64},
        )

    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM user_prefs WHERE phone_hash = :phone_hash"),
            {"phone_hash": "a" * 64},
        )
        remaining = connection.execute(text("SELECT count(*) FROM parcelas")).scalar_one()
    assert remaining == 0
    engine.dispose()


def test_downgrade_elimina_feature_y_conserva_preferencias(tmp_path: Path) -> None:
    """El downgrade quita tabla/consentimiento sin borrar la identidad."""
    database_path = tmp_path / "parcelas_downgrade.db"
    config = _alembic_config(database_path)
    engine = _engine(database_path)

    command.upgrade(config, _CURRENT_REVISION)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO user_prefs (
                    phone_hash,
                    dataset_consent,
                    alert_consent,
                    history_consent,
                    expense_consent,
                    parcela_consent
                )
                VALUES (:phone_hash, 1, 0, 0, 0, 1)
                """
            ),
            {"phone_hash": "b" * 64},
        )

    command.downgrade(config, _PREVIOUS_REVISION)

    inspector = inspect(engine)
    assert "parcelas" not in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("user_prefs")}
    assert "parcela_consent" not in columns
    with engine.connect() as connection:
        preserved = connection.execute(
            text("SELECT phone_hash, dataset_consent FROM user_prefs WHERE phone_hash = :phone_hash"),
            {"phone_hash": "b" * 64},
        ).one()
    assert preserved == ("b" * 64, 1)
    engine.dispose()
