"""Tests del historial consentido y del borrado auditado (#195, #201)."""

import asyncio
import logging
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base
from app.jobs.purge_consultation_history import main as purge_job_main
from app.main import (
    _CONSULTATION_HISTORY_PURGE_INTERVAL_SECONDS,
    _CONSULTATION_STAGING_CLEANUP_INTERVAL_SECONDS,
    _cancel_background_task,
    _consultation_history_scheduler,
    _consultation_staging_cleanup_scheduler,
    _start_consultation_history_scheduler,
    _start_consultation_staging_cleanup_scheduler,
)
from app.models.consultation import Consultation
from app.models.consultation_history import (
    ConsultationHistory,
    ConsultationHistoryDeletionAudit,
)
from app.models.user_prefs import UserPrefs
from app.services.consultation_history_service import (
    HISTORY_CONTEXT_INTENT_MAX_CHARS,
    HISTORY_CONTEXT_PRODUCT_MAX_CHARS,
    HISTORY_CONTEXT_QUERY_MAX_CHARS,
    HISTORY_CONTEXT_RESPONSE_MAX_CHARS,
    DeliveredHistorySaveOutcome,
    HistoryOperationError,
    HistoryPurgeResult,
    LatestConsultationContext,
    delete_history,
    get_history,
    get_latest_consultation_context,
    purge_expired_history,
    save_delivered_consultation_to_history,
    save_to_history_if_consented,
)

AUDIT_KEY = "k" * 32


@pytest.fixture
def engine() -> Iterator[Engine]:
    """Entrega un engine SQLite aislado por test."""
    eng = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def history_enabled() -> Iterator[None]:
    """Activa el historial con una clave de auditoría válida."""
    with (
        patch.object(settings, "consultation_history_enabled", True),
        patch.object(
            settings,
            "consultation_history_audit_key",
            SecretStr(AUDIT_KEY),
        ),
        patch.object(settings, "consultation_history_ttl_days", 28),
    ):
        yield


def _create_session(engine: Engine) -> Session:
    """Crea una sesión asociada al engine aislado."""
    return Session(engine)


def _seed_user(
    engine: Engine,
    phone_hash: str,
    *,
    consent: bool,
    history_entries: int = 0,
) -> None:
    """Crea preferencias y una cantidad acotada de consultas."""
    with Session(engine) as session:
        session.add(
            UserPrefs(
                phone_hash=phone_hash,
                comuna="Traiguén",
                history_consent=consent,
            )
        )
        session.add_all(
            [
                ConsultationHistory(
                    phone_hash=phone_hash,
                    query_text=f"consulta-{index}",
                    response_text=f"respuesta-{index}",
                    intent="precio",
                )
                for index in range(history_entries)
            ]
        )
        session.commit()


def _count_rows(engine: Engine, model: type[object]) -> int:
    """Cuenta filas de un modelo en la base aislada."""
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def _seed_consultation(engine: Engine, consultation: Consultation) -> None:
    """Persiste una consulta fuente con estado controlado."""
    with Session(engine) as session:
        session.add(consultation)
        session.commit()


def _seed_history_at(
    engine: Engine,
    phone_hash: str,
    created_at_values: list[datetime],
) -> None:
    """Agrega consultas con tiempos controlados para probar el cutoff."""
    with Session(engine) as session:
        session.add_all(
            [
                ConsultationHistory(
                    phone_hash=phone_hash,
                    query_text=f"{phone_hash}-{index}",
                    response_text=f"respuesta-{index}",
                    intent="precio",
                    created_at=created_at,
                )
                for index, created_at in enumerate(created_at_values)
            ]
        )
        session.commit()


class TestSaveWithConsent:
    """Verifica feature gate y consentimiento antes de persistir."""

    @pytest.mark.parametrize(
        "intent",
        ["resumen", "saludo", "feedback", "alerta", "desconocido"],
    )
    def test_intencion_no_sustantiva_no_abre_sesion(
        self,
        history_enabled: None,
        intent: str,
    ) -> None:
        """Una respuesta meta no reemplaza la última consulta útil."""
        with patch("app.services.consultation_history_service.SessionLocal") as session_factory:
            result = save_to_history_if_consented(
                "a" * 64,
                "consulta meta",
                "respuesta meta",
                intent,
            )

        assert result is False
        session_factory.assert_not_called()

    def test_gate_apagado_no_abre_sesion(self) -> None:
        """El default seguro retorna antes de tocar SQLite."""
        with (
            patch.object(settings, "consultation_history_enabled", False),
            patch("app.services.consultation_history_service.SessionLocal") as session_factory,
        ):
            result = save_to_history_if_consented(
                "hash",
                "precio papa",
                "respuesta",
                "precio",
                "papa",
            )

        assert result is False
        session_factory.assert_not_called()

    def test_gate_activo_con_consentimiento_guarda(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """El opt-in se revalida incluso con el feature gate activo."""
        _seed_user(engine, "hash_ok", consent=True)

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = save_to_history_if_consented(
                "hash_ok",
                "precio papa",
                "respuesta",
                "precio",
                "papa",
            )

        assert result is True
        assert _count_rows(engine, ConsultationHistory) == 1

    def test_gate_activo_sin_consentimiento_no_guarda(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """El flag nunca reemplaza el consentimiento del agricultor."""
        _seed_user(engine, "hash_no", consent=False)

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = save_to_history_if_consented(
                "hash_no",
                "precio papa",
                "respuesta",
                "precio",
                "papa",
            )

        assert result is False
        assert _count_rows(engine, ConsultationHistory) == 0

    def test_usuario_sin_prefs_no_guarda(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """La ausencia de un opt-in explícito mantiene el flujo stateless."""
        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = save_to_history_if_consented(
                "no_existe",
                "test",
                "test",
                "test",
            )

        assert result is False


class TestSaveDeliveredConsultation:
    """Verifica el guardado idempotente posterior a una entrega real."""

    def test_outcomes_son_cerrados(self) -> None:
        """El caller solo recibe estados estables y no excepciones internas."""
        assert {outcome.value for outcome in DeliveredHistorySaveOutcome} == {
            "disabled",
            "saved",
            "already_saved",
            "not_eligible",
            "no_consent",
            "database_error",
        }

    def test_gate_apagado_no_abre_sesion(self) -> None:
        """Una entrega no habilita historial sin activación explícita."""
        with (
            patch.object(settings, "consultation_history_enabled", False),
            patch("app.services.consultation_history_service.SessionLocal") as session_factory,
        ):
            outcome = save_delivered_consultation_to_history(1)

        assert outcome is DeliveredHistorySaveOutcome.DISABLED
        session_factory.assert_not_called()

    def test_entrega_con_consentimiento_guarda_vinculo_y_contenido(
        self,
        engine: Engine,
        history_enabled: None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """La fuente entregada produce exactamente una fila scoped."""
        phone_hash = "d" * 64
        query_text = "consulta sensible que no debe loguearse"
        response_text = "respuesta sensible que no debe loguearse"
        _seed_user(engine, phone_hash, consent=True)
        _seed_consultation(
            engine,
            Consultation(
                id=301,
                phone_hash=phone_hash,
                intent="precio",
                producto="papa",
                query_text=query_text,
                response_text=response_text,
                delivery_status="delivered",
            ),
        )
        caplog.set_level(logging.DEBUG)

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            outcome = save_delivered_consultation_to_history(301)

        with Session(engine) as session:
            history = session.scalar(select(ConsultationHistory))

        assert outcome is DeliveredHistorySaveOutcome.SAVED
        assert history is not None
        assert history.source_consultation_id == 301
        assert history.phone_hash == phone_hash
        assert history.query_text == query_text
        assert history.response_text == response_text
        assert history.producto == "papa"
        assert phone_hash not in caplog.text
        assert query_text not in caplog.text
        assert response_text not in caplog.text

    def test_retry_retorna_already_saved_y_mantiene_una_fila(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """La UNIQUE de la fuente vuelve inocuo un reintento secuencial."""
        phone_hash = "e" * 64
        _seed_user(engine, phone_hash, consent=True)
        _seed_consultation(
            engine,
            Consultation(
                id=302,
                phone_hash=phone_hash,
                intent="clima",
                query_text="clima mañana",
                response_text="respuesta clima",
                delivery_status="delivered",
            ),
        )

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            first = save_delivered_consultation_to_history(302)
            retry = save_delivered_consultation_to_history(302)

        assert first is DeliveredHistorySaveOutcome.SAVED
        assert retry is DeliveredHistorySaveOutcome.ALREADY_SAVED
        assert _count_rows(engine, ConsultationHistory) == 1

    @pytest.mark.parametrize("delivery_status", ["pending", "failed"])
    def test_consulta_no_entregada_no_es_elegible(
        self,
        engine: Engine,
        history_enabled: None,
        delivery_status: str,
    ) -> None:
        """Pending y failed nunca alimentan el historial consentido."""
        phone_hash = "f" * 64
        _seed_user(engine, phone_hash, consent=True)
        _seed_consultation(
            engine,
            Consultation(
                id=303,
                phone_hash=phone_hash,
                intent="precio",
                query_text="precio papa",
                response_text="500 pesos",
                delivery_status=delivery_status,
            ),
        )

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            outcome = save_delivered_consultation_to_history(303)

        assert outcome is DeliveredHistorySaveOutcome.NOT_ELIGIBLE
        assert _count_rows(engine, ConsultationHistory) == 0

    def test_consulta_no_encontrada_no_es_elegible(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """Un ID inexistente no crea contenido ni inventa identidad."""
        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            outcome = save_delivered_consultation_to_history(999)

        assert outcome is DeliveredHistorySaveOutcome.NOT_ELIGIBLE
        assert _count_rows(engine, ConsultationHistory) == 0

    @pytest.mark.parametrize(
        "intent",
        ["resumen", "saludo", "feedback", "alerta", "desconocido"],
    )
    def test_entrega_meta_no_reemplaza_contexto_util(
        self,
        engine: Engine,
        history_enabled: None,
        intent: str,
    ) -> None:
        """Solo consultas sustantivas alimentan la memoria consentida."""
        phone_hash = f"{intent[0]}" * 64
        _seed_user(engine, phone_hash, consent=True)
        _seed_consultation(
            engine,
            Consultation(
                phone_hash=phone_hash,
                intent=intent,
                query_text="cuál fue mi última consulta",
                response_text="respuesta contextual",
                delivery_status="delivered",
            ),
        )

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            outcome = save_delivered_consultation_to_history(1)

        assert outcome is DeliveredHistorySaveOutcome.NOT_ELIGIBLE
        assert _count_rows(engine, ConsultationHistory) == 0

    @pytest.mark.parametrize(
        ("query_text", "response_text"),
        [
            ("", "respuesta"),
            ("consulta", ""),
            ("   ", "respuesta"),
            ("\t\n", "respuesta"),
            ("consulta", "\r\n"),
        ],
    )
    def test_contenido_vacio_no_es_elegible(
        self,
        engine: Engine,
        history_enabled: None,
        query_text: str,
        response_text: str,
    ) -> None:
        """Query y respuesta deben aportar contenido real después de trim."""
        phone_hash = "1" * 64
        _seed_user(engine, phone_hash, consent=True)
        _seed_consultation(
            engine,
            Consultation(
                id=304,
                phone_hash=phone_hash,
                intent="precio",
                query_text=query_text,
                response_text=response_text,
                delivery_status="delivered",
            ),
        )

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            outcome = save_delivered_consultation_to_history(304)

        assert outcome is DeliveredHistorySaveOutcome.NOT_ELIGIBLE
        assert _count_rows(engine, ConsultationHistory) == 0

    def test_sin_consentimiento_no_guarda(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """El estado delivered nunca sustituye el opt-in vigente."""
        phone_hash = "2" * 64
        _seed_user(engine, phone_hash, consent=False)
        _seed_consultation(
            engine,
            Consultation(
                id=305,
                phone_hash=phone_hash,
                intent="precio",
                query_text="precio papa",
                response_text="500 pesos",
                delivery_status="delivered",
            ),
        )

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            outcome = save_delivered_consultation_to_history(305)

        assert outcome is DeliveredHistorySaveOutcome.NO_CONSENT
        assert _count_rows(engine, ConsultationHistory) == 0

    def test_carrera_unique_retorna_already_saved(self) -> None:
        """Una inserción concurrente equivalente se interpreta como retry."""
        phone_hash = "3" * 64
        consultation = Consultation(
            id=306,
            phone_hash=phone_hash,
            intent="precio",
            query_text="precio papa",
            response_text="500 pesos",
            delivery_status="delivered",
        )
        prefs = UserPrefs(
            phone_hash=phone_hash,
            comuna="Traiguén",
            history_consent=True,
        )
        session = MagicMock(spec=Session)
        session.scalar.side_effect = [consultation, prefs, None, 77]
        session.commit.side_effect = IntegrityError(
            "INSERT",
            {},
            RuntimeError("unique"),
        )

        with (
            patch.object(settings, "consultation_history_enabled", True),
            patch(
                "app.services.consultation_history_service.SessionLocal",
                return_value=session,
            ),
        ):
            outcome = save_delivered_consultation_to_history(306)

        assert outcome is DeliveredHistorySaveOutcome.ALREADY_SAVED
        session.rollback.assert_called_once()
        session.close.assert_called_once()

    def test_error_db_se_sanea_y_cierra_sesion(self) -> None:
        """Un fallo secundario retorna outcome sin escapar hacia la entrega."""
        session = MagicMock(spec=Session)
        session.scalar.side_effect = SQLAlchemyError("base no disponible")

        with (
            patch.object(settings, "consultation_history_enabled", True),
            patch(
                "app.services.consultation_history_service.SessionLocal",
                return_value=session,
            ),
        ):
            outcome = save_delivered_consultation_to_history(307)

        assert outcome is DeliveredHistorySaveOutcome.DATABASE_ERROR
        session.rollback.assert_called_once()
        session.close.assert_called_once()

    def test_early_return_cierra_sesion(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """La rama no encontrada también libera la conexión."""
        session = Session(engine)

        with (
            patch(
                "app.services.consultation_history_service.SessionLocal",
                return_value=session,
            ),
            patch.object(session, "close", wraps=session.close) as close,
        ):
            outcome = save_delivered_consultation_to_history(999)

        assert outcome is DeliveredHistorySaveOutcome.NOT_ELIGIBLE
        close.assert_called_once()


class TestGetLatestConsultationContext:
    """Verifica el lector contextual acotado y sujeto a consentimiento."""

    def test_gate_apagado_no_abre_sesion(self) -> None:
        """El lector permanece stateless mientras el feature está apagado."""
        with (
            patch.object(settings, "consultation_history_enabled", False),
            patch("app.services.consultation_history_service.SessionLocal") as session_factory,
        ):
            result = get_latest_consultation_context("hash")

        assert result is None
        session_factory.assert_not_called()

    @pytest.mark.parametrize("phone_hash", ["", "   "])
    def test_hash_vacio_no_abre_sesion(
        self,
        history_enabled: None,
        phone_hash: str,
    ) -> None:
        """Una identidad vacía se rechaza antes de consultar SQLite."""
        with patch("app.services.consultation_history_service.SessionLocal") as session_factory:
            result = get_latest_consultation_context(phone_hash)

        assert result is None
        session_factory.assert_not_called()

    def test_sin_consentimiento_no_lee_historial(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """El gate global no reemplaza el opt-in vigente del sujeto."""
        phone_hash = "contexto_sin_consentimiento"
        _seed_user(engine, phone_hash, consent=False, history_entries=1)
        session = Session(engine)

        with (
            patch(
                "app.services.consultation_history_service.SessionLocal",
                return_value=session,
            ),
            patch.object(session, "execute", wraps=session.execute) as execute,
        ):
            result = get_latest_consultation_context(phone_hash)

        assert result is None
        execute.assert_not_called()

    def test_aisla_el_contexto_por_phone_hash(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """Una fila más nueva de otro sujeto nunca contamina el contexto."""
        subject_hash = "contexto_propio"
        other_hash = "contexto_ajeno"
        _seed_user(engine, subject_hash, consent=True)
        _seed_user(engine, other_hash, consent=True)
        with Session(engine) as session:
            session.add_all(
                [
                    ConsultationHistory(
                        phone_hash=subject_hash,
                        query_text="consulta propia",
                        response_text="respuesta propia",
                        intent="precio",
                        created_at=datetime(2026, 7, 30, 10, tzinfo=UTC),
                    ),
                    ConsultationHistory(
                        phone_hash=other_hash,
                        query_text="consulta ajena",
                        response_text="respuesta ajena",
                        intent="clima",
                        created_at=datetime(2026, 7, 30, 12, tzinfo=UTC),
                    ),
                ]
            )
            session.commit()

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = get_latest_consultation_context(subject_hash)

        assert result is not None
        assert result.query_text == "consulta propia"
        assert result.response_text == "respuesta propia"

    def test_orden_estable_desempata_por_id(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """A igual created_at, la fila con mayor ID es la más reciente."""
        phone_hash = "contexto_orden"
        created_at = datetime(2026, 7, 30, 12, tzinfo=UTC)
        _seed_user(engine, phone_hash, consent=True)
        with Session(engine) as session:
            session.add_all(
                [
                    ConsultationHistory(
                        id=801,
                        phone_hash=phone_hash,
                        query_text="consulta anterior",
                        response_text="respuesta anterior",
                        intent="precio",
                        created_at=created_at,
                    ),
                    ConsultationHistory(
                        id=802,
                        phone_hash=phone_hash,
                        query_text="consulta elegida",
                        response_text="respuesta elegida",
                        intent="clima",
                        producto="trigo",
                        created_at=created_at,
                    ),
                ]
            )
            session.commit()

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = get_latest_consultation_context(phone_hash)

        assert result == LatestConsultationContext(
            query_text="consulta elegida",
            response_text="respuesta elegida",
            intent="clima",
            producto="trigo",
        )

    def test_ignora_respuesta_meta_mas_nueva(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """Pedir contexto repetidamente conserva la consulta sustantiva."""
        phone_hash = "contexto_sin_bucle"
        _seed_user(engine, phone_hash, consent=True)
        with Session(engine) as session:
            session.add_all(
                [
                    ConsultationHistory(
                        phone_hash=phone_hash,
                        query_text="precio de la papa",
                        response_text="la papa está a 500 pesos",
                        intent="precio",
                        created_at=datetime(2026, 7, 30, 10, tzinfo=UTC),
                    ),
                    ConsultationHistory(
                        phone_hash=phone_hash,
                        query_text="cuál fue mi última consulta",
                        response_text="tu consulta anterior fue precio de la papa",
                        intent="resumen",
                        created_at=datetime(2026, 7, 30, 12, tzinfo=UTC),
                    ),
                ]
            )
            session.commit()

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = get_latest_consultation_context(phone_hash)

        assert result is not None
        assert result.query_text == "precio de la papa"
        assert result.response_text == "la papa está a 500 pesos"

    def test_recorta_campos_y_no_expone_datos_en_logs(
        self,
        engine: Engine,
        history_enabled: None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Cada campo sale normalizado bajo un máximo fijo y conservador."""
        phone_hash = "hash-contexto-secreto"
        query_text = "q" * (HISTORY_CONTEXT_QUERY_MAX_CHARS + 50)
        response_text = "r" * (HISTORY_CONTEXT_RESPONSE_MAX_CHARS + 50)
        intent = "precio"
        producto = "p" * (HISTORY_CONTEXT_PRODUCT_MAX_CHARS + 50)
        _seed_user(engine, phone_hash, consent=True)
        with Session(engine) as session:
            session.add(
                ConsultationHistory(
                    phone_hash=phone_hash,
                    query_text=f"  {query_text}  ",
                    response_text=f"\n{response_text}\t",
                    intent=f"  {intent}  ",
                    producto=f"  {producto}  ",
                )
            )
            session.commit()
        caplog.set_level(logging.DEBUG)

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = get_latest_consultation_context(phone_hash)

        assert result is not None
        assert result.query_text == query_text[:HISTORY_CONTEXT_QUERY_MAX_CHARS]
        assert result.response_text == response_text[:HISTORY_CONTEXT_RESPONSE_MAX_CHARS]
        assert result.intent == intent
        assert result.producto == producto[:HISTORY_CONTEXT_PRODUCT_MAX_CHARS]
        assert len(result.query_text) == HISTORY_CONTEXT_QUERY_MAX_CHARS
        assert len(result.response_text) == HISTORY_CONTEXT_RESPONSE_MAX_CHARS
        assert len(result.intent) <= HISTORY_CONTEXT_INTENT_MAX_CHARS
        assert result.producto is not None
        assert len(result.producto) == HISTORY_CONTEXT_PRODUCT_MAX_CHARS
        assert phone_hash not in caplog.text
        assert query_text not in caplog.text
        assert response_text not in caplog.text
        with pytest.raises(FrozenInstanceError):
            result.intent = "otro"  # type: ignore[misc]

    def test_error_db_se_degrada_y_cierra_sesion(
        self,
        history_enabled: None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Una falla de lectura retorna None y siempre libera la conexión."""
        phone_hash = "hash-que-no-se-debe-loguear"
        session = MagicMock(spec=Session)
        session.scalar.side_effect = SQLAlchemyError("contenido-secreto")
        caplog.set_level(logging.DEBUG)

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            return_value=session,
        ):
            result = get_latest_consultation_context(phone_hash)

        assert result is None
        session.close.assert_called_once()
        assert phone_hash not in caplog.text
        assert "contenido-secreto" not in caplog.text


class TestDeleteHistory:
    """Verifica borrado bulk, auditoría atómica e idempotencia."""

    def test_borrado_genera_hmac_sin_hash_original(
        self,
        engine: Engine,
        history_enabled: None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """La evidencia usa HMAC dedicado y los logs omiten sujeto y contenido."""
        phone_hash = "hash-que-no-debe-aparecer"
        query_text = "consulta sensible que debe redactarse"
        response_text = "respuesta sensible que debe redactarse"
        _seed_user(engine, phone_hash, consent=True, history_entries=2)
        _seed_consultation(
            engine,
            Consultation(
                id=901,
                phone_hash=phone_hash,
                query_text=query_text,
                response_text=response_text,
                intent="precio",
            ),
        )
        caplog.set_level(logging.DEBUG)

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = delete_history(phone_hash)

        with Session(engine) as session:
            audit = session.scalar(select(ConsultationHistoryDeletionAudit))
            consultation = session.scalar(select(Consultation).where(Consultation.id == 901))

        assert audit is not None
        assert consultation is not None
        assert consultation.query_text == ""
        assert consultation.response_text == ""
        assert result.records_deleted == 2
        assert result.outcome == "completed"
        assert audit.subject_token != phone_hash
        assert audit.subject_token is not None
        assert len(audit.subject_token) == 64
        assert phone_hash not in audit.subject_token
        assert phone_hash not in caplog.text
        assert "consulta-0" not in caplog.text
        assert query_text not in caplog.text
        assert response_text not in caplog.text
        assert _count_rows(engine, ConsultationHistory) == 0

    def test_revocacion_redacta_aunque_no_haya_historial(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """La consulta base se redacta aunque el historial separado esté vacío."""
        phone_hash = "hash_revocacion_sin_historial"
        _seed_user(engine, phone_hash, consent=True)
        _seed_consultation(
            engine,
            Consultation(
                id=902,
                phone_hash=phone_hash,
                query_text="consulta previa a la revocación",
                response_text="respuesta previa a la revocación",
                intent="clima",
            ),
        )

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = delete_history(
                phone_hash,
                reason="consent_revoked",
                requested_via="admin_api",
            )

        with Session(engine) as session:
            consultation = session.scalar(select(Consultation).where(Consultation.id == 902))
            audit = session.scalar(select(ConsultationHistoryDeletionAudit))
            prefs = session.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))

        assert consultation is not None
        assert consultation.query_text == ""
        assert consultation.response_text == ""
        assert result.records_deleted == 0
        assert result.outcome == "no_records"
        assert audit is not None
        assert audit.reason == "consent_revoked"
        assert audit.requested_via == "admin_api"
        assert audit.records_deleted == 0
        assert audit.outcome == "no_records"
        assert prefs is not None
        assert prefs.history_consent is False

    def test_borrado_aisla_al_sujeto(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """No redacta consultas ni elimina historial de otro sujeto."""
        subject_hash = "hash_sujeto_borrado"
        other_hash = "hash_sujeto_ajeno"
        _seed_user(engine, subject_hash, consent=True, history_entries=1)
        _seed_user(engine, other_hash, consent=True, history_entries=1)
        _seed_consultation(
            engine,
            Consultation(
                id=903,
                phone_hash=subject_hash,
                query_text="consulta propia",
                response_text="respuesta propia",
                intent="precio",
            ),
        )
        _seed_consultation(
            engine,
            Consultation(
                id=904,
                phone_hash=other_hash,
                query_text="consulta ajena",
                response_text="respuesta ajena",
                intent="precio",
            ),
        )

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = delete_history(subject_hash)

        with Session(engine) as session:
            subject = session.scalar(select(Consultation).where(Consultation.id == 903))
            other = session.scalar(select(Consultation).where(Consultation.id == 904))
            remaining_history_subjects = set(session.scalars(select(ConsultationHistory.phone_hash)).all())

        assert subject is not None
        assert subject.query_text == ""
        assert subject.response_text == ""
        assert other is not None
        assert other.query_text == "consulta ajena"
        assert other.response_text == "respuesta ajena"
        assert result.records_deleted == 1
        assert remaining_history_subjects == {other_hash}

    def test_borrado_sin_filas_tambien_se_audita(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """Una solicitud válida sin datos deja evidencia con no_records."""
        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = delete_history("sin_historial")

        with Session(engine) as session:
            audit = session.scalar(select(ConsultationHistoryDeletionAudit))

        assert audit is not None
        assert result.records_deleted == 0
        assert result.outcome == "no_records"
        assert audit.records_deleted == 0
        assert audit.outcome == "no_records"

    def test_rowcount_none_se_trata_como_cero(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """Un driver sin rowcount confiable no provoca TypeError."""
        session = Session(engine)
        session.connection()
        connection_proxy = SimpleNamespace(execute=lambda _statement: SimpleNamespace(rowcount=None))

        with (
            patch(
                "app.services.consultation_history_service.SessionLocal",
                return_value=session,
            ),
            patch.object(
                session,
                "connection",
                return_value=connection_proxy,
            ),
        ):
            result = delete_history("sin_historial")

        assert result.records_deleted == 0
        assert result.outcome == "no_records"

    def test_resultado_es_inmutable(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """El caller no puede alterar el resultado confirmado."""
        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = delete_history("sin_historial")

        with pytest.raises(FrozenInstanceError):
            result.records_deleted = 99  # type: ignore[misc]

    def test_fallo_de_auditoria_revierte_delete(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """Nunca se confirma el derecho a borrado sin evidencia durable."""
        _seed_user(engine, "hash_rollback", consent=True, history_entries=2)
        _seed_consultation(
            engine,
            Consultation(
                id=905,
                phone_hash="hash_rollback",
                query_text="consulta que debe sobrevivir",
                response_text="respuesta que debe sobrevivir",
                intent="precio",
            ),
        )
        failing_session = Session(engine)

        with (
            patch(
                "app.services.consultation_history_service.SessionLocal",
                return_value=failing_session,
            ),
            patch.object(
                failing_session,
                "add",
                side_effect=SQLAlchemyError("fallo de auditoría"),
            ),
            pytest.raises(HistoryOperationError),
        ):
            delete_history(
                "hash_rollback",
                reason="consent_revoked",
                requested_via="admin_api",
            )

        assert _count_rows(engine, ConsultationHistory) == 2
        assert _count_rows(engine, ConsultationHistoryDeletionAudit) == 0
        with Session(engine) as session:
            consultation = session.scalar(select(Consultation).where(Consultation.id == 905))
        assert consultation is not None
        assert consultation.query_text == "consulta que debe sobrevivir"
        assert consultation.response_text == "respuesta que debe sobrevivir"
        with Session(engine) as session:
            prefs = session.scalar(select(UserPrefs).where(UserPrefs.phone_hash == "hash_rollback"))
        assert prefs is not None
        assert prefs.history_consent is True

    def test_reintento_con_mismo_event_id_es_idempotente(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """Un retry equivalente retorna la evidencia original sin duplicarla."""
        _seed_user(engine, "hash_retry", consent=True, history_entries=2)
        _seed_consultation(
            engine,
            Consultation(
                id=906,
                phone_hash="hash_retry",
                query_text="consulta original",
                response_text="respuesta original",
                intent="precio",
            ),
        )
        event_id = str(uuid4())

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            first = delete_history("hash_retry", event_id=event_id)
            with Session(engine) as session:
                consultation = session.scalar(select(Consultation).where(Consultation.id == 906))
                assert consultation is not None
                consultation.query_text = "consulta creada después"
                consultation.response_text = "respuesta creada después"
                session.commit()
            retry = delete_history("hash_retry", event_id=event_id)

        assert retry == first
        assert retry.records_deleted == 2
        assert _count_rows(engine, ConsultationHistoryDeletionAudit) == 1
        with Session(engine) as session:
            consultation = session.scalar(select(Consultation).where(Consultation.id == 906))
        assert consultation is not None
        assert consultation.query_text == "consulta creada después"
        assert consultation.response_text == "respuesta creada después"

    @pytest.mark.parametrize(
        ("retry_hash", "reason", "requested_via"),
        [
            ("otro_hash", "user_request", "verified_whatsapp"),
            ("hash_original", "consent_revoked", "verified_whatsapp"),
            ("hash_original", "user_request", "admin_api"),
        ],
    )
    def test_event_id_reutilizado_con_mismatch_es_rechazado(
        self,
        engine: Engine,
        history_enabled: None,
        retry_hash: str,
        reason: str,
        requested_via: str,
    ) -> None:
        """El UUID no puede encubrir otro sujeto, motivo u origen."""
        event_id = str(uuid4())

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            delete_history("hash_original", event_id=event_id)
            with pytest.raises(HistoryOperationError, match="otra solicitud"):
                delete_history(
                    retry_hash,
                    reason=reason,  # type: ignore[arg-type]
                    requested_via=requested_via,  # type: ignore[arg-type]
                    event_id=event_id,
                )

        assert _count_rows(engine, ConsultationHistoryDeletionAudit) == 1

    @pytest.mark.parametrize(
        ("reason", "requested_via"),
        [
            ("user_request", "verified_whatsapp"),
            ("user_request", "admin_api"),
            ("consent_revoked", "verified_whatsapp"),
            ("consent_revoked", "admin_api"),
        ],
    )
    def test_motivos_y_origenes_de_sujeto_permitidos(
        self,
        engine: Engine,
        history_enabled: None,
        reason: str,
        requested_via: str,
    ) -> None:
        """WhatsApp verificado y API admin usan los dominios del esquema."""
        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = delete_history(
                "hash_scope",
                reason=reason,  # type: ignore[arg-type]
                requested_via=requested_via,  # type: ignore[arg-type]
            )

        assert result.outcome == "no_records"

    @pytest.mark.parametrize(
        ("reason", "requested_via"),
        [
            ("ttl", "system_retention"),
            ("user_request", "texto_libre"),
        ],
    )
    def test_motivo_u_origen_fuera_del_scope_no_abre_sesion(
        self,
        history_enabled: None,
        reason: str,
        requested_via: str,
    ) -> None:
        """El servicio de sujeto no acepta la purga agregada ni texto libre."""
        with (
            patch("app.services.consultation_history_service.SessionLocal") as session_factory,
            pytest.raises(HistoryOperationError, match="no está permitida"),
        ):
            delete_history(
                "hash_scope",
                reason=reason,  # type: ignore[arg-type]
                requested_via=requested_via,  # type: ignore[arg-type]
            )

        session_factory.assert_not_called()


class TestPurgeExpiredHistory:
    """Verifica cutoff estricto, atomicidad e idempotencia de la purga TTL."""

    def test_gate_apagado_no_abre_sesion(self) -> None:
        """La purga desactivada es un no-op tipado sin tocar SQLite."""
        now = datetime(2026, 7, 30, 12, tzinfo=UTC)

        with (
            patch.object(settings, "consultation_history_enabled", False),
            patch.object(settings, "consultation_history_ttl_days", 28),
            patch("app.services.consultation_history_service.SessionLocal") as session_factory,
        ):
            result = purge_expired_history(now=now)

        assert result.event_id is None
        assert result.records_deleted == 0
        assert result.cutoff_at == now - timedelta(days=28)
        assert result.outcome == "disabled"
        session_factory.assert_not_called()

    def test_cutoff_estricto_purga_multiples_sujetos_y_audita_agregado(
        self,
        engine: Engine,
        history_enabled: None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Solo `created_at < cutoff` se elimina, sin identificar sujetos."""
        now = datetime(2026, 7, 30, 12, tzinfo=UTC)
        cutoff = now - timedelta(days=28)
        _seed_user(engine, "hash_a", consent=True)
        _seed_user(engine, "hash_b", consent=True)
        _seed_history_at(
            engine,
            "hash_a",
            [cutoff - timedelta(microseconds=1), cutoff],
        )
        _seed_history_at(
            engine,
            "hash_b",
            [cutoff - timedelta(days=1), cutoff + timedelta(seconds=1)],
        )
        caplog.set_level(logging.DEBUG)

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = purge_expired_history(now=now)

        with Session(engine) as session:
            remaining_queries = set(session.scalars(select(ConsultationHistory.query_text)).all())
            audit = session.scalar(select(ConsultationHistoryDeletionAudit))

        assert result.records_deleted == 2
        assert result.outcome == "completed"
        assert remaining_queries == {"hash_a-1", "hash_b-1"}
        assert audit is not None
        assert audit.reason == "ttl"
        assert audit.subject_token is None
        assert audit.requested_via == "system_retention"
        assert audit.cutoff_at is not None
        assert audit.cutoff_at.replace(tzinfo=UTC) == cutoff
        assert "hash_a" not in caplog.text
        assert "hash_b" not in caplog.text

    def test_cero_filas_tambien_genera_auditoria_agregada(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """Una ejecución válida sin vencidos deja outcome no_records."""
        now = datetime(2026, 7, 30, 12, tzinfo=UTC)

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = purge_expired_history(now=now)

        with Session(engine) as session:
            audit = session.scalar(select(ConsultationHistoryDeletionAudit))

        assert result.records_deleted == 0
        assert result.outcome == "no_records"
        assert audit is not None
        assert audit.records_deleted == 0
        assert audit.subject_token is None
        assert audit.cutoff_at is not None

    def test_fallo_de_auditoria_revierte_purga(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """Las filas vencidas sobreviven si no se puede insertar evidencia."""
        now = datetime(2026, 7, 30, 12, tzinfo=UTC)
        cutoff = now - timedelta(days=28)
        _seed_user(engine, "hash_rollback_ttl", consent=True)
        _seed_history_at(
            engine,
            "hash_rollback_ttl",
            [cutoff - timedelta(seconds=1)],
        )
        failing_session = Session(engine)

        with (
            patch(
                "app.services.consultation_history_service.SessionLocal",
                return_value=failing_session,
            ),
            patch.object(
                failing_session,
                "add",
                side_effect=SQLAlchemyError("fallo de auditoría"),
            ),
            pytest.raises(HistoryOperationError),
        ):
            purge_expired_history(now=now)

        assert _count_rows(engine, ConsultationHistory) == 1
        assert _count_rows(engine, ConsultationHistoryDeletionAudit) == 0

    def test_reintento_mismo_event_id_y_cutoff_es_idempotente(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """El retry retorna el primer resultado sin nueva auditoría."""
        now = datetime(2026, 7, 30, 12, tzinfo=UTC)
        cutoff = now - timedelta(days=28)
        event_id = str(uuid4())
        _seed_user(engine, "hash_retry_ttl", consent=True)
        _seed_history_at(
            engine,
            "hash_retry_ttl",
            [cutoff - timedelta(seconds=1)],
        )

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            first = purge_expired_history(now=now, event_id=event_id)
            retry = purge_expired_history(now=now, event_id=event_id)

        assert retry == first
        assert retry.records_deleted == 1
        assert _count_rows(engine, ConsultationHistoryDeletionAudit) == 1

    def test_reintento_mismo_event_id_con_otro_cutoff_es_rechazado(
        self,
        engine: Engine,
        history_enabled: None,
    ) -> None:
        """El UUID no puede reutilizar evidencia de otra ventana temporal."""
        now = datetime(2026, 7, 30, 12, tzinfo=UTC)
        event_id = str(uuid4())

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            purge_expired_history(now=now, event_id=event_id)
            with pytest.raises(HistoryOperationError, match="otra operación"):
                purge_expired_history(
                    now=now + timedelta(seconds=1),
                    event_id=event_id,
                )

        assert _count_rows(engine, ConsultationHistoryDeletionAudit) == 1


class TestPurgeHistoryJob:
    """Verifica el contrato de salida del CLI one-shot."""

    def test_exit_cero_y_propaga_event_id(self) -> None:
        """Un no-op confirmado finaliza correctamente y conserva el UUID."""
        event_id = str(uuid4())
        result = HistoryPurgeResult(
            event_id=event_id,
            records_deleted=0,
            cutoff_at=datetime(2026, 7, 2, 12, tzinfo=UTC),
            outcome="no_records",
        )

        with patch(
            "app.jobs.purge_consultation_history.purge_expired_history",
            return_value=result,
        ) as purge:
            exit_code = purge_job_main(["--event-id", event_id])

        assert exit_code == 0
        purge.assert_called_once_with(event_id=event_id)

    def test_exit_uno_si_operacion_no_se_confirma(self) -> None:
        """Un error tipado produce estado fallido para cron/Dokploy."""
        with patch(
            "app.jobs.purge_consultation_history.purge_expired_history",
            side_effect=HistoryOperationError("fallo"),
        ):
            exit_code = purge_job_main([])

        assert exit_code == 1


class TestPurgeHistoryScheduler:
    """Verifica ejecución diaria, resiliencia y cierre del lifespan."""

    @pytest.mark.asyncio
    async def test_ejecuta_en_thread_y_espera_24_horas(self) -> None:
        """La operación síncrona nunca bloquea directamente el event loop."""
        result = HistoryPurgeResult(
            event_id=str(uuid4()),
            records_deleted=3,
            cutoff_at=datetime(2026, 7, 2, 12, tzinfo=UTC),
            outcome="completed",
        )
        to_thread = AsyncMock(return_value=result)
        sleep = AsyncMock(side_effect=asyncio.CancelledError)

        with (
            patch("app.main.asyncio.to_thread", to_thread),
            patch("app.main.asyncio.sleep", sleep),
            pytest.raises(asyncio.CancelledError),
        ):
            await _consultation_history_scheduler()

        assert to_thread.await_count == 1
        sleep.assert_awaited_once_with(_CONSULTATION_HISTORY_PURGE_INTERVAL_SECONDS)

    @pytest.mark.asyncio
    async def test_continua_despues_de_error_operativo(self) -> None:
        """Una purga fallida no mata las ejecuciones diarias siguientes."""
        result = HistoryPurgeResult(
            event_id=str(uuid4()),
            records_deleted=0,
            cutoff_at=datetime(2026, 7, 2, 12, tzinfo=UTC),
            outcome="no_records",
        )
        to_thread = AsyncMock(side_effect=[HistoryOperationError("fallo"), result])
        sleep = AsyncMock(
            side_effect=[None, asyncio.CancelledError()],
        )

        with (
            patch("app.main.asyncio.to_thread", to_thread),
            patch("app.main.asyncio.sleep", sleep),
            pytest.raises(asyncio.CancelledError),
        ):
            await _consultation_history_scheduler()

        assert to_thread.await_count == 2
        assert sleep.await_count == 2

    def test_lifespan_gate_apagado_no_crea_tarea(self) -> None:
        """El helper usado por lifespan no instancia ni una coroutine TTL."""
        with (
            patch.object(settings, "consultation_history_enabled", False),
            patch("app.main.asyncio.create_task") as create_task,
        ):
            task = _start_consultation_history_scheduler()

        assert task is None
        create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_tarea_habilitada_se_cancela_y_espera_limpio(self) -> None:
        """Shutdown espera el finally de la tarea creada por lifespan."""
        started = asyncio.Event()
        finalized = asyncio.Event()

        async def controlled_scheduler() -> None:
            """Permanece activo hasta que el shutdown lo cancele."""
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finalized.set()

        with (
            patch.object(settings, "consultation_history_enabled", True),
            patch(
                "app.main._consultation_history_scheduler",
                controlled_scheduler,
            ),
        ):
            task = _start_consultation_history_scheduler()
            assert task is not None
            await started.wait()
            await _cancel_background_task(task)

        assert task.cancelled()
        assert finalized.is_set()


class TestStagingCleanupScheduler:
    """Verifica que la minimización de staging sea continua y sin feature gate."""

    @pytest.mark.asyncio
    async def test_ejecuta_en_thread_cada_hora(self) -> None:
        """La limpieza síncrona corre fuera del event loop."""
        to_thread = AsyncMock(return_value=2)
        sleep = AsyncMock(side_effect=asyncio.CancelledError)

        with (
            patch("app.main.asyncio.to_thread", to_thread),
            patch("app.main.asyncio.sleep", sleep),
            pytest.raises(asyncio.CancelledError),
        ):
            await _consultation_staging_cleanup_scheduler()

        assert to_thread.await_count == 1
        sleep.assert_awaited_once_with(_CONSULTATION_STAGING_CLEANUP_INTERVAL_SECONDS)

    @pytest.mark.asyncio
    async def test_tarea_siempre_se_crea_y_cancela(self) -> None:
        """La retención máxima no depende del gate de historial."""
        started = asyncio.Event()
        finalized = asyncio.Event()

        async def controlled_scheduler() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finalized.set()

        with patch(
            "app.main._consultation_staging_cleanup_scheduler",
            controlled_scheduler,
        ):
            task = _start_consultation_staging_cleanup_scheduler()
            await started.wait()
            await _cancel_background_task(task)

        assert task.cancelled()
        assert finalized.is_set()


class TestGetHistory:
    """Conserva el acceso interno siempre scoped por phone_hash."""

    def test_get_history_vacio(self, engine: Engine) -> None:
        """Un sujeto sin datos recibe una lista vacía."""
        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = get_history("no_existe")

        assert result == []

    def test_get_history_con_datos(self, engine: Engine) -> None:
        """La consulta solo retorna filas del sujeto solicitado."""
        _seed_user(engine, "hash_get", consent=True, history_entries=2)
        _seed_user(engine, "otro_hash", consent=True, history_entries=1)

        with patch(
            "app.services.consultation_history_service.SessionLocal",
            lambda: _create_session(engine),
        ):
            result = get_history("hash_get", limit=10)

        assert len(result) == 2
        assert result[0]["query"] == "consulta-1"
        assert result[1]["query"] == "consulta-0"
