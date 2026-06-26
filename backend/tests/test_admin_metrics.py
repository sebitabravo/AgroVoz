"""Tests para los endpoints admin de métricas (T5.1).

Cubre:
- Auth: 401 sin X-Admin-Key, 401 con key inválido, 200 con key válido.
- /dashboard: KPIs con datos y sin datos.
- /daily: rellena huecos con count=0.
- /latency: avg/percentiles sobre muestra.
- /intents: conteos por intent.
- /products: top productos vía match keyword ODEPA.
- /errors: tasa + lista de errores.
- /recent: lista de consultas recientes.
- /stages: desglose de latencia por etapa del pipeline (Whisper/LLM/TTS).
"""

import datetime
from collections.abc import Generator
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.consultation import Consultation
from app.models.odepa_price import OdepaPrice

_ADMIN_HEADERS = {"X-Admin-Key": settings.admin_api_key}


# ── Helpers ────────────────────────────────────────────────────────


def _session_test_db(tmp_path: Path) -> Generator[Session, None, None]:
    """Session sobre la misma DB temporal del fixture client."""
    db_path = tmp_path / "test_agrovoz.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _consulta(
    db: Session,
    intent: str = "precio",
    query_text: str = "¿a qué precio está la papa?",
    response_text: str = "La papa está a 1200 pesos.",
    latency_ms: int = 1500,
    phone_hash: str = "a" * 64,
    created_at: datetime.datetime | None = None,
    whisper_ms: int = 0,
    llm_ms: int = 0,
    tts_ms: int = 0,
) -> Consultation:
    """Inserta una Consultation de prueba."""
    reg = Consultation(
        phone_hash=phone_hash,
        intent=intent,
        query_text=query_text,
        response_text=response_text,
        audio_duration_ms=3000,
        latency_ms=latency_ms,
        whisper_ms=whisper_ms,
        llm_ms=llm_ms,
        tts_ms=tts_ms,
        created_at=created_at or datetime.datetime.now(),
    )
    db.add(reg)
    db.commit()
    return reg


# ── Auth ───────────────────────────────────────────────────────────


class TestMetricasAuth:
    """Gate de auth con X-Admin-Key en endpoints de métricas."""

    async def test_sin_admin_key_retorna_401(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/admin/metrics/dashboard")
        assert resp.status_code == 401

    async def test_admin_key_invalido_retorna_401(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/admin/metrics/dashboard",
            headers={"X-Admin-Key": "invalido"},
        )
        assert resp.status_code == 401

    async def test_admin_key_valido_retorna_200(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/admin/metrics/dashboard", headers=_ADMIN_HEADERS
        )
        assert resp.status_code == 200


# ── /dashboard ─────────────────────────────────────────────────────


class TestDashboard:
    """KPIs principales."""

    async def test_sin_datos_retorna_ceros(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/admin/metrics/dashboard", headers=_ADMIN_HEADERS
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["today"] == 0
        assert data["yesterday"] == 0
        assert data["success_rate"] == 0.0
        assert data["active_farmers_7d"] == 0
        # Sin consultas no hay base de comparación → trend en None (no 0%).
        assert data["today_trend_pct"] is None
        # sparkline siempre tiene 14 puntos (relleno de días); path es str.
        assert isinstance(data["sparkline_line_path"], str)

    async def test_con_datos_hoy_cuenta_consultas(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, intent="precio", phone_hash="h1" + "a" * 62)
            _consulta(db, intent="clima", phone_hash="h2" + "a" * 62)
            _consulta(db, intent="desconocido", phone_hash="h3" + "a" * 62)

        resp = await client.get(
            "/api/v1/admin/metrics/dashboard", headers=_ADMIN_HEADERS
        )
        data = resp.json()
        assert data["today"] == 3
        # 2 de 3 fueron éxito (precio + clima).
        assert data["success_rate"] == pytest.approx(0.667, abs=0.01)
        assert data["error_count_24h"] == 1
        assert data["active_farmers_7d"] == 3
        # sparkline tiene 14 puntos y el path no es vacío (hay datos hoy).
        assert len(data["sparkline"]) == 14
        assert data["sparkline_line_path"]

    async def test_ayer_no_cuenta_para_hoy(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        ayer = datetime.datetime.now() - datetime.timedelta(days=1)
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, intent="precio", created_at=ayer)
        resp = await client.get(
            "/api/v1/admin/metrics/dashboard", headers=_ADMIN_HEADERS
        )
        data = resp.json()
        assert data["today"] == 0
        assert data["yesterday"] == 1
        # today=0 vs yesterday=1 → caída del 100%.
        assert data["today_trend_pct"] == pytest.approx(-100.0)

    async def test_today_trend_pct_calcula_variacion(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        ayer = datetime.datetime.now() - datetime.timedelta(days=1)
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, intent="precio", created_at=ayer)  # 1 ayer
            _consulta(db, intent="precio")  # 2 hoy
            _consulta(db, intent="clima")
        resp = await client.get(
            "/api/v1/admin/metrics/dashboard", headers=_ADMIN_HEADERS
        )
        data = resp.json()
        assert data["today"] == 2
        assert data["yesterday"] == 1
        # (2 - 1) / 1 * 100 = +100%.
        assert data["today_trend_pct"] == pytest.approx(100.0)


# ── /daily ─────────────────────────────────────────────────────────


class TestDaily:
    """Series diarias con relleno de huecos."""

    async def test_rellena_huecos_con_cero(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/admin/metrics/daily?days=7", headers=_ADMIN_HEADERS
        )
        data = resp.json()
        assert len(data) == 7
        # Todas las entradas tienen date ISO y count entero.
        for entry in data:
            assert "date" in entry
            assert "count" in entry
            assert entry["count"] == 0

    async def test_cuenta_consulta_de_hoy(self, client: AsyncClient, tmp_path: Path) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, intent="precio")
        resp = await client.get(
            "/api/v1/admin/metrics/daily?days=3", headers=_ADMIN_HEADERS
        )
        data = resp.json()
        hoy = datetime.date.today().isoformat()
        ultimo = data[-1]
        assert ultimo["date"] == hoy
        assert ultimo["count"] == 1


# ── /latency ───────────────────────────────────────────────────────


class TestLatency:
    """Estadísticos de latencia."""

    async def test_sin_datos_retorna_ceros(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/admin/metrics/latency", headers=_ADMIN_HEADERS
        )
        data = resp.json()
        assert data["count"] == 0
        assert data["avg"] == 0.0

    async def test_avg_sobre_muestra(self, client: AsyncClient, tmp_path: Path) -> None:
        with next(_session_test_db(tmp_path)) as db:
            for ms in (100, 200, 300):
                _consulta(db, latency_ms=ms)
        resp = await client.get(
            "/api/v1/admin/metrics/latency?days=1", headers=_ADMIN_HEADERS
        )
        data = resp.json()
        assert data["count"] == 3
        assert data["avg"] == pytest.approx(200.0)
        # Percentiles no decrecientes.
        assert data["p50"] <= data["p95"] <= data["p99"]


# ── /intents ───────────────────────────────────────────────────────


class TestIntents:
    """Distribución de intents."""

    async def test_conteo_por_intent(self, client: AsyncClient, tmp_path: Path) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, intent="precio")
            _consulta(db, intent="precio")
            _consulta(db, intent="clima")
            _consulta(db, intent="desconocido")
        resp = await client.get(
            "/api/v1/admin/metrics/intents?days=1", headers=_ADMIN_HEADERS
        )
        data = resp.json()
        assert data["precio"] == 2
        assert data["clima"] == 1
        assert data["desconocido"] == 1


# ── /products ──────────────────────────────────────────────────────


class TestProducts:
    """Top productos mencionados (match keyword ODEPA)."""

    async def test_match_producto_papa(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            db.add(OdepaPrice(
                producto="papa", mercado="Lo Valledor",
                precio_kg=1200, unidad="kg", fecha=datetime.date.today(),
            ))
            db.commit()
            _consulta(db, intent="precio", query_text="¿cuánto vale la papa?")
            _consulta(db, intent="precio", query_text="precio papa lo valledor")
        resp = await client.get(
            "/api/v1/admin/metrics/products?days=1", headers=_ADMIN_HEADERS
        )
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["name"] == "papa"
        assert data[0]["queries"] == 2
        assert data[0]["pct"] == 100.0

    async def test_substring_no_cuenta_papa_en_papaya(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        # Regresión (review PR #70): el match era por substring, así una
        # consulta sobre "papaya" contaba falsamente como "papa" (papa está
        # antes en orden A-Z y el break se queda con el primero). Ahora usa
        # word-boundary: cada producto cuenta solo cuando aparece como palabra.
        with next(_session_test_db(tmp_path)) as db:
            db.add(OdepaPrice(
                producto="papa", mercado="X", precio_kg=1,
                unidad="kg", fecha=datetime.date.today(),
            ))
            db.add(OdepaPrice(
                producto="papaya", mercado="X", precio_kg=1,
                unidad="kg", fecha=datetime.date.today(),
            ))
            db.commit()
            _consulta(db, intent="precio", query_text="¿cuánto cuesta la papaya?")
        resp = await client.get(
            "/api/v1/admin/metrics/products?days=1", headers=_ADMIN_HEADERS
        )
        nombres = {p["name"]: p["queries"] for p in resp.json()}
        assert nombres.get("papaya") == 1
        assert "papa" not in nombres

    async def test_sin_productos_retorna_lista_vacia(
        self, client: AsyncClient
    ) -> None:
        resp = await client.get(
            "/api/v1/admin/metrics/products", headers=_ADMIN_HEADERS
        )
        assert resp.json() == []


# ── /errors ────────────────────────────────────────────────────────


class TestErrors:
    """Tasa de error (intent desconocido)."""

    async def test_tasa_y_lista_errores(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, intent="precio")
            _consulta(
                db,
                intent="desconocido",
                query_text="bla bla incomprensible",
            )
        resp = await client.get(
            "/api/v1/admin/metrics/errors?days=1", headers=_ADMIN_HEADERS
        )
        data = resp.json()
        assert data["total_queries"] == 2
        assert data["total_errors"] == 1
        assert data["rate"] == pytest.approx(0.5)
        assert len(data["recent"]) == 1
        assert data["recent"][0]["query"] == "bla bla incomprensible"


# ── /recent ────────────────────────────────────────────────────────


class TestRecent:
    """Consultas recientes."""

    async def test_lista_recientes_ordenadas(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, query_text="primera consulta")
            _consulta(db, query_text="segunda consulta")
        resp = await client.get(
            "/api/v1/admin/metrics/recent?hours=24", headers=_ADMIN_HEADERS
        )
        data = resp.json()
        assert len(data) == 2
        # Cada entrada tiene los campos esperados.
        for entry in data:
            assert {"text", "intent", "latency_s", "ok", "status_text", "ago", "ts"} <= set(entry)

    async def test_limit_acota_resultado(self, client: AsyncClient, tmp_path: Path) -> None:
        with next(_session_test_db(tmp_path)) as db:
            for i in range(5):
                _consulta(db, query_text=f"consulta {i}")
        resp = await client.get(
            "/api/v1/admin/metrics/recent?hours=24&limit=2", headers=_ADMIN_HEADERS
        )
        assert len(resp.json()) == 2


# ── /stages ─────────────────────────────────────────────────────────


class TestStages:
    """Desglose de latencia por etapa del pipeline."""

    async def test_sin_datos_retorna_ceros(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/admin/metrics/stages", headers=_ADMIN_HEADERS
        )
        data = resp.json()
        assert data["whisper_ms"] == 0.0
        assert data["llm_ms"] == 0.0
        assert data["tts_ms"] == 0.0
        assert data["total_ms"] == 0.0
        assert data["count"] == 0

    async def test_promedio_sobre_muestra(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, whisper_ms=800, llm_ms=1200, tts_ms=600)
            _consulta(db, whisper_ms=1000, llm_ms=1400, tts_ms=800)
        resp = await client.get(
            "/api/v1/admin/metrics/stages?days=1", headers=_ADMIN_HEADERS
        )
        data = resp.json()
        assert data["count"] == 2
        assert data["whisper_ms"] == pytest.approx(900.0)
        assert data["llm_ms"] == pytest.approx(1300.0)
        assert data["tts_ms"] == pytest.approx(700.0)
        assert data["total_ms"] == pytest.approx(2900.0)

    async def test_filtra_registros_sin_timing(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """Consultas con stage en 0 (pre-migración) no diluyen el promedio."""
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, whisper_ms=0, llm_ms=0, tts_ms=0)  # pre-migración
            _consulta(db, whisper_ms=500, llm_ms=700, tts_ms=300)  # con timing
        resp = await client.get(
            "/api/v1/admin/metrics/stages?days=1", headers=_ADMIN_HEADERS
        )
        data = resp.json()
        assert data["count"] == 1  # solo la que tiene timing
        assert data["whisper_ms"] == pytest.approx(500.0)
        assert data["total_ms"] == pytest.approx(1500.0)
