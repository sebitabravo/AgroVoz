"""Tests para los endpoints admin de métricas (T5.1).

Cubre:
- Auth: 401 sin X-Admin-Key, 401 con key inválido, 200 con key válido.
- /dashboard: KPIs con datos y sin datos.
- /daily: rellena huecos con count=0.
- /latency: avg/percentiles sobre muestra.
- /intents: conteos por intent.
- /products: top productos desde el campo estructurado de la consulta.
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
from app.models.user_prefs import UserPrefs
from app.services.metrics_service import get_intent_distribution

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
    is_test: bool = False,
    delivery_status: str = "delivered",
    producto: str | None = None,
) -> Consultation:
    """Inserta una Consultation de prueba.

    is_test=True simula una fila de datos sintéticos (seed/smoke test) para
    verificar que las métricas del dashboard la excluyan.
    """
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
        is_test=is_test,
        delivery_status=delivery_status,
        producto=producto,
    )
    db.add(reg)
    db.commit()
    return reg


def _identidad(
    db: Session,
    *,
    phone_hash: str,
    identity_type: str,
    group_label: str | None = None,
    comuna: str | None = "Traiguén",
    localidad: str | None = None,
) -> UserPrefs:
    """Inserta una identidad individual o grupal sin exponerla en respuestas."""
    prefs = UserPrefs(
        phone_hash=phone_hash,
        identity_type=identity_type,
        group_label=group_label,
        comuna=comuna,
        localidad=localidad,
        dataset_consent=False,
    )
    db.add(prefs)
    db.commit()
    return prefs


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
        resp = await client.get("/api/v1/admin/metrics/dashboard", headers=_ADMIN_HEADERS)
        assert resp.status_code == 200


class TestPilotoMetricsEndpoint:
    """Contrato de ventana explícita para el endpoint de cierre del piloto."""

    async def test_sin_ventana_es_fail_closed(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/admin/metrics/piloto", headers=_ADMIN_HEADERS)

        assert resp.status_code == 200
        assert resp.json()["window"] == {"pilot_started_at": None, "pilot_ended_at": None}
        assert resp.json()["metrics"]["total_consultas"] == 0

    async def test_ventana_se_devuelve_y_rechaza_fechas_naive(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/admin/metrics/piloto",
            params={
                "pilot_started_at": "2026-08-01T00:00:00Z",
                "pilot_ended_at": "2026-08-08T00:00:00Z",
            },
            headers=_ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["window"]["pilot_started_at"].endswith("+00:00")
        assert resp.json()["metrics"]["total_consultas"] == 0

        invalid = await client.get(
            "/api/v1/admin/metrics/piloto",
            params={
                "pilot_started_at": "2026-08-01T00:00:00",
                "pilot_ended_at": "2026-08-08T00:00:00",
            },
            headers=_ADMIN_HEADERS,
        )
        assert invalid.status_code == 422


# ── /dashboard ─────────────────────────────────────────────────────


class TestDashboard:
    """KPIs principales."""

    async def test_sin_datos_retorna_ceros(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/admin/metrics/dashboard", headers=_ADMIN_HEADERS)
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

    async def test_con_datos_hoy_cuenta_consultas(self, client: AsyncClient, tmp_path: Path) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, intent="precio", phone_hash="h1" + "a" * 62)
            _consulta(db, intent="clima", phone_hash="h2" + "a" * 62)
            _consulta(
                db,
                intent="desconocido",
                phone_hash="h3" + "a" * 62,
                delivery_status="failed",
            )

        resp = await client.get("/api/v1/admin/metrics/dashboard", headers=_ADMIN_HEADERS)
        data = resp.json()
        assert data["today"] == 3
        # Dos respuestas fueron entregadas y una falló.
        assert data["success_rate"] == pytest.approx(0.667, abs=0.01)
        assert data["error_count_24h"] == 1
        assert data["active_farmers_7d"] == 3
        # sparkline tiene 14 puntos y el path no es vacío (hay datos hoy).
        assert len(data["sparkline"]) == 14
        assert data["sparkline_line_path"]

    async def test_ayer_no_cuenta_para_hoy(self, client: AsyncClient, tmp_path: Path) -> None:
        ayer = datetime.datetime.now() - datetime.timedelta(days=1)
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, intent="precio", created_at=ayer)
        resp = await client.get("/api/v1/admin/metrics/dashboard", headers=_ADMIN_HEADERS)
        data = resp.json()
        assert data["today"] == 0
        assert data["yesterday"] == 1
        # today=0 vs yesterday=1 → caída del 100%.
        assert data["today_trend_pct"] == pytest.approx(-100.0)

    async def test_today_trend_pct_calcula_variacion(self, client: AsyncClient, tmp_path: Path) -> None:
        ayer = datetime.datetime.now() - datetime.timedelta(days=1)
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, intent="precio", created_at=ayer)  # 1 ayer
            _consulta(db, intent="precio")  # 2 hoy
            _consulta(db, intent="clima")
        resp = await client.get("/api/v1/admin/metrics/dashboard", headers=_ADMIN_HEADERS)
        data = resp.json()
        assert data["today"] == 2
        assert data["yesterday"] == 1
        # (2 - 1) / 1 * 100 = +100%.
        assert data["today_trend_pct"] == pytest.approx(100.0)

    async def test_is_test_no_cuenta_en_kpis(self, client: AsyncClient, tmp_path: Path) -> None:
        """Regresión: filas is_test=True no deben sesgar los KPIs del dashboard.

        148 filas de datos sintéticos ("Hola esta es una prueba") en la DB
        real inflaban el conteo y distorsionaban success_rate/latencia.
        """
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, intent="precio", phone_hash="h1" + "a" * 62)
            _consulta(
                db,
                intent="desconocido",
                phone_hash="h2" + "a" * 62,
                query_text="Hola esta es una prueba",
                response_text="Respuesta mock del LLM",
                latency_ms=1,
                is_test=True,
                delivery_status="failed",
            )
        resp = await client.get("/api/v1/admin/metrics/dashboard", headers=_ADMIN_HEADERS)
        data = resp.json()
        # Solo la consulta real cuenta; la de test queda afuera.
        assert data["today"] == 1
        assert data["success_rate"] == 1.0
        assert data["error_count_24h"] == 0
        assert data["active_farmers_7d"] == 1

    async def test_success_rate_mide_entrega_no_intent(self, client: AsyncClient, tmp_path: Path) -> None:
        """Un intent válido fallido no cuenta; uno desconocido entregado sí."""
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, intent="precio", delivery_status="failed")
            _consulta(
                db,
                intent="desconocido",
                phone_hash="b" * 64,
                delivery_status="delivered",
            )
            _consulta(
                db,
                intent="clima",
                phone_hash="c" * 64,
                delivery_status="pending",
            )
            _consulta(
                db,
                intent="credito",
                phone_hash="d" * 64,
                delivery_status="delivered",
            )

        resp = await client.get("/api/v1/admin/metrics/dashboard", headers=_ADMIN_HEADERS)

        data = resp.json()
        # Dos entregadas y una fallida; pending no entra al denominador.
        # El intent crédito no altera la definición basada en delivery.
        assert data["success_rate"] == pytest.approx(0.667, abs=0.001)
        assert data["error_count_24h"] == 1


# ── /daily ─────────────────────────────────────────────────────────


class TestDaily:
    """Series diarias con relleno de huecos."""

    async def test_rellena_huecos_con_cero(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/admin/metrics/daily?days=7", headers=_ADMIN_HEADERS)
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
        resp = await client.get("/api/v1/admin/metrics/daily?days=3", headers=_ADMIN_HEADERS)
        data = resp.json()
        hoy = datetime.date.today().isoformat()
        ultimo = data[-1]
        assert ultimo["date"] == hoy
        assert ultimo["count"] == 1


# ── /latency ───────────────────────────────────────────────────────


class TestLatency:
    """Estadísticos de latencia."""

    async def test_sin_datos_retorna_ceros(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/admin/metrics/latency", headers=_ADMIN_HEADERS)
        data = resp.json()
        assert data["count"] == 0
        assert data["avg"] == 0.0

    async def test_avg_sobre_muestra(self, client: AsyncClient, tmp_path: Path) -> None:
        with next(_session_test_db(tmp_path)) as db:
            for ms in (100, 200, 300):
                _consulta(db, latency_ms=ms)
        resp = await client.get("/api/v1/admin/metrics/latency?days=1", headers=_ADMIN_HEADERS)
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
            _consulta(db, intent="credito")
            _consulta(db, intent="credito")
            _consulta(db, intent="desconocido")
            # Otros intents no se mezclan con desconocido ni con el total
            # operativo de IntentDistribution.
            _consulta(db, intent="corpus")
            _consulta(db, intent="resumen")
            distribution = get_intent_distribution(db, days=1)
        resp = await client.get("/api/v1/admin/metrics/intents?days=1", headers=_ADMIN_HEADERS)
        data = resp.json()
        assert data["precio"] == 2
        assert data["clima"] == 1
        assert data["credito"] == 2
        assert data["desconocido"] == 1
        assert distribution.total == 6

    async def test_is_test_excluido_del_conteo(self, client: AsyncClient, tmp_path: Path) -> None:
        """Regresión: filas is_test=True no deben aparecer en la distribución."""
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, intent="precio")
            _consulta(db, intent="credito", phone_hash="b" * 64)
            _consulta(db, intent="credito", phone_hash="c" * 64, is_test=True)
            _consulta(db, intent="desconocido", is_test=True)
            _consulta(db, intent="desconocido", is_test=True)
            distribution = get_intent_distribution(db, days=1)
        resp = await client.get("/api/v1/admin/metrics/intents?days=1", headers=_ADMIN_HEADERS)
        data = resp.json()
        assert data["precio"] == 1
        assert data["credito"] == 1
        assert data["desconocido"] == 0
        assert distribution.total == 2


# ── /products ──────────────────────────────────────────────────────


class TestProducts:
    """Top productos desde Consultation.producto."""

    async def test_producto_cuenta_con_contenido_redactado(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """El ranking funciona sin conservar query_text ni response_text."""
        with next(_session_test_db(tmp_path)) as db:
            db.add(
                OdepaPrice(
                    producto="papa",
                    mercado="Lo Valledor",
                    precio_kg=1200,
                    unidad="kg",
                    fecha=datetime.date.today(),
                )
            )
            db.commit()
            _consulta(
                db,
                intent="precio",
                query_text="",
                response_text="",
                producto="  PAPA  ",
            )
        resp = await client.get("/api/v1/admin/metrics/products?days=1", headers=_ADMIN_HEADERS)
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "papa"
        assert data[0]["queries"] == 1
        assert data[0]["pct"] == 100.0

    async def test_texto_libre_no_infiere_producto_sin_valor_estructurado(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """Null y vacío se excluyen aunque el texto mencione un producto."""
        with next(_session_test_db(tmp_path)) as db:
            db.add(
                OdepaPrice(
                    producto="papa",
                    mercado="X",
                    precio_kg=1,
                    unidad="kg",
                    fecha=datetime.date.today(),
                )
            )
            db.commit()
            _consulta(
                db,
                intent="precio",
                query_text="¿cuánto cuesta la papa?",
                producto=None,
            )
            _consulta(
                db,
                intent="precio",
                query_text="precio papa",
                producto="   ",
                phone_hash="b" * 64,
            )
        resp = await client.get("/api/v1/admin/metrics/products?days=1", headers=_ADMIN_HEADERS)
        assert resp.json() == []

    async def test_sin_productos_retorna_lista_vacia(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/admin/metrics/products", headers=_ADMIN_HEADERS)
        assert resp.json() == []


# ── /errors ────────────────────────────────────────────────────────


class TestErrors:
    """Tasa de error (intent desconocido)."""

    async def test_tasa_y_lista_errores(self, client: AsyncClient, tmp_path: Path) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, intent="precio")
            _consulta(
                db,
                intent="desconocido",
                query_text="bla bla incomprensible",
            )
        resp = await client.get("/api/v1/admin/metrics/errors?days=1", headers=_ADMIN_HEADERS)
        data = resp.json()
        assert data["total_queries"] == 2
        assert data["total_errors"] == 1
        assert data["rate"] == pytest.approx(0.5)
        assert len(data["recent"]) == 1
        assert data["recent"][0]["query"] == "bla bla incomprensible"


# ── /recent ────────────────────────────────────────────────────────


class TestRecent:
    """Consultas recientes."""

    async def test_lista_recientes_ordenadas(self, client: AsyncClient, tmp_path: Path) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, query_text="primera consulta")
            _consulta(db, query_text="segunda consulta")
        resp = await client.get("/api/v1/admin/metrics/recent?hours=24", headers=_ADMIN_HEADERS)
        data = resp.json()
        assert len(data) == 2
        # Cada entrada tiene los campos esperados.
        for entry in data:
            assert {"text", "intent", "latency_s", "ok", "status_text", "ago", "ts"} <= set(entry)

    async def test_limit_acota_resultado(self, client: AsyncClient, tmp_path: Path) -> None:
        with next(_session_test_db(tmp_path)) as db:
            for i in range(5):
                _consulta(db, query_text=f"consulta {i}")
        resp = await client.get("/api/v1/admin/metrics/recent?hours=24&limit=2", headers=_ADMIN_HEADERS)
        assert len(resp.json()) == 2

    async def test_estado_reciente_refleja_entrega_real(self, client: AsyncClient, tmp_path: Path) -> None:
        """El intent no puede convertir un envío fallido en “ok”."""
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, intent="precio", delivery_status="failed")
            _consulta(
                db,
                intent="desconocido",
                phone_hash="b" * 64,
                delivery_status="delivered",
            )
            _consulta(
                db,
                intent="clima",
                phone_hash="c" * 64,
                delivery_status="pending",
            )

        resp = await client.get("/api/v1/admin/metrics/recent?hours=24", headers=_ADMIN_HEADERS)
        por_estado = {entry["status_text"]: entry["ok"] for entry in resp.json()}

        assert por_estado == {
            "pendiente": False,
            "ok": True,
            "error": False,
        }


# ── /groups ─────────────────────────────────────────────────────────


class TestGroups:
    """Métricas colectivas PRODESAL sin detalle de integrantes."""

    async def test_requiere_auth_y_acepta_admin(
        self,
        client: AsyncClient,
    ) -> None:
        """El endpoint nuevo hereda el gate X-Admin-Key del router."""
        unauthorized = await client.get("/api/v1/admin/metrics/groups")
        authorized = await client.get(
            "/api/v1/admin/metrics/groups",
            headers=_ADMIN_HEADERS,
        )

        assert unauthorized.status_code == 401
        assert authorized.status_code == 200

    @pytest.mark.parametrize("days", [0, 91])
    async def test_valida_ventana(
        self,
        client: AsyncClient,
        days: int,
    ) -> None:
        """SQLite nunca recibe ventanas fuera del rango común 1..90."""
        response = await client.get(
            f"/api/v1/admin/metrics/groups?days={days}",
            headers=_ADMIN_HEADERS,
        )

        assert response.status_code == 422

    async def test_grupo_sin_consultas_aparece_con_ceros(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """El LEFT JOIN conserva una agrupación todavía inactiva."""
        phone_hash = "grupo-sin-actividad"
        with next(_session_test_db(tmp_path)) as db:
            _identidad(
                db,
                phone_hash=phone_hash,
                identity_type="prodesal_group",
                group_label="Comité Los Aromos",
                comuna="Traiguén",
                localidad="Quechereguas",
            )

        response = await client.get(
            "/api/v1/admin/metrics/groups?days=30",
            headers=_ADMIN_HEADERS,
        )

        assert response.status_code == 200
        assert response.json() == [
            {
                "group_label": "Comité Los Aromos",
                "comuna": "Traiguén",
                "localidad": "Quechereguas",
                "total_consultations": 0,
                "delivered": 0,
                "failed": 0,
                "pending": 0,
                "delivery_rate": 0.0,
                "avg_latency_ms": 0.0,
                "last_activity": None,
            }
        ]

    async def test_dos_grupos_aislados_de_identidades_individuales(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """Solo group_label/comuna/localidad definen agregados visibles."""
        group_a_member_1 = "grupo-a-integrante-1"
        group_a_member_2 = "grupo-a-integrante-2"
        group_b_member = "grupo-b-integrante"
        individual = "productor-individual"
        with next(_session_test_db(tmp_path)) as db:
            for member_hash in (group_a_member_1, group_a_member_2):
                _identidad(
                    db,
                    phone_hash=member_hash,
                    identity_type="prodesal_group",
                    group_label="Grupo A",
                    comuna="Traiguén",
                    localidad="Quino",
                )
            _identidad(
                db,
                phone_hash=group_b_member,
                identity_type="prodesal_group",
                group_label="Grupo B",
                comuna="Lumaco",
                localidad="Capitán Pastene",
            )
            _identidad(
                db,
                phone_hash=individual,
                identity_type="individual",
            )
            _consulta(db, phone_hash=group_a_member_1, query_text="secreto a1")
            _consulta(db, phone_hash=group_a_member_2, query_text="secreto a2")
            _consulta(db, phone_hash=group_b_member, query_text="secreto b")
            _consulta(db, phone_hash=individual, query_text="secreto individual")

        response = await client.get(
            "/api/v1/admin/metrics/groups?days=1",
            headers=_ADMIN_HEADERS,
        )
        data = response.json()

        assert response.status_code == 200
        assert [group["group_label"] for group in data] == ["Grupo A", "Grupo B"]
        assert [group["total_consultations"] for group in data] == [2, 1]
        serialized = response.text
        for forbidden in (
            group_a_member_1,
            group_a_member_2,
            group_b_member,
            individual,
            "secreto a1",
            "secreto a2",
            "secreto b",
            "secreto individual",
            "phone_hash",
            "query_text",
            "response_text",
            "members",
        ):
            assert forbidden not in serialized

    async def test_estados_tasa_latencia_y_filtro_is_test(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """Pending se separa y no entra al denominador de entrega."""
        phone_hash = "grupo-estados"
        with next(_session_test_db(tmp_path)) as db:
            _identidad(
                db,
                phone_hash=phone_hash,
                identity_type="prodesal_group",
                group_label="Grupo Estados",
            )
            _consulta(
                db,
                phone_hash=phone_hash,
                delivery_status="delivered",
                latency_ms=100,
            )
            _consulta(
                db,
                phone_hash=phone_hash,
                delivery_status="failed",
                latency_ms=300,
            )
            _consulta(
                db,
                phone_hash=phone_hash,
                delivery_status="pending",
                latency_ms=500,
            )
            _consulta(
                db,
                phone_hash=phone_hash,
                delivery_status="delivered",
                latency_ms=1,
                is_test=True,
            )

        response = await client.get(
            "/api/v1/admin/metrics/groups?days=1",
            headers=_ADMIN_HEADERS,
        )
        group = response.json()[0]

        assert group["total_consultations"] == 3
        assert group["delivered"] == 1
        assert group["failed"] == 1
        assert group["pending"] == 1
        assert group["delivery_rate"] == pytest.approx(0.5)
        assert group["avg_latency_ms"] == pytest.approx(300.0)
        assert group["last_activity"] is not None

    async def test_ventana_excluye_consultas_antiguas_sin_ocultar_grupo(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """Una actividad fuera de ventana deja el grupo visible con cero."""
        phone_hash = "grupo-antiguo"
        old_date = datetime.datetime.now() - datetime.timedelta(days=10)
        with next(_session_test_db(tmp_path)) as db:
            _identidad(
                db,
                phone_hash=phone_hash,
                identity_type="prodesal_group",
                group_label="Grupo Histórico",
            )
            _consulta(
                db,
                phone_hash=phone_hash,
                created_at=old_date,
                delivery_status="delivered",
            )

        response = await client.get(
            "/api/v1/admin/metrics/groups?days=7",
            headers=_ADMIN_HEADERS,
        )
        group = response.json()[0]

        assert group["group_label"] == "Grupo Histórico"
        assert group["total_consultations"] == 0
        assert group["delivered"] == 0
        assert group["last_activity"] is None


# ── /stages ─────────────────────────────────────────────────────────


class TestStages:
    """Desglose de latencia por etapa del pipeline."""

    async def test_sin_datos_retorna_ceros(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/admin/metrics/stages", headers=_ADMIN_HEADERS)
        data = resp.json()
        assert data["whisper_ms"] == 0.0
        assert data["llm_ms"] == 0.0
        assert data["tts_ms"] == 0.0
        assert data["total_ms"] == 0.0
        assert data["count"] == 0

    async def test_promedio_sobre_muestra(self, client: AsyncClient, tmp_path: Path) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, whisper_ms=800, llm_ms=1200, tts_ms=600)
            _consulta(db, whisper_ms=1000, llm_ms=1400, tts_ms=800)
        resp = await client.get("/api/v1/admin/metrics/stages?days=1", headers=_ADMIN_HEADERS)
        data = resp.json()
        assert data["count"] == 2
        assert data["whisper_ms"] == pytest.approx(900.0)
        assert data["llm_ms"] == pytest.approx(1300.0)
        assert data["tts_ms"] == pytest.approx(700.0)
        assert data["total_ms"] == pytest.approx(2900.0)

    async def test_filtra_registros_sin_timing(self, client: AsyncClient, tmp_path: Path) -> None:
        """Consultas con stage en 0 (pre-migración) no diluyen el promedio."""
        with next(_session_test_db(tmp_path)) as db:
            _consulta(db, whisper_ms=0, llm_ms=0, tts_ms=0)  # pre-migración
            _consulta(db, whisper_ms=500, llm_ms=700, tts_ms=300)  # con timing
        resp = await client.get("/api/v1/admin/metrics/stages?days=1", headers=_ADMIN_HEADERS)
        data = resp.json()
        assert data["count"] == 1  # solo la que tiene timing
        assert data["whisper_ms"] == pytest.approx(500.0)
        assert data["total_ms"] == pytest.approx(1500.0)
