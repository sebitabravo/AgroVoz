"""Pruebas de migración del consentimiento de historial."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"
_MIGRATIONS_DIR = _BACKEND_DIR / "migrations"
_PREVIOUS_REVISION = "d9f3a6b2c7e1"
_CURRENT_REVISION = "f2a8c1d7e4b6"


def _alembic_config(database_path: Path) -> Config:
    """Construye una configuración Alembic para una base temporal."""
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    return config


def test_upgrade_preserva_legacy_en_false_y_endurece_columna(
    tmp_path: Path,
) -> None:
    """El upgrade conserva filas y aplica default, NOT NULL y dominio booleano."""
    database_path = tmp_path / "history_consent_upgrade.db"
    config = _alembic_config(database_path)
    engine = create_engine(f"sqlite:///{database_path}")

    command.upgrade(config, _PREVIOUS_REVISION)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO user_prefs (
                    phone_hash,
                    dataset_consent,
                    alert_consent
                )
                VALUES (:phone_hash, 1, 1)
                """
            ),
            {"phone_hash": "a" * 64},
        )

    command.upgrade(config, _CURRENT_REVISION)

    columns = {column["name"]: column for column in inspect(engine).get_columns("user_prefs")}
    history_column = columns["history_consent"]
    assert history_column["nullable"] is False
    assert history_column["default"] is not None

    with engine.connect() as connection:
        legacy = connection.execute(
            text(
                """
                SELECT dataset_consent, alert_consent, history_consent
                FROM user_prefs
                WHERE phone_hash = :phone_hash
                """
            ),
            {"phone_hash": "a" * 64},
        ).one()
    assert legacy == (1, 1, 0)

    invalid_values: list[int | None] = [None, 2]
    for index, invalid_value in enumerate(invalid_values):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO user_prefs (
                        phone_hash,
                        dataset_consent,
                        alert_consent,
                        history_consent
                    )
                    VALUES (:phone_hash, 0, 0, :history_consent)
                    """
                ),
                {
                    "phone_hash": str(index) * 64,
                    "history_consent": invalid_value,
                },
            )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO user_prefs (
                    phone_hash,
                    dataset_consent,
                    alert_consent,
                    history_consent
                )
                VALUES (:phone_hash, 0, 0, 1)
                """
            ),
            {"phone_hash": "b" * 64},
        )
        independent = connection.execute(
            text(
                """
                SELECT dataset_consent, alert_consent, history_consent
                FROM user_prefs
                WHERE phone_hash = :phone_hash
                """
            ),
            {"phone_hash": "b" * 64},
        ).one()
    assert independent == (0, 0, 1)

    engine.dispose()


def test_downgrade_es_reversible_y_conserva_preferencias(
    tmp_path: Path,
) -> None:
    """El downgrade elimina la columna sin borrar la fila ni otros consentimientos."""
    database_path = tmp_path / "history_consent_downgrade.db"
    config = _alembic_config(database_path)
    engine = create_engine(f"sqlite:///{database_path}")

    command.upgrade(config, _CURRENT_REVISION)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO user_prefs (
                    phone_hash,
                    dataset_consent,
                    alert_consent,
                    history_consent
                )
                VALUES (:phone_hash, 1, 0, 1)
                """
            ),
            {"phone_hash": "c" * 64},
        )

    command.downgrade(config, _PREVIOUS_REVISION)

    columns = {column["name"] for column in inspect(engine).get_columns("user_prefs")}
    assert "history_consent" not in columns

    with engine.connect() as connection:
        preserved = connection.execute(
            text(
                """
                SELECT phone_hash, dataset_consent, alert_consent
                FROM user_prefs
                WHERE phone_hash = :phone_hash
                """
            ),
            {"phone_hash": "c" * 64},
        ).one()
    assert preserved == ("c" * 64, 1, 0)

    engine.dispose()
