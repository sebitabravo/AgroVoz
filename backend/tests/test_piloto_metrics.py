"""Tests de métricas de piloto y utilidad percibida (Issue #97).

Cubre:
- Feedback del agricultor: detección de keywords, asociación a consulta anterior.
- Feedback no se procesa como consulta normal (no genera nueva consulta).
- Métricas del piloto: productores activos, % útiles, latencia promedio.
- Marcado manual de decisión productiva (toggle).
- Export CSV del piloto.
- Privacidad: solo phone_hash, sin PII.
"""


import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.admin.auth import COOKIE_NAME, create_session_cookie
from app.models.consultation import Consultation


def _pilot_window() -> tuple[datetime.datetime, datetime.datetime]:
    """Ventana determinista alrededor de las filas creadas por cada test."""
    now = datetime.datetime.now(datetime.UTC)
    return now - datetime.timedelta(days=1), now + datetime.timedelta(days=1)


def _autenticar(client: AsyncClient) -> None:
    """Setea cookie de sesión admin válida en el client."""
    client.cookies.set(COOKIE_NAME, create_session_cookie())


def _crear_consultation(
    db: Session,
    phone_hash: str = "abc123",
    intent: str = "precio",
    feedback: str | None = None,
    decision_productiva: bool = False,
    latency_ms: int = 5000,
    created_at: datetime.datetime | None = None,
) -> Consultation:
    """Helper para crear una consulta de prueba."""
    c = Consultation(
        phone_hash=phone_hash,
        intent=intent,
        query_text="test query",
        response_text="test response",
        audio_duration_ms=3000,
        latency_ms=latency_ms,
        whisper_ms=1000,
        llm_ms=2000,
        tts_ms=2000,
        feedback=feedback,
        decision_productiva=decision_productiva,
        created_at=created_at,
    )
    db.add(c)
    db.flush()  # Asegura que el ID esté disponible.
    return c


# ── Detección de feedback ──────────────────────────────────────────


class TestFeedbackDetection:
    """Tests de detección de keywords de feedback."""

    def test_feedback_util_keywords(self) -> None:
        from app.services.pipeline_service import _detect_feedback

        assert _detect_feedback("me sirvió") == "util"
        assert _detect_feedback("me sirve") == "util"
        assert _detect_feedback("me sirvio") == "util"
        assert _detect_feedback("útil") == "util"
        assert _detect_feedback("gracias") == "util"
        assert _detect_feedback("eso era") == "util"
        assert _detect_feedback("perfecto") == "util"

    def test_feedback_no_util_keywords(self) -> None:
        from app.services.pipeline_service import _detect_feedback

        assert _detect_feedback("no me sirvió") == "no_util"
        assert _detect_feedback("no me sirve") == "no_util"
        assert _detect_feedback("no me sirvio") == "no_util"
        assert _detect_feedback("no entendí") == "no_util"
        assert _detect_feedback("no entendi") == "no_util"
        assert _detect_feedback("malo") == "no_util"

    def test_feedback_none_si_no_es_feedback(self) -> None:
        from app.services.pipeline_service import _detect_feedback

        assert _detect_feedback("¿cuánto está la papa?") is None
        assert _detect_feedback("¿cómo está el clima?") is None
        assert _detect_feedback("") is None

    def test_feedback_negativo_prioritario(self) -> None:
        """Si contiene keywords de ambos, prioriza negativo."""
        from app.services.pipeline_service import _detect_feedback

        # "no me sirvió, gracias" → feedback negativo.
        assert _detect_feedback("no me sirvió, gracias") == "no_util"


# ── Asociación de feedback a consulta anterior ─────────────────────


class TestFeedbackAssociation:
    """Tests de asociación de feedback a la consulta anterior."""

    def test_feedback_se_asocia_a_consulta_anterior(self, db: Session) -> None:
        """El feedback actualiza el campo feedback de la última consulta."""
        from app.services.pipeline_service import AgroVozPipeline

        # Crear dos consultas previas.
        c1 = _crear_consultation(db, phone_hash="user1", intent="precio")
        c2 = _crear_consultation(db, phone_hash="user1", intent="clima")
        db.commit()

        # Simular feedback "util" — SessionLocal() crea sesión sobre misma DB.
        pipeline = AgroVozPipeline()
        updated = pipeline._update_previous_feedback("user1", "util")

        assert updated is True
        # Verificar: expire objetos y re-leer.
        db.expire_all()
        assert db.get(Consultation, c2.id).feedback == "util"
        assert db.get(Consultation, c1.id).feedback is None

    def test_feedback_no_util_se_asocia_correctamente(self, db: Session) -> None:
        from app.services.pipeline_service import AgroVozPipeline

        _crear_consultation(db, phone_hash="user2", intent="precio")
        c2 = _crear_consultation(db, phone_hash="user2", intent="clima")
        db.commit()

        pipeline = AgroVozPipeline()
        updated = pipeline._update_previous_feedback("user2", "no_util")

        assert updated is True
        db.expire_all()
        assert db.get(Consultation, c2.id).feedback == "no_util"

    def test_feedback_sin_consulta_previa_retorna_false(self, db: Session) -> None:
        from app.services.pipeline_service import AgroVozPipeline

        pipeline = AgroVozPipeline()
        updated = pipeline._update_previous_feedback("nuevo_user", "util")

        assert updated is False

    def test_feedback_no_afecta_otro_phone_hash(self, db: Session) -> None:
        """El feedback solo actualiza consultas del mismo phone_hash."""
        from app.services.pipeline_service import AgroVozPipeline

        c1 = _crear_consultation(db, phone_hash="user_a", intent="precio")
        _crear_consultation(db, phone_hash="user_b", intent="clima")
        db.commit()

        pipeline = AgroVozPipeline()
        pipeline._update_previous_feedback("user_b", "util")

        db.expire_all()
        assert db.get(Consultation, c1.id).feedback is None


# ── Métricas del piloto ────────────────────────────────────────────


class TestPilotoMetrics:
    """Tests de las métricas del piloto."""

    def test_productores_activos_3_plus_consultas(self, db: Session) -> None:
        """Solo cuenta phone_hash con 3+ consultas."""
        from app.services.metrics_service import get_piloto_metrics

        # user1 tiene 3 consultas (activo).
        for _ in range(3):
            _crear_consultation(db, phone_hash="user1")
        # user2 tiene 2 consultas (no activo).
        for _ in range(2):
            _crear_consultation(db, phone_hash="user2")

        metrics = get_piloto_metrics(db, *_pilot_window())
        assert metrics.productores_activos == 1

    def test_consultas_por_productor_promedio(self, db: Session) -> None:
        from app.services.metrics_service import get_piloto_metrics

        # user1: 4 consultas, user2: 2 consultas → promedio 3.0.
        for _ in range(4):
            _crear_consultation(db, phone_hash="user1")
        for _ in range(2):
            _crear_consultation(db, phone_hash="user2")

        metrics = get_piloto_metrics(db, *_pilot_window())
        assert metrics.consultas_por_productor == 3.0

    def test_pct_utiles(self, db: Session) -> None:
        from app.services.metrics_service import get_piloto_metrics

        # 3 con feedback util, 1 con feedback no_util → 75% útiles.
        _crear_consultation(db, feedback="util")
        _crear_consultation(db, feedback="util")
        _crear_consultation(db, feedback="util")
        _crear_consultation(db, feedback="no_util")

        metrics = get_piloto_metrics(db, *_pilot_window())
        assert metrics.pct_utiles == 75.0

    def test_pct_utiles_sin_feedback(self, db: Session) -> None:
        from app.services.metrics_service import get_piloto_metrics

        _crear_consultation(db, feedback=None)

        metrics = get_piloto_metrics(db, *_pilot_window())
        assert metrics.pct_utiles == 0.0

    def test_latencia_promedio(self, db: Session) -> None:
        from app.services.metrics_service import get_piloto_metrics

        _crear_consultation(db, latency_ms=10000)
        _crear_consultation(db, latency_ms=20000)

        metrics = get_piloto_metrics(db, *_pilot_window())
        assert metrics.latencia_promedio_ms == 15000.0

    def test_decisiones_productivas(self, db: Session) -> None:
        from app.services.metrics_service import get_piloto_metrics

        _crear_consultation(db, decision_productiva=True)
        _crear_consultation(db, decision_productiva=True)
        _crear_consultation(db, decision_productiva=False)

        metrics = get_piloto_metrics(db, *_pilot_window())
        assert metrics.decisiones_productivas == 2

    def test_ventana_explicita_excluye_consultas_fuera_de_rango(self, db: Session) -> None:
        """Todas las métricas ignoran filas anteriores y posteriores al piloto."""
        from app.services.metrics_service import get_piloto_metrics

        start = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC)
        end = datetime.datetime(2026, 8, 8, tzinfo=datetime.UTC)
        inside = datetime.datetime(2026, 8, 4, tzinfo=datetime.UTC)
        before = datetime.datetime(2026, 7, 31, tzinfo=datetime.UTC)
        after = datetime.datetime(2026, 8, 9, tzinfo=datetime.UTC)

        for _ in range(3):
            _crear_consultation(
                db,
                phone_hash="inside",
                feedback="util",
                decision_productiva=True,
                latency_ms=1000,
                created_at=inside,
            )
        for timestamp in (before, after):
            for _ in range(3):
                _crear_consultation(
                    db,
                    phone_hash="outside",
                    feedback="no_util",
                    decision_productiva=True,
                    latency_ms=99_000,
                    created_at=timestamp,
                )

        metrics = get_piloto_metrics(db, start, end)

        assert metrics.productores_activos == 1
        assert metrics.consultas_por_productor == 3.0
        assert metrics.pct_utiles == 100.0
        assert metrics.latencia_promedio_ms == 1000.0
        assert metrics.decisiones_productivas == 3
        assert metrics.total_consultas == 3

    def test_sin_ventana_es_fail_closed(self, db: Session) -> None:
        """No entregar ventana nunca mezcla el histórico en el cierre piloto."""
        from app.services.metrics_service import get_piloto_metrics

        _crear_consultation(db)

        metrics = get_piloto_metrics(db)

        assert metrics.total_consultas == 0
        assert metrics.productores_activos == 0
        assert metrics.latencia_promedio_ms == 0.0

    def test_ventana_incompleta_o_naive_rechazada(self, db: Session) -> None:
        """La API no acepta una ventana ambigua o sin zona horaria."""
        from app.services.metrics_service import get_piloto_metrics

        with pytest.raises(ValueError, match="requiere inicio"):
            get_piloto_metrics(db, datetime.datetime.now(datetime.UTC), None)
        with pytest.raises(ValueError, match="zona horaria"):
            get_piloto_metrics(db, datetime.datetime(2026, 8, 1), datetime.datetime(2026, 8, 2))


# ── Toggle decisión productiva ─────────────────────────────────────


class TestToggleDecision:
    """Tests del toggle de decisión productiva."""

    def test_toggle_activa_decision(self, db: Session) -> None:
        from app.services.metrics_service import toggle_decision_productiva

        c = _crear_consultation(db, decision_productiva=False)
        result = toggle_decision_productiva(db, c.id)

        assert result is True
        db.refresh(c)
        assert c.decision_productiva is True

    def test_toggle_desactiva_decision(self, db: Session) -> None:
        from app.services.metrics_service import toggle_decision_productiva

        c = _crear_consultation(db, decision_productiva=True)
        result = toggle_decision_productiva(db, c.id)

        assert result is False
        db.refresh(c)
        assert c.decision_productiva is False

    def test_toggle_consulta_inexistente_retorna_none(self, db: Session) -> None:
        from app.services.metrics_service import toggle_decision_productiva

        result = toggle_decision_productiva(db, 99999)
        assert result is None


# ── Export CSV ──────────────────────────────────────────────────────


class TestPilotoExport:
    """Tests del export CSV del piloto."""

    def test_export_contiene_metricas(self, db: Session) -> None:
        from app.services.metrics_service import get_piloto_export_data

        _crear_consultation(db, feedback="util")
        _crear_consultation(db, feedback="no_util")

        data = get_piloto_export_data(db, *_pilot_window())
        assert len(data) == 10
        metricas = [d["metrica"] for d in data]
        assert "Productores activos (3+ consultas)" in metricas
        assert "% respuestas útiles" in metricas
        assert "Latencia promedio (ms)" in metricas
        assert "Casos de decisión productiva" in metricas


# ── Endpoints admin ────────────────────────────────────────────────


class TestPilotoEndpoints:
    """Tests de los endpoints del admin para piloto."""

    async def test_piloto_page_renderiza(self, client: AsyncClient) -> None:
        _autenticar(client)
        resp = await client.get("/admin/piloto", follow_redirects=False)
        assert resp.status_code == 200
        assert "Piloto" in resp.text
        assert "Productores activos" in resp.text

    async def test_piloto_sin_cookie_redirige(self, client: AsyncClient) -> None:
        resp = await client.get("/admin/piloto", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/login"

    async def test_toggle_decision_endpoint(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test del endpoint toggle_decision con monkeypatch."""
        from app.services import metrics_service

        # Monkeypatch toggle_decision_productiva para simular éxito.
        monkeypatch.setattr(
            metrics_service, "toggle_decision_productiva", lambda db, id: True
        )

        _autenticar(client)
        resp = await client.post(
            "/admin/consultations/1/decision",
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert "productiva" in resp.text

    async def test_toggle_decision_consulta_inexistente(
        self, client: AsyncClient
    ) -> None:
        _autenticar(client)
        resp = await client.post(
            "/admin/consultations/99999/decision",
            follow_redirects=False,
        )
        assert resp.status_code == 404

    async def test_piloto_export_csv(self, client: AsyncClient) -> None:
        _autenticar(client)
        resp = await client.get("/admin/piloto/export", follow_redirects=False)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/csv; charset=utf-8"
        content_disp = resp.headers.get("content-disposition", "").lower()
        assert "adjunto" in content_disp or "attachment" in content_disp
        # El CSV debe contener las métricas.
        body = resp.text
        assert "Productores activos" in body
        assert "% respuestas útiles" in body


# ── Privacidad ─────────────────────────────────────────────────────


class TestPrivacidad:
    """Tests de que no se expone PII."""

    def test_solo_phone_hash_en_metricas(self, db: Session) -> None:
        """Las métricas solo usan phone_hash, no datos personales."""
        from app.services.metrics_service import get_piloto_metrics

        _crear_consultation(db, phone_hash="hash_anonimo_123")
        metrics = get_piloto_metrics(db, *_pilot_window())
        # Las métricas son números, no contienen PII.
        assert isinstance(metrics.productores_activos, int)
        assert isinstance(metrics.pct_utiles, float)

    def test_export_no_contiene_pii(self, db: Session) -> None:
        """El export CSV no contiene datos personales."""
        from app.services.metrics_service import get_piloto_export_data

        _crear_consultation(db, phone_hash="hash_anonimo_456")
        data = get_piloto_export_data(db, *_pilot_window())
        for row in data:
            assert "phone" not in str(row["valor"]).lower()
            assert "@" not in str(row["valor"])
