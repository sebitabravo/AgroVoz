"""Tests para app.services.summary_service: get_consultation_summary.

Cubre: resumen con consultas previas (total, producto top, variacion),
resumen sin consultas previas (mensaje amable), resumen con consultas
sin producto (solo clima), privacidad (nunca expone datos de otros
phone_hash), y ventana de 30 dias (no mas viejas).
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.consultation import Consultation
from app.services.summary_service import get_consultation_summary


# ── Helpers ────────────────────────────────────────────────────────


def _crear_consultation(
    session: Session,
    phone_hash: str,
    intent: str = "precio",
    producto: str | None = None,
    query_text: str = "precio de la papa",
    response_text: str = "450 pesos el kilo",
    created_at: datetime | None = None,
) -> Consultation:
    """Crea y persiste una Consultation de prueba."""
    c = Consultation(
        phone_hash=phone_hash,
        intent=intent,
        producto=producto,
        query_text=query_text,
        response_text=response_text,
        audio_duration_ms=3000,
        latency_ms=500,
        whisper_ms=100,
        llm_ms=200,
        tts_ms=200,
    )
    if created_at is not None:
        c.created_at = created_at
    session.add(c)
    session.commit()
    return c


# ── Resumen con consultas previas ──────────────────────────────────


class TestResumenConConsultas:
    """Resumen con consultas previas del propio phone_hash."""

    def test_resumen_total_y_producto_top(self, db: Session) -> None:
        """Retorna total de consultas y producto más consultado."""
        phone = "hash_agricultor_001"
        # 5 consultas de papa, 3 de tomate.
        for _ in range(5):
            _crear_consultation(db, phone, producto="papa")
        for _ in range(3):
            _crear_consultation(db, phone, producto="tomate")

        resumen = get_consultation_summary(db, phone)

        assert "8 veces" in resumen
        assert "papa" in resumen
        assert "mas consultado" in resumen

    def test_resumen_con_variacion_precio(self, db: Session) -> None:
        """Si hay datos ODEPA, incluye variación de precio del producto top."""
        from app.models.odepa_price import OdepaPrice

        phone = "hash_agricultor_002"
        _crear_consultation(db, phone, producto="papa")

        # Crear datos ODEPA: precio actual y precio de hace 30 días.
        hoy = datetime.now().date()
        hace_30 = hoy - timedelta(days=30)

        precio_antiguo = OdepaPrice(
            producto="papa", mercado="Lo Valledor",
            precio_kg=500, unidad="kg", fecha=hace_30,
        )
        precio_actual = OdepaPrice(
            producto="papa", mercado="Lo Valledor",
            precio_kg=550, unidad="kg", fecha=hoy,
        )
        db.add_all([precio_antiguo, precio_actual])
        db.commit()

        resumen = get_consultation_summary(db, phone)

        assert "papa" in resumen
        # Variación: (550-500)/500 = 10%
        assert "subido" in resumen or "por ciento" in resumen

    def test_resumen_sin_producto_top(self, db: Session) -> None:
        """Consultas sin producto (ej: solo clima) → mensaje genérico."""
        phone = "hash_agricultor_003"
        _crear_consultation(db, phone, intent="clima", producto=None,
                          query_text="clima en traiguen",
                          response_text="8 grados con lluvia")
        _crear_consultation(db, phone, intent="clima", producto=None,
                          query_text="como esta el tiempo",
                          response_text="despejado 15 grados")

        resumen = get_consultation_summary(db, phone)

        assert "2 veces" in resumen
        assert "sigue consultando" in resumen.lower()


# ── Resumen sin consultas previas ──────────────────────────────────


class TestResumenSinConsultas:
    """Sin consultas previas → mensaje amable."""

    def test_sin_consultas_mensaje_amable(self, db: Session) -> None:
        """Retorna mensaje invitando a consultar."""
        resumen = get_consultation_summary(db, "hash_nuevo_001")

        assert "no tienes consultas" in resumen.lower()
        assert "papa" in resumen  # sugiere producto ejemplo
        assert "voz" in resumen


# ── Privacidad ─────────────────────────────────────────────────────


class TestResumenPrivacidad:
    """Nunca expone datos de otros phone_hash."""

    def test_solo_consultas_propias(self, db: Session) -> None:
        """No incluye consultas de otros agricultores."""
        phone_a = "hash_agricultor_a"
        phone_b = "hash_agricultor_b"

        # A tiene 3 consultas de papa.
        for _ in range(3):
            _crear_consultation(db, phone_a, producto="papa")

        # B tiene 10 consultas de tomate.
        for _ in range(10):
            _crear_consultation(db, phone_b, producto="tomate")

        resumen_a = get_consultation_summary(db, phone_a)

        # Solo debe mencionar las 3 consultas de A, no las 10 de B.
        assert "3 veces" in resumen_a
        assert "papa" in resumen_a
        assert "tomate" not in resumen_a

    def test_hash_desconocido_sin_consultas(self, db: Session) -> None:
        """Hash que no existe en la DB → mensaje sin consultas."""
        # Crear consultas de otro hash.
        for _ in range(5):
            _crear_consultation(db, "hash_otro", producto="cebolla")

        resumen = get_consultation_summary(db, "hash_inexistente")

        assert "no tienes consultas" in resumen.lower()


# ── Ventana de 30 dias ────────────────────────────────────────────


class TestResumenVentana30Dias:
    """Solo cuenta consultas de los últimos 30 días."""

    def test_consultas_fuera_de_ventana(self, db: Session) -> None:
        """Consultas de hace 31+ días no se cuentan."""
        phone = "hash_antiguo_001"
        ahora = datetime.now()

        # 2 consultas recientes (hoy).
        _crear_consultation(db, phone, producto="papa")
        _crear_consultation(db, phone, producto="papa")

        # 5 consultas antiguas (hace 31 días) — fuera de ventana.
        fecha_antigua = ahora - timedelta(days=31)
        for _ in range(5):
            _crear_consultation(
                db, phone, producto="tomate", created_at=fecha_antigua
            )

        resumen = get_consultation_summary(db, phone)

        # Solo cuenta las 2 recientes.
        assert "2 veces" in resumen
        assert "papa" in resumen
        assert "tomate" not in resumen

    def test_consultas_en_limite_ventana(self, db: Session) -> None:
        """Consultas dentro de la ventana de 30 días SÍ se cuentan."""
        phone = "hash_limite_001"
        ahora = datetime.now()

        # Consulta dentro de la ventana (hace 29 días y 23 horas).
        fecha_dentro = ahora - timedelta(days=29, hours=23)
        _crear_consultation(
            db, phone, producto="cebolla", created_at=fecha_dentro
        )

        resumen = get_consultation_summary(db, phone)

        assert "1 veces" in resumen
        assert "cebolla" in resumen
