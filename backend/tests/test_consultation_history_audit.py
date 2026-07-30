"""Pruebas del modelo y migración append-only del historial (#201)."""

import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.consultation_history import (
    ConsultationHistory,
    ConsultationHistoryDeletionAudit,
)

REVISION_ANTERIOR = "e4b7c2d91a63"
REVISION_AUDITORIA = "f2a9c4e7d1b6"
REVISION_VINCULO = "c8e1f4a7b2d5"
TABLE_NAME = "consultation_history_deletion_audit"


def _configurar_alembic(database_url: str) -> Config:
    """Crea una configuración Alembic aislada para una base temporal."""
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _insertar_evento(
    connection: Connection,
    **overrides: object,
) -> None:
    """Inserta un evento válido con reemplazos focales para probar constraints."""
    values: dict[str, object] = {
        "event_id": str(uuid4()),
        "subject_token": "a" * 64,
        "key_version": 1,
        "reason": "user_request",
        "records_deleted": 2,
        "requested_via": "verified_whatsapp",
        "cutoff_at": None,
        "deleted_at": datetime.now(UTC),
        "outcome": "completed",
    }
    values.update(overrides)
    connection.execute(
        text(
            f"""
            INSERT INTO {TABLE_NAME} (
                event_id,
                subject_token,
                key_version,
                reason,
                records_deleted,
                requested_via,
                cutoff_at,
                deleted_at,
                outcome
            )
            VALUES (
                :event_id,
                :subject_token,
                :key_version,
                :reason,
                :records_deleted,
                :requested_via,
                :cutoff_at,
                :deleted_at,
                :outcome
            )
            """
        ),
        values,
    )


def _crear_base_migrada(tmp_path: Path, filename: str) -> tuple[str, Config]:
    """Migra una base temporal hasta la revisión de auditoría."""
    database_url = f"sqlite:///{tmp_path / filename}"
    config = _configurar_alembic(database_url)
    command.upgrade(config, REVISION_AUDITORIA)
    return database_url, config


def _crear_base_con_vinculo(
    tmp_path: Path,
    filename: str,
) -> tuple[str, Config]:
    """Migra una base nueva hasta el vínculo idempotente del historial."""
    database_url = f"sqlite:///{tmp_path / filename}"
    config = _configurar_alembic(database_url)
    command.upgrade(config, REVISION_VINCULO)
    return database_url, config


def test_alembic_no_desactiva_loggers_de_la_aplicacion(
    tmp_path: Path,
) -> None:
    """Una migración embebida conserva la observabilidad del proceso."""
    app_logger = logging.getLogger("app.services.audio_service")
    previous_disabled = app_logger.disabled
    app_logger.disabled = False
    try:
        _crear_base_migrada(tmp_path, "logging.db")
        assert app_logger.disabled is False
    finally:
        app_logger.disabled = previous_disabled


def test_modelo_guarda_evento_sin_pii_y_con_fecha_utc() -> None:
    """El modelo contiene solo evidencia acotada y genera deleted_at en UTC."""
    engine = create_engine("sqlite:///:memory:")
    ConsultationHistoryDeletionAudit.__table__.create(engine)
    entry = ConsultationHistoryDeletionAudit(
        event_id=str(uuid4()),
        subject_token="a" * 64,
        key_version=1,
        reason="user_request",
        records_deleted=1,
        requested_via="verified_whatsapp",
        cutoff_at=None,
        outcome="completed",
    )

    with Session(engine, expire_on_commit=False) as session:
        session.add(entry)
        session.flush()

        assert entry.id is not None
        assert entry.deleted_at.tzinfo is UTC

    assert set(ConsultationHistoryDeletionAudit.__table__.columns.keys()) == {
        "id",
        "event_id",
        "subject_token",
        "key_version",
        "reason",
        "records_deleted",
        "requested_via",
        "cutoff_at",
        "deleted_at",
        "outcome",
    }
    engine.dispose()


def test_migracion_crea_constraints_e_indices(tmp_path: Path) -> None:
    """El esquema migrado fija dominios, scopes e idempotencia en la DB."""
    database_url, _ = _crear_base_migrada(tmp_path, "schema.db")
    engine = create_engine(database_url)
    inspector = inspect(engine)

    assert TABLE_NAME in inspector.get_table_names()
    constraint_names = {constraint["name"] for constraint in inspector.get_check_constraints(TABLE_NAME)}
    assert {
        "ck_history_deletion_audit_subject_scope",
        "ck_history_deletion_audit_cutoff_scope",
        "ck_history_deletion_audit_records_deleted",
        "ck_history_deletion_audit_outcome",
    }.issubset(constraint_names)
    unique_names = {constraint["name"] for constraint in inspector.get_unique_constraints(TABLE_NAME)}
    assert "uq_consultation_history_deletion_audit_event_id" in unique_names
    index_names = {index["name"] for index in inspector.get_indexes(TABLE_NAME)}
    assert "ix_consultation_history_deletion_audit_deleted_at" in index_names
    engine.dispose()


def test_event_id_duplicado_es_rechazado(tmp_path: Path) -> None:
    """El identificador único permite reintentos idempotentes en el servicio."""
    database_url, _ = _crear_base_migrada(tmp_path, "unique.db")
    engine = create_engine(database_url)
    event_id = str(uuid4())

    with engine.begin() as connection:
        _insertar_evento(connection, event_id=event_id)

    with (
        pytest.raises(IntegrityError),
        engine.begin() as connection,
    ):
        _insertar_evento(connection, event_id=event_id)

    engine.dispose()


@pytest.mark.parametrize(
    "overrides",
    [
        {"records_deleted": -1},
        {"key_version": 0},
        {"reason": "texto_libre"},
        {"requested_via": "texto_libre"},
        {"subject_token": None},
        {"cutoff_at": datetime.now(UTC)},
        {"records_deleted": 0, "outcome": "completed"},
        {
            "reason": "ttl",
            "subject_token": "a" * 64,
            "requested_via": "system_retention",
            "cutoff_at": datetime.now(UTC),
        },
        {
            "reason": "ttl",
            "subject_token": None,
            "requested_via": "system_retention",
            "cutoff_at": None,
        },
    ],
)
def test_constraints_rechazan_eventos_inconsistentes(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    """Los dominios inválidos y scopes cruzados no llegan a persistirse."""
    database_url, _ = _crear_base_migrada(
        tmp_path,
        f"constraint-{uuid4()}.db",
    )
    engine = create_engine(database_url)

    with (
        pytest.raises(IntegrityError),
        engine.begin() as connection,
    ):
        _insertar_evento(connection, **overrides)

    engine.dispose()


def test_ttl_agregado_exige_cutoff_y_no_guarda_sujeto(tmp_path: Path) -> None:
    """Una purga TTL válida registra solo su corte y cantidad agregada."""
    database_url, _ = _crear_base_migrada(tmp_path, "ttl.db")
    engine = create_engine(database_url)

    with engine.begin() as connection:
        _insertar_evento(
            connection,
            subject_token=None,
            reason="ttl",
            requested_via="system_retention",
            cutoff_at=datetime.now(UTC),
        )

    with engine.connect() as connection:
        row = connection.execute(
            text(
                f"""
                SELECT subject_token, reason, requested_via, cutoff_at
                FROM {TABLE_NAME}
                """
            )
        ).one()

    assert row.subject_token is None
    assert row.reason == "ttl"
    assert row.requested_via == "system_retention"
    assert row.cutoff_at is not None
    engine.dispose()


def test_revocacion_desde_admin_api_es_un_origen_valido(tmp_path: Path) -> None:
    """El canal administrativo real puede auditar una revocación consentida."""
    database_url, _ = _crear_base_migrada(tmp_path, "admin-api.db")
    engine = create_engine(database_url)

    with engine.begin() as connection:
        _insertar_evento(
            connection,
            reason="consent_revoked",
            requested_via="admin_api",
        )

    with engine.connect() as connection:
        row = connection.execute(
            text(
                f"""
                SELECT reason, requested_via
                FROM {TABLE_NAME}
                """
            )
        ).one()

    assert row.reason == "consent_revoked"
    assert row.requested_via == "admin_api"
    engine.dispose()


def test_triggers_rechazan_update_y_delete(tmp_path: Path) -> None:
    """La evidencia persiste aunque se intente modificarla o eliminarla."""
    database_url, _ = _crear_base_migrada(tmp_path, "append-only.db")
    engine = create_engine(database_url)
    event_id = str(uuid4())

    with engine.begin() as connection:
        _insertar_evento(connection, event_id=event_id)

    with (
        pytest.raises(IntegrityError, match="append-only"),
        engine.begin() as connection,
    ):
        connection.execute(
            text(
                f"""
                UPDATE {TABLE_NAME}
                SET records_deleted = 3
                WHERE event_id = :event_id
                """
            ),
            {"event_id": event_id},
        )

    with (
        pytest.raises(IntegrityError, match="append-only"),
        engine.begin() as connection,
    ):
        connection.execute(
            text(f"DELETE FROM {TABLE_NAME} WHERE event_id = :event_id"),
            {"event_id": event_id},
        )

    with engine.connect() as connection:
        count = connection.scalar(
            text(
                f"""
                SELECT COUNT(*)
                FROM {TABLE_NAME}
                WHERE event_id = :event_id
                """
            ),
            {"event_id": event_id},
        )

    assert count == 1
    engine.dispose()


def test_downgrade_quita_triggers_antes_de_la_tabla(tmp_path: Path) -> None:
    """El downgrade restaura la revisión anterior sin objetos huérfanos."""
    database_url, config = _crear_base_migrada(tmp_path, "downgrade-audit.db")
    command.downgrade(config, REVISION_ANTERIOR)
    engine = create_engine(database_url)

    assert TABLE_NAME not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        triggers = connection.execute(
            text(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'trigger'
                  AND name IN (
                    'trg_history_deletion_audit_no_update',
                    'trg_history_deletion_audit_no_delete'
                  )
                """
            )
        ).all()

    assert triggers == []
    engine.dispose()


def test_modelo_declara_vinculo_unico_y_set_null() -> None:
    """El contrato ORM conserva historial si desaparece su métrica fuente."""
    table = ConsultationHistory.__table__
    column = table.c.source_consultation_id
    foreign_key = next(iter(column.foreign_keys))
    unique_names = {
        constraint.name for constraint in table.constraints if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert column.nullable is True
    assert foreign_key.target_fullname == "consultations.id"
    assert foreign_key.name == "fk_consultation_history_source_consultation_id"
    assert foreign_key.ondelete == "SET NULL"
    assert "uq_consultation_history_source_consultation_id" in unique_names


def test_upgrade_desde_base_nueva_crea_vinculo_nombrado(tmp_path: Path) -> None:
    """Alembic crea columna, FK e idempotencia desde una base vacía."""
    database_url, _ = _crear_base_con_vinculo(tmp_path, "fresh-link.db")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("consultation_history")}
    foreign_keys = {
        foreign_key["name"]: foreign_key for foreign_key in inspector.get_foreign_keys("consultation_history")
    }
    unique_names = {constraint["name"] for constraint in inspector.get_unique_constraints("consultation_history")}

    assert columns["source_consultation_id"]["nullable"] is True
    source_fk = foreign_keys["fk_consultation_history_source_consultation_id"]
    assert source_fk["referred_table"] == "consultations"
    assert source_fk["referred_columns"] == ["id"]
    assert source_fk["options"]["ondelete"] == "SET NULL"
    assert "uq_consultation_history_source_consultation_id" in unique_names
    engine.dispose()


def test_vinculo_es_unico_y_delete_fuente_hace_set_null(
    tmp_path: Path,
) -> None:
    """Una consulta se vincula una vez y su borrado no elimina el historial."""
    database_url, _ = _crear_base_con_vinculo(tmp_path, "set-null.db")
    engine = create_engine(database_url)
    now = datetime.now(UTC)

    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.execute(
            text(
                """
                INSERT INTO user_prefs (phone_hash, comuna)
                VALUES (:phone_hash, 'Traiguén')
                """
            ),
            {"phone_hash": "a" * 64},
        )
        connection.execute(
            text(
                """
                INSERT INTO consultations (
                    id,
                    phone_hash,
                    intent,
                    query_text,
                    response_text,
                    audio_duration_ms,
                    latency_ms
                )
                VALUES (
                    101,
                    :phone_hash,
                    'precio',
                    'precio papa',
                    '500 pesos',
                    0,
                    0
                )
                """
            ),
            {"phone_hash": "a" * 64},
        )
        connection.execute(
            text(
                """
                INSERT INTO consultation_history (
                    id,
                    source_consultation_id,
                    phone_hash,
                    query_text,
                    response_text,
                    intent,
                    created_at
                )
                VALUES
                    (201, 101, :phone_hash, 'q1', 'r1', 'precio', :created_at),
                    (202, NULL, :phone_hash, 'q2', 'r2', 'precio', :created_at)
                """
            ),
            {"phone_hash": "a" * 64, "created_at": now},
        )

    with (
        pytest.raises(IntegrityError),
        engine.begin() as connection,
    ):
        connection.execute(
            text(
                """
                UPDATE consultation_history
                SET source_consultation_id = 101
                WHERE id = 202
                """
            )
        )

    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.execute(text("DELETE FROM consultations WHERE id = 101"))

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, source_consultation_id
                FROM consultation_history
                ORDER BY id
                """
            )
        ).all()

    assert [(row.id, row.source_consultation_id) for row in rows] == [
        (201, None),
        (202, None),
    ]
    engine.dispose()


def test_downgrade_elimina_solo_vinculo(tmp_path: Path) -> None:
    """Volver a la revisión de auditoría conserva ambas tablas previas."""
    database_url, config = _crear_base_con_vinculo(
        tmp_path,
        "downgrade-link.db",
    )
    command.downgrade(config, REVISION_AUDITORIA)
    engine = create_engine(database_url)
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("consultation_history")}

    assert "source_consultation_id" not in columns
    assert "consultation_history" in inspector.get_table_names()
    assert TABLE_NAME in inspector.get_table_names()
    engine.dispose()
