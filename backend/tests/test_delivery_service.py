"""Pruebas focalizadas para las transiciones de entrega."""

import datetime
import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core import database
from app.models.consultation import Consultation
from app.services import delivery_service


def _crear_consulta(
    db: Session,
    *,
    delivery_status: str = "pending",
    delivered_at: datetime.datetime | None = None,
    delivery_error_code: str | None = None,
    requires_review: bool = False,
    created_at: datetime.datetime | None = None,
) -> int:
    consulta = Consultation(
        phone_hash="a" * 64,
        intent="precio",
        query_text="precio de la papa",
        response_text="La papa está a mil pesos",
        audio_duration_ms=1_000,
        latency_ms=500,
        delivery_status=delivery_status,
        delivered_at=delivered_at,
        delivery_error_code=delivery_error_code,
        requires_review=requires_review,
    )
    if created_at is not None:
        consulta.created_at = created_at
    db.add(consulta)
    db.commit()
    return consulta.id


def _obtener_consulta(db: Session, consultation_id: int) -> Consultation:
    db.expire_all()
    consulta = db.get(Consultation, consultation_id)
    assert consulta is not None
    return consulta


def test_mark_delivery_delivered_registra_utc_y_limpia_error(
    db: Session,
    monkeypatch,
) -> None:
    momento_utc = datetime.datetime(2026, 7, 29, 15, 30, tzinfo=datetime.UTC)
    consultation_id = _crear_consulta(
        db,
        delivery_status="failed",
        delivery_error_code="openwa_timeout",
        requires_review=True,
    )
    monkeypatch.setattr(delivery_service, "_utc_now", lambda: momento_utc)

    resultado = delivery_service.mark_delivery_delivered(consultation_id)

    consulta = _obtener_consulta(db, consultation_id)
    assert resultado is True
    assert consulta.delivery_status == "delivered"
    # SQLite no conserva el offset, pero el valor proviene del reloj UTC inyectado.
    assert consulta.delivered_at == momento_utc.replace(tzinfo=None)
    assert consulta.delivery_error_code is None
    assert consulta.requires_review is True


def test_mark_delivery_failed_limpia_fecha_y_solicita_revision(
    db: Session,
) -> None:
    consultation_id = _crear_consulta(
        db,
        delivery_status="delivered",
        delivered_at=datetime.datetime(2026, 7, 29, 15, 30, tzinfo=datetime.UTC),
    )

    resultado = delivery_service.mark_delivery_failed(
        consultation_id,
        "openwa_timeout",
    )

    consulta = _obtener_consulta(db, consultation_id)
    assert resultado is True
    assert consulta.delivery_status == "failed"
    assert consulta.delivered_at is None
    assert consulta.delivery_error_code == "openwa_timeout"
    assert consulta.requires_review is True
    assert consulta.query_text == ""
    assert consulta.response_text == ""


def test_redact_consultation_content_es_idempotente_y_conserva_metricas(
    db: Session,
) -> None:
    """La limpieza post-entrega no borra estado ni dimensiones agregables."""
    consultation_id = _crear_consulta(db, delivery_status="delivered")

    first = delivery_service.redact_consultation_content(consultation_id)
    retry = delivery_service.redact_consultation_content(consultation_id)

    consulta = _obtener_consulta(db, consultation_id)
    assert first is True
    assert retry is True
    assert consulta.query_text == ""
    assert consulta.response_text == ""
    assert consulta.intent == "precio"
    assert consulta.delivery_status == "delivered"


def test_redact_stale_consultation_content_aplica_cutoff_estricto(
    db: Session,
) -> None:
    """Solo staging anterior a 24 horas se elimina en una operación bulk."""
    now = datetime.datetime(2026, 7, 30, 12, tzinfo=datetime.UTC)
    old_id = _crear_consulta(
        db,
        created_at=now - datetime.timedelta(hours=24, microseconds=1),
    )
    cutoff_id = _crear_consulta(
        db,
        created_at=now - datetime.timedelta(hours=24),
    )
    recent_id = _crear_consulta(
        db,
        created_at=now - datetime.timedelta(hours=1),
    )

    records_redacted = delivery_service.redact_stale_consultation_content(now=now)

    assert records_redacted == 1
    assert _obtener_consulta(db, old_id).query_text == ""
    assert _obtener_consulta(db, old_id).response_text == ""
    assert _obtener_consulta(db, cutoff_id).query_text == "precio de la papa"
    assert _obtener_consulta(db, recent_id).response_text == ("La papa está a mil pesos")


def test_mark_delivery_failed_rechaza_mensaje_con_pii_sin_mutar(
    db: Session,
    caplog,
) -> None:
    consultation_id = _crear_consulta(db)
    mensaje_sensible = "Timeout enviando a +56 9 1234 5678"

    with caplog.at_level(logging.WARNING):
        resultado = delivery_service.mark_delivery_failed(
            consultation_id,
            mensaje_sensible,
        )

    consulta = _obtener_consulta(db, consultation_id)
    assert resultado is False
    assert consulta.delivery_status == "pending"
    assert consulta.delivery_error_code is None
    assert consulta.requires_review is False
    assert mensaje_sensible not in caplog.text
    assert "+56 9 1234 5678" not in caplog.text


def test_transiciones_retornan_false_para_consulta_inexistente() -> None:
    assert delivery_service.mark_delivery_delivered(999_999) is False
    assert delivery_service.mark_delivery_failed(999_999, "openwa_send_failed") is False


class _FailingSession:
    def __init__(self) -> None:
        self.rollback_called = False
        self.close_called = False

    def get(
        self,
        model: type[Consultation],
        consultation_id: int,
    ) -> Consultation | None:
        raise SQLAlchemyError("falló para +56 9 8765 4321")

    def rollback(self) -> None:
        self.rollback_called = True

    def close(self) -> None:
        self.close_called = True


def test_sqlalchemy_error_no_derriba_pipeline_ni_filtra_excepcion(
    monkeypatch,
    caplog,
) -> None:
    failing_session = _FailingSession()
    monkeypatch.setattr(database, "SessionLocal", lambda: failing_session)

    with caplog.at_level(logging.ERROR):
        resultado = delivery_service.mark_delivery_delivered(42)

    assert resultado is False
    assert failing_session.rollback_called is True
    assert failing_session.close_called is True
    assert "+56 9 8765 4321" not in caplog.text
