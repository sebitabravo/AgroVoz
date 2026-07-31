"""Pruebas de la migración de identidad grupal PRODESAL."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"
_MIGRATIONS_DIR = _BACKEND_DIR / "migrations"
_PREVIOUS_REVISION = "c8e1f4a7b2d5"
_CURRENT_REVISION = "d9f3a6b2c7e1"


def _alembic_config(database_path: Path) -> Config:
    """Construye una configuración Alembic aislada para una base temporal."""
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    return config


def test_upgrade_preserva_legacy_y_aplica_constraints(tmp_path: Path) -> None:
    """El upgrade conserva filas y restringe los nuevos metadatos."""
    database_path = tmp_path / "prodesal_upgrade.db"
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
                VALUES (:phone_hash, 0, 0)
                """
            ),
            {"phone_hash": "a" * 64},
        )

    command.upgrade(config, _CURRENT_REVISION)

    columns = {column["name"] for column in inspect(engine).get_columns("user_prefs")}
    assert {"identity_type", "group_label", "localidad"} <= columns

    with engine.begin() as connection:
        legacy = connection.execute(
            text(
                """
                SELECT phone_hash, identity_type, group_label, localidad
                FROM user_prefs
                WHERE phone_hash = :phone_hash
                """
            ),
            {"phone_hash": "a" * 64},
        ).one()
        assert legacy == ("a" * 64, "individual", None, None)

        connection.execute(
            text(
                """
                INSERT INTO user_prefs (
                    phone_hash,
                    identity_type,
                    group_label,
                    localidad,
                    dataset_consent,
                    alert_consent
                )
                VALUES (
                    :phone_hash,
                    'prodesal_group',
                    'prodesal-traiguen-norte',
                    'Quilquén',
                    0,
                    0
                )
                """
            ),
            {"phone_hash": "b" * 64},
        )

    invalid_rows = [
        ("c" * 64, "otro", None, None),
        ("d" * 64, "prodesal_group", None, None),
        ("e" * 64, "prodesal_group", "   ", None),
        ("f" * 64, "individual", "prodesal-traiguen-norte", None),
        ("1" * 64, "prodesal_group", "g" * 101, None),
        ("2" * 64, "individual", None, "l" * 121),
        ("3" * 64, "individual", None, "   "),
        ("4" * 64, "prodesal_group", (" " * 100) + "g", None),
        ("5" * 64, "individual", None, (" " * 120) + "l"),
        ("6" * 64, "prodesal_group", "\t\n", None),
        ("7" * 64, "prodesal_group", "prodesal-norte ", None),
        ("8" * 64, "individual", None, "\tSector Norte\n"),
    ]
    for phone_hash, identity_type, group_label, localidad in invalid_rows:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO user_prefs (
                        phone_hash,
                        identity_type,
                        group_label,
                        localidad,
                        dataset_consent,
                        alert_consent
                    )
                    VALUES (
                        :phone_hash,
                        :identity_type,
                        :group_label,
                        :localidad,
                        0,
                        0
                    )
                    """
                ),
                {
                    "phone_hash": phone_hash,
                    "identity_type": identity_type,
                    "group_label": group_label,
                    "localidad": localidad,
                },
            )

    engine.dispose()


def test_downgrade_reversible_y_conserva_filas(tmp_path: Path) -> None:
    """El downgrade elimina columnas grupales sin borrar identidades."""
    database_path = tmp_path / "prodesal_downgrade.db"
    config = _alembic_config(database_path)
    engine = create_engine(f"sqlite:///{database_path}")

    command.upgrade(config, _CURRENT_REVISION)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO user_prefs (
                    phone_hash,
                    identity_type,
                    group_label,
                    localidad,
                    dataset_consent,
                    alert_consent
                )
                VALUES (
                    :phone_hash,
                    'prodesal_group',
                    'prodesal-traiguen-norte',
                    'Quilquén',
                    0,
                    0
                )
                """
            ),
            {"phone_hash": "9" * 64},
        )

    command.downgrade(config, _PREVIOUS_REVISION)

    columns = {column["name"] for column in inspect(engine).get_columns("user_prefs")}
    assert "identity_type" not in columns
    assert "group_label" not in columns
    assert "localidad" not in columns

    with engine.connect() as connection:
        stored_hash = connection.execute(
            text(
                """
                SELECT phone_hash
                FROM user_prefs
                WHERE phone_hash = :phone_hash
                """
            ),
            {"phone_hash": "9" * 64},
        ).scalar_one()
    assert stored_hash == "9" * 64

    engine.dispose()
