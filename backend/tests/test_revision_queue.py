"""Tests para la cola de revisión humana (issue #99).

Verifica:
- Marcado automático de consultas con fallback
- Consultas normales NO se marcan
- Intent desconocido → requires_review=True
- Vista admin muestra pendientes
- Filtro por resueltas
- Marcar como resuelto (toggle)
- Privacidad: phone_hash truncado en vista
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.admin.auth import COOKIE_NAME, create_session_cookie
from app.core.config import settings
from app.core.database import Base, get_db
from app.models.consultation import Consultation
from app.services.llm_service import FALLBACK_TEXT, NO_RESPONSE_TEXT
from app.services.pipeline_service import AgroVozPipeline

# Key de admin para tests (dev-admin-key por defecto).
_ADMIN_KEY = settings.admin_api_key


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture()
def review_db(tmp_path) -> Generator[Session, None, None]:
    """Engine SQLite temporal con tablas incluyendo campos de revisión."""
    from app.models import Consultation, OdepaPrice  # noqa: F401

    db_path = tmp_path / "test_review.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest_asyncio.fixture()
async def review_client(review_db: Session) -> AsyncGenerator[AsyncClient, None]:
    """Cliente HTTP con DB compartida para tests de revisión.

    Usa la misma DB que review_db para que los datos insertados
    sean visibles por los endpoints admin.
    """
    from app.main import app

    original_override = app.dependency_overrides.get(get_db)

    def override_get_db() -> Generator[Session, None, None]:
        yield review_db

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        # Set cookie de autenticación.
        c.cookies.set(COOKIE_NAME, create_session_cookie())
        try:
            yield c
        finally:
            app.dependency_overrides.pop(get_db, None)
            if original_override is not None:
                app.dependency_overrides[get_db] = original_override


def _create_consultation(
    db: Session,
    *,
    intent: str = "precio",
    response_text: str = "Papa está a $1.200 el kilo.",
    requires_review: bool = False,
    resuelto: bool = False,
    revisado_por: str | None = None,
    nota_revision: str | None = None,
    phone_hash: str = "a" * 64,
) -> Consultation:
    """Crea una consulta de prueba en la DB."""
    c = Consultation(
        phone_hash=phone_hash,
        intent=intent,
        query_text="¿A cuánto está la papa?",
        response_text=response_text,
        audio_duration_ms=5000,
        latency_ms=3000,
        whisper_ms=1000,
        llm_ms=1500,
        tts_ms=500,
        requires_review=requires_review,
        resuelto=resuelto,
        revisado_por=revisado_por,
        nota_revision=nota_revision,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


# ── Tests de marcado automático ────────────────────────────────────


class TestShouldMarkForReview:
    """Tests para AgroVozPipeline._should_mark_for_review."""

    def test_fallback_text_requires_review(self) -> None:
        """Respuesta FALLBACK_TEXT → requiere revisión."""
        result = AgroVozPipeline._should_mark_for_review(
            response_text=FALLBACK_TEXT,
            intent="desconocido",
            whisper_ms=1000,
            llm_ms=1500,
            transcribed_text="algo",
        )
        assert result is True

    def test_no_response_text_requires_review(self) -> None:
        """Respuesta NO_RESPONSE_TEXT → requiere revisión."""
        result = AgroVozPipeline._should_mark_for_review(
            response_text=NO_RESPONSE_TEXT,
            intent="desconocido",
            whisper_ms=1000,
            llm_ms=1500,
            transcribed_text="algo",
        )
        assert result is True

    def test_normal_response_no_review(self) -> None:
        """Respuesta normal con intent válido → NO requiere revisión."""
        result = AgroVozPipeline._should_mark_for_review(
            response_text="Papa está a $1.200 el kilo en Lo Valledor.",
            intent="precio",
            whisper_ms=1000,
            llm_ms=1500,
            transcribed_text="¿A cuánto está la papa?",
        )
        assert result is False

    def test_unknown_intent_requires_review(self) -> None:
        """Intent desconocido → requiere revisión."""
        result = AgroVozPipeline._should_mark_for_review(
            response_text="No entendí bien.",
            intent="desconocido",
            whisper_ms=1000,
            llm_ms=1500,
            transcribed_text="hola",
        )
        assert result is True

    def test_whisper_failed_requires_review(self) -> None:
        """Whisper falló (whisper_ms=0, texto vacío) → requiere revisión."""
        result = AgroVozPipeline._should_mark_for_review(
            response_text="",
            intent="desconocido",
            whisper_ms=0,
            llm_ms=0,
            transcribed_text="",
        )
        assert result is True

    def test_llm_failed_with_text_requires_review(self) -> None:
        """LLM falló (llm_ms=0) con texto transcrito → requiere revisión."""
        result = AgroVozPipeline._should_mark_for_review(
            response_text="",
            intent="desconocido",
            whisper_ms=1000,
            llm_ms=0,
            transcribed_text="hola mundo",
        )
        assert result is True

    def test_clima_response_no_review(self) -> None:
        """Respuesta de clima válida → NO requiere revisión."""
        result = AgroVozPipeline._should_mark_for_review(
            response_text="En Traiguén ahora: 18°C, nublado.",
            intent="clima",
            whisper_ms=800,
            llm_ms=2000,
            transcribed_text="¿Cómo está el clima?",
        )
        assert result is False


# ── Tests de integración del pipeline ──────────────────────────────


class TestPipelineReviewFlag:
    """Verifica que el pipeline guarda requires_review en la DB."""

    def test_save_consultation_with_review_flag(
        self, review_db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_save_consultation persiste requires_review=True."""
        import app.core.database as db_mod

        original = db_mod.SessionLocal
        db_mod.SessionLocal = lambda: review_db  # type: ignore[assignment]
        try:
            start = time.monotonic()
            AgroVozPipeline._save_consultation(
                phone_hash="b" * 64,
                intent="desconocido",
                query_text="test",
                response_text=FALLBACK_TEXT,
                audio_duration_ms=1000,
                start_time=start,
                whisper_ms=500,
                llm_ms=500,
                tts_ms=0,
                requires_review=True,
            )

            c = review_db.query(Consultation).filter_by(phone_hash="b" * 64).first()
            assert c is not None
            assert c.requires_review is True
            assert c.resuelto is False
        finally:
            db_mod.SessionLocal = original  # type: ignore[assignment]

    def test_save_consultation_without_review_flag(
        self, review_db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_save_consultation persiste requires_review=False por defecto."""
        import app.core.database as db_mod

        original = db_mod.SessionLocal
        db_mod.SessionLocal = lambda: review_db  # type: ignore[assignment]
        try:
            start = time.monotonic()
            AgroVozPipeline._save_consultation(
                phone_hash="c" * 64,
                intent="precio",
                query_text="papa",
                response_text="Papa a $1200",
                audio_duration_ms=1000,
                start_time=start,
                whisper_ms=500,
                llm_ms=500,
                tts_ms=500,
                requires_review=False,
            )

            c = review_db.query(Consultation).filter_by(phone_hash="c" * 64).first()
            assert c is not None
            assert c.requires_review is False
        finally:
            db_mod.SessionLocal = original  # type: ignore[assignment]


# ── Tests de vista admin ───────────────────────────────────────────


class TestRevisionView:
    """Tests para la vista /admin/revision."""

    @pytest.mark.asyncio
    async def test_revision_page_shows_pending(
        self, review_client: AsyncClient, review_db: Session
    ) -> None:
        """Vista de revisión muestra consultas pendientes."""
        _create_consultation(review_db, requires_review=True, resuelto=False)
        _create_consultation(review_db, intent="precio", requires_review=False)

        resp = await review_client.get("/admin/revision?status=pending")
        assert resp.status_code == 200
        body = resp.text
        assert "pendiente" in body.lower() or "⏳" in body

    @pytest.mark.asyncio
    async def test_revision_page_filter_resolved(
        self, review_client: AsyncClient, review_db: Session
    ) -> None:
        """Filtro 'resolved' muestra solo consultas resueltas."""
        _create_consultation(
            review_db, requires_review=True, resuelto=True, revisado_por="admin"
        )
        _create_consultation(review_db, requires_review=True, resuelto=False)

        resp = await review_client.get("/admin/revision?status=resolved")
        assert resp.status_code == 200
        body = resp.text
        assert "resuelta" in body.lower() or "✓" in body

    @pytest.mark.asyncio
    async def test_revision_page_filter_all(
        self, review_client: AsyncClient, review_db: Session
    ) -> None:
        """Filtro 'all' muestra todas las consultas marcadas."""
        _create_consultation(review_db, requires_review=True, resuelto=True)
        _create_consultation(review_db, requires_review=True, resuelto=False)

        resp = await review_client.get("/admin/revision?status=all")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_revision_excludes_unmarked(
        self, review_client: AsyncClient, review_db: Session
    ) -> None:
        """Consultas sin requires_review NO aparecen en la cola."""
        _create_consultation(review_db, requires_review=False)

        resp = await review_client.get("/admin/revision?status=all")
        assert resp.status_code == 200
        body = resp.text
        assert "Sin consultas" in body


# ── Tests de resolver ──────────────────────────────────────────────


class TestResolveConsultation:
    """Tests para POST /admin/consultations/{id}/resolve."""

    @pytest.mark.asyncio
    async def test_resolve_toggles_to_resolved(
        self, review_client: AsyncClient, review_db: Session
    ) -> None:
        """POST resolve marca la consulta como resuelta."""
        c = _create_consultation(review_db, requires_review=True, resuelto=False)

        resp = await review_client.post(
            f"/admin/consultations/{c.id}/resolve",
            data={"nota": "fallback por producto no mapeado", "revisado_por": "sebastian"},
        )
        assert resp.status_code == 200

        # Verificar en DB.
        review_db.refresh(c)
        assert c.resuelto is True
        assert c.revisado_por == "sebastian"
        assert c.nota_revision == "fallback por producto no mapeado"

    @pytest.mark.asyncio
    async def test_resolve_toggles_back_to_pending(
        self, review_client: AsyncClient, review_db: Session
    ) -> None:
        """POST resolve en consulta ya resuelta la reabre (toggle)."""
        c = _create_consultation(
            review_db, requires_review=True, resuelto=True, revisado_por="admin"
        )

        resp = await review_client.post(
            f"/admin/consultations/{c.id}/resolve", data={}
        )
        assert resp.status_code == 200

        review_db.refresh(c)
        assert c.resuelto is False

    @pytest.mark.asyncio
    async def test_resolve_nonexistent_returns_404(
        self, review_client: AsyncClient
    ) -> None:
        """POST resolve con ID inexistente retorna 404."""
        resp = await review_client.post(
            "/admin/consultations/99999/resolve", data={}
        )
        assert resp.status_code == 404


# ── Tests de privacidad ────────────────────────────────────────────


class TestPrivacy:
    """Verifica que datos sensibles se truncan en la vista."""

    @pytest.mark.asyncio
    async def test_phone_hash_truncated_in_view(
        self, review_client: AsyncClient, review_db: Session
    ) -> None:
        """El phone_hash se muestra truncado (8 chars + '...') en la vista."""
        _create_consultation(review_db, requires_review=True)

        resp = await review_client.get("/admin/revision?status=pending")
        assert resp.status_code == 200
        body = resp.text
        # El hash completo (64 'a') NO debe aparecer.
        assert "a" * 64 not in body
        # La versión truncada (8 chars + ...) debe aparecer.
        assert "aaaaaaaa..." in body

    @pytest.mark.asyncio
    async def test_query_text_truncated_in_view(
        self, review_client: AsyncClient, review_db: Session
    ) -> None:
        """query_text se trunca a 80 caracteres en la tabla."""
        long_text = "x" * 200
        c = Consultation(
            phone_hash="d" * 64,
            intent="desconocido",
            query_text=long_text,
            response_text="ok",
            audio_duration_ms=1000,
            latency_ms=1000,
            whisper_ms=500,
            llm_ms=500,
            tts_ms=0,
            requires_review=True,
        )
        review_db.add(c)
        review_db.commit()

        resp = await review_client.get("/admin/revision?status=pending")
        assert resp.status_code == 200
        # El title attribute tiene el texto completo, pero la celda visible
        # tiene el truncado. Verificamos que el truncado aparece.
        assert "xxx..." in resp.text
