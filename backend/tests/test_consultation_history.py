"""Tests para modelo ConsultationHistory (issue #195)."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.consultation_history import ConsultationHistory


@pytest.fixture
def session() -> Session:
    """Sesión SQLite en memoria para tests."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class TestConsultationHistoryModel:
    """Verifica el modelo ConsultationHistory."""

    def test_crear_registro(self, session: Session) -> None:
        """Debe poder crear un registro de historial."""
        entry = ConsultationHistory(
            phone_hash="abc123def456",
            query_text="¿a cómo está la papa?",
            response_text="La papa está a $500 el kilo en Santiago.",
            producto="papa",
            intent="precio",
        )
        session.add(entry)
        session.commit()
        assert entry.id is not None

    def test_consultar_por_phone_hash(self, session: Session) -> None:
        """Debe poder consultar historial por phone_hash."""
        session.add(
            ConsultationHistory(
                phone_hash="hash1",
                query_text="precio papa",
                response_text="respuesta",
                intent="precio",
            )
        )
        session.add(
            ConsultationHistory(
                phone_hash="hash1",
                query_text="clima",
                response_text="respuesta clima",
                intent="clima",
            )
        )
        session.add(
            ConsultationHistory(
                phone_hash="hash2",
                query_text="otro",
                response_text="otra respuesta",
                intent="desconocido",
            )
        )
        session.commit()

        stmt = (
            select(ConsultationHistory)
            .where(ConsultationHistory.phone_hash == "hash1")
            .order_by(ConsultationHistory.created_at.asc())
        )
        results = session.scalars(stmt).all()
        assert len(results) == 2

    def test_borrar_historial(self, session: Session) -> None:
        """Debe poder borrar todo el historial de un phone_hash."""
        session.add(
            ConsultationHistory(
                phone_hash="borrar_hash",
                query_text="test",
                response_text="test",
                intent="test",
            )
        )
        session.commit()
        assert session.scalar(
            select(ConsultationHistory).where(
                ConsultationHistory.phone_hash == "borrar_hash"
            )
        ) is not None

        # Borrar
        session.query(ConsultationHistory).filter(
            ConsultationHistory.phone_hash == "borrar_hash"
        ).delete()
        session.commit()
        assert session.scalar(
            select(ConsultationHistory).where(
                ConsultationHistory.phone_hash == "borrar_hash"
            )
        ) is None

    def test_producto_nullable(self, session: Session) -> None:
        """El campo producto debe aceptar None."""
        entry = ConsultationHistory(
            phone_hash="hash3",
            query_text="¿cómo está el clima?",
            response_text="Soleado, 22°C.",
            producto=None,
            intent="clima",
        )
        session.add(entry)
        session.commit()
        assert entry.producto is None
        assert entry.id is not None
