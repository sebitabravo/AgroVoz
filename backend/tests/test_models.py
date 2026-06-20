"""Tests para modelos SQLAlchemy y migraciones."""

import datetime
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

# Imports a nivel módulo: necesarios ANTES de que las fixtures llamen
# a Base.metadata.create_all(). Si se importan dentro del test, la
# primera fixture crea tablas sin metadata de modelos y falla.
from app.models.consultation import Consultation  # registra modelo en Base.metadata
from app.models.odepa_price import OdepaPrice  # registra modelo en Base.metadata


class TestOdepaPrice:
    """CRUD y constraints del modelo OdepaPrice."""

    @pytest.fixture
    def db(self, tmp_path: Path) -> Generator[Session, None, None]:
        """Engine SQLite temporal con tablas creadas desde los modelos."""
        from app.core.database import Base

        db_path = tmp_path / "test.db"
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

    def test_crear_precio_basico(self, db: Session) -> None:
        """Un OdepaPrice se crea con campos mínimos y se persiste."""
        precio = OdepaPrice(
            producto="papa",
            mercado="Santiago",
            precio_kg=500.0,
            unidad="kg",
            fecha=datetime.date(2026, 6, 15),
        )
        db.add(precio)
        db.commit()
        db.refresh(precio)

        assert precio.id is not None
        assert precio.producto == "papa"
        assert precio.mercado == "Santiago"
        assert precio.precio_kg == 500.0
        assert precio.fuente == "ODEPA"  # default
        assert precio.created_at is not None

    def test_consultar_precio_por_producto(self, db: Session) -> None:
        """Se puede filtrar por producto con query."""
        db.add_all([
            OdepaPrice(producto="papa", mercado="Santiago", precio_kg=500, fecha=datetime.date(2026, 6, 15)),
            OdepaPrice(producto="papa", mercado="Temuco", precio_kg=480, fecha=datetime.date(2026, 6, 15)),
            OdepaPrice(producto="trigo", mercado="Santiago", precio_kg=320, fecha=datetime.date(2026, 6, 15)),
        ])
        db.commit()

        papas = db.query(OdepaPrice).filter_by(producto="papa").all()
        assert len(papas) == 2

    def test_repr_incluye_producto_y_precio(self, db: Session) -> None:
        """El __repr__ muestra info útil para debugging."""
        precio = OdepaPrice(
            producto="papa", mercado="Santiago", precio_kg=500, fecha=datetime.date(2026, 6, 15)
        )
        db.add(precio)
        db.commit()

        r = repr(precio)
        assert "papa" in r
        assert "Santiago" in r
        assert "500" in r

    def test_precio_kg_float_valido(self, db: Session) -> None:
        """precio_kg acepta valores decimales (ej: 500.5)."""
        precio = OdepaPrice(
            producto="papa", mercado="Santiago", precio_kg=500.5, fecha=datetime.date(2026, 6, 15)
        )
        db.add(precio)
        db.commit()

        assert isinstance(precio.precio_kg, float)
        assert precio.precio_kg == 500.5

    def test_producto_vacio_se_persiste(self, db: Session) -> None:
        """Producto vacío se guarda (validación es responsabilidad del service, no del modelo)."""
        precio = OdepaPrice(
            producto="", mercado="Santiago", precio_kg=500, fecha=datetime.date(2026, 6, 15)
        )
        db.add(precio)
        db.commit()

        assert precio.producto == ""

    def test_unique_constraint_producto_mercado_fecha(
        self, db: Session
    ) -> None:
        """No se pueden insertar dos precios con el mismo (producto, mercado, fecha)."""
        from sqlalchemy.exc import IntegrityError

        precio1 = OdepaPrice(
            producto="papa",
            mercado="Santiago",
            precio_kg=500,
            fecha=datetime.date(2026, 6, 15),
        )
        db.add(precio1)
        db.commit()

        precio2 = OdepaPrice(
            producto="papa",
            mercado="Santiago",
            precio_kg=480,
            fecha=datetime.date(2026, 6, 15),
        )
        db.add(precio2)
        with pytest.raises(IntegrityError):
            db.commit()


class TestConsultation:
    """CRUD y constraints del modelo Consultation."""

    @pytest.fixture
    def db(self, tmp_path: Path) -> Generator[Session, None, None]:
        """Engine SQLite temporal con tablas creadas desde los modelos."""
        from app.core.database import Base

        db_path = tmp_path / "test.db"
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

    def test_crear_consulta_basica(self, db: Session) -> None:
        """Una Consultation se crea y persiste con los campos requeridos."""
        consulta = Consultation(
            phone_hash="a" * 64,
            intent="precio",
            query_text="¿A cuánto está la papa?",
            response_text="La papa está a 500 pesos el kilo en Santiago.",
            audio_duration_ms=3200,
            latency_ms=4500,
        )
        db.add(consulta)
        db.commit()
        db.refresh(consulta)

        assert consulta.id is not None
        assert consulta.phone_hash == "a" * 64
        assert consulta.intent == "precio"
        assert len(consulta.query_text) > 0
        assert consulta.created_at is not None

    def test_phone_hash_longitud_64(self, db: Session) -> None:
        """phone_hash debe ser exactamente 64 caracteres (SHA-256 hex)."""
        hash_valido = "a1b2c3d4" * 8  # 64 chars
        assert len(hash_valido) == 64

        consulta = Consultation(
            phone_hash=hash_valido,
            intent="clima",
            query_text="¿Lloverá mañana?",
            response_text="No hay lluvia pronosticada.",
        )
        db.add(consulta)
        db.commit()

        assert consulta.phone_hash == hash_valido

    def test_intent_default_desconocido(self, db: Session) -> None:
        """Si no se especifica intent, usa 'desconocido' por defecto."""
        consulta = Consultation(
            phone_hash="b" * 64,
            query_text="texto cualquiera",
            response_text="respuesta",
        )
        db.add(consulta)
        db.commit()

        assert consulta.intent == "desconocido"

    def test_duracion_y_latencia_default_cero(self, db: Session) -> None:
        """audio_duration_ms y latency_ms por defecto son 0."""
        consulta = Consultation(
            phone_hash="c" * 64,
            query_text="test",
            response_text="test response",
        )
        db.add(consulta)
        db.commit()

        assert consulta.audio_duration_ms == 0
        assert consulta.latency_ms == 0

    def test_repr_oculta_phone_hash(self, db: Session) -> None:
        """El __repr__ solo muestra los primeros 8 chars del hash (anonimizado)."""
        consulta = Consultation(
            phone_hash="d" * 64,
            intent="precio",
            query_text="test",
            response_text="test",
        )
        db.add(consulta)
        db.commit()

        r = repr(consulta)
        assert "d" * 64 not in r  # hash completo NO visible
        assert "dddddddd..." in r  # solo primeros 8 chars
        assert "precio" in r


# Ruta al alembic.ini relativa a este archivo de test
_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


class TestMigraciones:
    """Verifica que alembic upgrade head funcione y la DB tenga las tablas esperadas."""

    def test_upgrade_head_crea_tablas(self, tmp_path: Path) -> None:
        """alembic upgrade head crea las tablas consultations y odepa_prices."""
        from alembic import command
        from alembic.config import Config

        db_path = tmp_path / "test_agrovoz.db"

        alembic_cfg = Config(str(_ALEMBIC_INI))
        alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

        command.upgrade(alembic_cfg, "head")

        assert db_path.exists()

        engine = create_engine(f"sqlite:///{db_path}")
        inspector = inspect(engine)
        tablas = inspector.get_table_names()

        assert "consultations" in tablas
        assert "odepa_prices" in tablas
        assert "alembic_version" in tablas

        engine.dispose()

    def test_migraciones_son_idempotentes(self, tmp_path: Path) -> None:
        """Ejecutar upgrade head dos veces no falla (idempotencia)."""
        from alembic import command
        from alembic.config import Config

        db_path = tmp_path / "test_agrovoz.db"

        alembic_cfg = Config(str(_ALEMBIC_INI))
        alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

        command.upgrade(alembic_cfg, "head")
        command.upgrade(alembic_cfg, "head")  # segunda vez: no debe lanzar excepción

        assert db_path.exists()
