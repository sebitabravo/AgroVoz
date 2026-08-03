"""Pruebas de la migración de coordenadas GPS en user_prefs."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"
_MIGRATIONS_DIR = _BACKEND_DIR / "migrations"
_PREVIOUS_REVISION = "b3f8e2a91c47"
_CURRENT_REVISION = "c5d9e7f1a2b3"


def _alembic_config(database_path: Path) -> Config:
    """Construye una configuración Alembic aislada para SQLite temporal."""
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    return config


def test_upgrade_preserva_legacy_y_guarda_coordenadas(tmp_path: Path) -> None:
    """El upgrade deja GPS nullable y aplica rangos WGS84."""
    database_path = tmp_path / "location_upgrade.db"
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
                    alert_consent,
                    history_consent,
                    expense_consent,
                    parcela_consent
                )
                VALUES (:phone_hash, 0, 0, 0, 0, 0)
                """
            ),
            {"phone_hash": "a" * 64},
        )

    command.upgrade(config, _CURRENT_REVISION)

    columns = {column["name"] for column in inspect(engine).get_columns("user_prefs")}
    assert {"lat", "lng"} <= columns
    with engine.begin() as connection:
        legacy = connection.execute(
            text("SELECT lat, lng FROM user_prefs WHERE phone_hash = :phone_hash"),
            {"phone_hash": "a" * 64},
        ).one()
        assert legacy == (None, None)
        connection.execute(
            text(
                """
                INSERT INTO user_prefs (
                    phone_hash,
                    lat,
                    lng,
                    dataset_consent,
                    alert_consent,
                    history_consent,
                    expense_consent,
                    parcela_consent
                )
                VALUES (:phone_hash, :lat, :lng, 0, 0, 0, 0, 0)
                """
            ),
            {"phone_hash": "b" * 64, "lat": -33.45, "lng": -70.65},
        )

    invalid_coordinates = [
        ("c" * 64, 91.0, -72.0),
        ("d" * 64, -38.0, 181.0),
        ("e" * 64, -38.0, None),
    ]
    for phone_hash, lat, lng in invalid_coordinates:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO user_prefs (
                        phone_hash,
                        lat,
                        lng,
                        dataset_consent,
                        alert_consent,
                        history_consent,
                        expense_consent,
                        parcela_consent
                    )
                    VALUES (:phone_hash, :lat, :lng, 0, 0, 0, 0, 0)
                    """
                ),
                {"phone_hash": phone_hash, "lat": lat, "lng": lng},
            )

    engine.dispose()


def test_downgrade_elimina_coordenadas_y_conserva_fila(tmp_path: Path) -> None:
    """El downgrade retira solo los campos GPS."""
    database_path = tmp_path / "location_downgrade.db"
    config = _alembic_config(database_path)
    engine = create_engine(f"sqlite:///{database_path}")

    command.upgrade(config, _CURRENT_REVISION)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO user_prefs (
                    phone_hash,
                    lat,
                    lng,
                    dataset_consent,
                    alert_consent,
                    history_consent,
                    expense_consent,
                    parcela_consent
                )
                VALUES (:phone_hash, -33.45, -70.65, 0, 0, 0, 0, 0)
                """
            ),
            {"phone_hash": "f" * 64},
        )

    command.downgrade(config, _PREVIOUS_REVISION)
    columns = {column["name"] for column in inspect(engine).get_columns("user_prefs")}
    assert "lat" not in columns
    assert "lng" not in columns
    with engine.connect() as connection:
        stored_hash = connection.execute(
            text("SELECT phone_hash FROM user_prefs WHERE phone_hash = :phone_hash"),
            {"phone_hash": "f" * 64},
        ).scalar_one()
    assert stored_hash == "f" * 64
    engine.dispose()

