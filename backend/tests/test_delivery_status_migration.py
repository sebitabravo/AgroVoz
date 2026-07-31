"""Pruebas de la migración del estado efectivo de entrega."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

REVISION_ANTERIOR = "b7c3e91a4d28"
REVISION_ENTREGA = "e4b7c2d91a63"


def _configurar_alembic(database_url: str) -> Config:
    """Crea una configuración Alembic aislada para una base temporal."""
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_upgrade_conserva_consultas_historicas_como_pending(
    tmp_path: Path,
) -> None:
    """El upgrade asigna ``pending`` a filas previas sin inventar una entrega."""
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    config = _configurar_alembic(database_url)
    command.upgrade(config, REVISION_ANTERIOR)

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO consultations (
                    phone_hash,
                    intent,
                    query_text,
                    response_text,
                    audio_duration_ms,
                    latency_ms
                )
                VALUES (
                    :phone_hash,
                    'precio',
                    'precio de la papa',
                    '500 pesos por kilo',
                    0,
                    0
                )
                """
            ),
            {"phone_hash": "a" * 64},
        )

    command.upgrade(config, REVISION_ENTREGA)

    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT delivery_status, delivered_at, delivery_error_code
                FROM consultations
                """
            )
        ).one()

    assert row.delivery_status == "pending"
    assert row.delivered_at is None
    assert row.delivery_error_code is None

    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("consultations")}
    assert {
        "delivery_status",
        "delivered_at",
        "delivery_error_code",
    }.issubset(columns)
    constraints = {constraint["name"] for constraint in inspector.get_check_constraints("consultations")}
    assert "ck_consultations_delivery_status" in constraints
    engine.dispose()


def test_downgrade_elimina_campos_de_entrega(tmp_path: Path) -> None:
    """El downgrade restaura el esquema de la revisión anterior."""
    database_url = f"sqlite:///{tmp_path / 'downgrade.db'}"
    config = _configurar_alembic(database_url)
    command.upgrade(config, REVISION_ENTREGA)
    command.downgrade(config, REVISION_ANTERIOR)

    engine = create_engine(database_url)
    columns = {column["name"] for column in inspect(engine).get_columns("consultations")}

    assert "delivery_status" not in columns
    assert "delivered_at" not in columns
    assert "delivery_error_code" not in columns
    engine.dispose()
