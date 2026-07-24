"""Tests para historial de consultas con consentimiento (issue #195)."""

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.consultation_history import ConsultationHistory
from app.models.user_prefs import UserPrefs
from app.services.consultation_history_service import (
    delete_history,
    get_history,
    save_to_history_if_consented,
)


@pytest.fixture
def engine():
    """Engine SQLite en memoria."""
    eng = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(eng)
    return eng


def _create_session(engine):
    """Factory de sesión para monkeypatch."""
    return Session(engine)


class TestSaveWithConsent:
    """Verifica que solo guarde con consentimiento."""

    def test_con_consentimiento_guarda(self, engine) -> None:
        with Session(engine) as s:
            s.add(UserPrefs(
                phone_hash="hash_ok", comuna="Traiguén", dataset_consent=True
            ))
            s.commit()

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = save_to_history_if_consented(
                "hash_ok", "precio papa", "respuesta", "precio", "papa"
            )
            assert result is True

    def test_sin_consentimiento_no_guarda(self, engine) -> None:
        with Session(engine) as s:
            s.add(UserPrefs(
                phone_hash="hash_no", comuna="Traiguén", dataset_consent=False
            ))
            s.commit()

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = save_to_history_if_consented(
                "hash_no", "precio papa", "respuesta", "precio", "papa"
            )
            assert result is False

    def test_usuario_sin_prefs_no_guarda(self, engine) -> None:
        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = save_to_history_if_consented(
                "no_existe", "test", "test", "test"
            )
            assert result is False


class TestDeleteHistory:
    """Verifica borrado a pedido."""

    def test_borrar_historial(self, engine) -> None:
        with Session(engine) as s:
            s.add(UserPrefs(
                phone_hash="hash_del", comuna="Traiguén", dataset_consent=True
            ))
            s.add_all([
                ConsultationHistory(
                    phone_hash="hash_del", query_text="q1",
                    response_text="r1", intent="precio",
                ),
                ConsultationHistory(
                    phone_hash="hash_del", query_text="q2",
                    response_text="r2", intent="clima",
                ),
            ])
            s.commit()

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            count = delete_history("hash_del")
            assert count == 2

    def test_borrar_sin_historial(self, engine) -> None:
        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            count = delete_history("sin_historial")
            assert count == 0


class TestGetHistory:
    """Verifica lectura de historial."""

    def test_get_history_vacio(self, engine) -> None:
        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = get_history("no_existe")
            assert result == []

    def test_get_history_con_datos(self, engine) -> None:
        with Session(engine) as s:
            s.add(UserPrefs(
                phone_hash="hash_get", comuna="Traiguén", dataset_consent=True
            ))
            s.add_all([
                ConsultationHistory(
                    phone_hash="hash_get", query_text="q1",
                    response_text="r1", intent="precio", producto="papa",
                ),
                ConsultationHistory(
                    phone_hash="hash_get", query_text="q2",
                    response_text="r2", intent="clima",
                ),
            ])
            s.commit()

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = get_history("hash_get", limit=10)
            assert len(result) == 2
            assert result[0]["query"] == "q2"  # Más reciente primero
            assert result[0]["intent"] == "clima"
            assert result[1]["producto"] == "papa"
