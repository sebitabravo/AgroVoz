"""Tests para modelos SQLAlchemy, migraciones y utilidades de seguridad.

Verifica CRUD, constraints, migraciones Alembic y anonimización
de números de teléfono con HMAC-SHA256 + pepper key.
"""

import datetime
import hashlib
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Float, String, create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.consultation import Consultation
from app.models.odepa_price import OdepaPrice


class TestOdepaPrice:
    """CRUD y constraints del modelo OdepaPrice."""

    def test_crear_precio_basico(self, db: Session) -> None:
        """Un OdepaPrice se crea con campos mínimos y se persiste."""
        precio = OdepaPrice(
            producto="papa",
            mercado="Santiago",
            precio_kg=500,
            fecha=datetime.date(2026, 6, 15),
        )
        db.add(precio)
        db.commit()
        db.refresh(precio)

        assert precio.id is not None
        assert precio.producto == "papa"
        assert precio.mercado == "Santiago"
        assert float(precio.precio_kg) == 500.0
        assert precio.fuente == "ODEPA"  # default
        assert precio.unidad == "kg"  # default
        assert precio.created_at is not None
        assert precio.updated_at is not None

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

    def test_precio_kg_es_decimal(self, db: Session) -> None:
        """precio_kg usa Decimal (Numeric 10,2) para precisión monetaria exacta."""
        precio = OdepaPrice(
            producto="papa", mercado="Santiago", precio_kg=500.5, fecha=datetime.date(2026, 6, 15)
        )
        db.add(precio)
        db.commit()

        assert isinstance(precio.precio_kg, Decimal)
        assert float(precio.precio_kg) == 500.5

    def test_producto_vacio_se_persiste(self, db: Session) -> None:
        """Producto vacío se guarda (validación es responsabilidad del service, no del modelo)."""
        precio = OdepaPrice(
            producto="", mercado="Santiago", precio_kg=500, fecha=datetime.date(2026, 6, 15)
        )
        db.add(precio)
        db.commit()

        assert precio.producto == ""

    def test_unique_constraint_producto_mercado_fecha(self, db: Session) -> None:
        """No se pueden insertar dos precios con el mismo (producto, mercado, fecha)."""
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
        db.rollback()  # Limpia el estado fallido de la sesión tras IntegrityError

        # Verifica que la sesión sigue usable después del rollback
        precio3 = OdepaPrice(
            producto="trigo", mercado="Santiago", precio_kg=320, fecha=datetime.date(2026, 6, 15)
        )
        db.add(precio3)
        db.commit()
        assert precio3.id is not None

    def test_updated_at_no_cambia_con_update(self, db: Session) -> None:
        """updated_at no se modifica al hacer UPDATE (datos ODEPA append-only, sin onupdate)."""
        precio = OdepaPrice(
            producto="papa", mercado="Santiago", precio_kg=500, fecha=datetime.date(2026, 6, 15)
        )
        db.add(precio)
        db.commit()
        db.refresh(precio)

        updated_at_original = precio.updated_at

        # Simular UPDATE (aunque en producción los datos ODEPA son append-only)
        precio.precio_kg = Decimal("550.00")
        db.commit()
        db.refresh(precio)

        assert precio.updated_at == updated_at_original

    def test_campos_requeridos_sin_default(self, db: Session) -> None:
        """Falta producto (nullable=False sin default) lanza IntegrityError."""
        precio = OdepaPrice(
            mercado="Santiago",
            precio_kg=500,
            fecha=datetime.date(2026, 6, 15),
            # producto omitido intencionalmente
        )
        db.add(precio)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


class TestConsultation:
    """CRUD y constraints del modelo Consultation."""

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

    def test_phone_hash_formato_hex_64(self, db: Session) -> None:
        """phone_hash almacena 64 caracteres hexadecimales en minúscula."""
        # Simula un hash HMAC-SHA256 real
        hash_real = hashlib.sha256(b"+56912345678").hexdigest()
        assert len(hash_real) == 64
        assert all(c in "0123456789abcdef" for c in hash_real)

        consulta = Consultation(
            phone_hash=hash_real,
            intent="clima",
            query_text="¿Lloverá mañana?",
            response_text="No hay lluvia pronosticada.",
        )
        db.add(consulta)
        db.commit()

        assert consulta.phone_hash == hash_real

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

    def test_intent_largo_soportado(self, db: Session) -> None:
        """String(50) permite intents compuestos post-MVP (ej: 'precio_historico_semanal')."""
        consulta = Consultation(
            phone_hash="c" * 64,
            intent="precio_historico_semanal",
            query_text="test",
            response_text="test",
        )
        db.add(consulta)
        db.commit()
        db.refresh(consulta)

        assert consulta.intent == "precio_historico_semanal"
        assert len(consulta.intent) <= 50

    def test_duracion_y_latencia_default_cero(self, db: Session) -> None:
        """audio_duration_ms y latency_ms por defecto son 0."""
        consulta = Consultation(
            phone_hash="d" * 64,
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
            phone_hash="e" * 64,
            intent="precio",
            query_text="test",
            response_text="test",
        )
        db.add(consulta)
        db.commit()

        r = repr(consulta)
        assert "e" * 64 not in r  # hash completo NO visible
        assert "eeeeeeee..." in r  # solo primeros 8 chars
        assert "precio" in r

    def test_campos_requeridos_sin_default(self, db: Session) -> None:
        """Falta response_text (nullable=False sin default) lanza IntegrityError."""
        consulta = Consultation(
            phone_hash="f" * 64,
            query_text="test",
            # response_text omitido intencionalmente
        )
        db.add(consulta)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


class TestPhoneHash:
    """Anonimización de números de teléfono con HMAC-SHA256 + pepper key."""

    def test_hash_phone_64_chars_hex(self) -> None:
        """hash_phone devuelve 64 caracteres hexadecimales en minúscula."""
        from app.core.phone_hash import hash_phone

        result = hash_phone("+56912345678", "dev-pepper")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_phone_deterministico(self) -> None:
        """Mismo teléfono + misma pepper = mismo hash."""
        from app.core.phone_hash import hash_phone

        h1 = hash_phone("+56912345678", "dev-pepper")
        h2 = hash_phone("+56912345678", "dev-pepper")
        assert h1 == h2

    def test_hash_phone_pepper_vacio_error(self) -> None:
        """Pepper vacío lanza ValueError para evitar hashing sin clave."""
        from app.core.phone_hash import hash_phone

        with pytest.raises(ValueError, match="phone_hash_pepper"):
            hash_phone("+56912345678", "")

    def test_hash_phone_diferente_pepper_diferente_hash(self) -> None:
        """Distinta pepper produce distinto hash para el mismo teléfono."""
        from app.core.phone_hash import hash_phone

        h1 = hash_phone("+56912345678", "pepper-a")
        h2 = hash_phone("+56912345678", "pepper-b")
        assert h1 != h2

    def test_hash_phone_diferente_telefono_diferente_hash(self) -> None:
        """Distinto teléfono produce distinto hash con la misma pepper."""
        from app.core.phone_hash import hash_phone

        h1 = hash_phone("+56912345678", "dev-pepper")
        h2 = hash_phone("+56987654321", "dev-pepper")
        assert h1 != h2

    def test_validate_phone_hash_valido(self) -> None:
        """Un hash HMAC-SHA256 real de 64 chars hex en minúscula es válido."""
        from app.core.phone_hash import hash_phone, validate_phone_hash

        hashed = hash_phone("+56912345678", "dev-pepper")
        assert validate_phone_hash(hashed) is True

    def test_validate_phone_hash_todo_digitos(self) -> None:
        """Hash de solo dígitos (0-9) es válido. islower() fallaría, value != value.lower() no."""
        from app.core.phone_hash import validate_phone_hash

        assert validate_phone_hash("0" * 64) is True

    def test_validate_phone_hash_mayusculas(self) -> None:
        """Hash con mayúsculas es inválido (formato canónico: minúscula)."""
        from app.core.phone_hash import validate_phone_hash

        assert validate_phone_hash("A" * 64) is False

    def test_validate_phone_hash_longitud_incorrecta(self) -> None:
        """Hash con longitud != 64 es inválido."""
        from app.core.phone_hash import validate_phone_hash

        assert validate_phone_hash("a" * 63) is False
        assert validate_phone_hash("a" * 65) is False

    def test_validate_phone_hash_caracteres_no_hex(self) -> None:
        """Hash con caracteres no hexadecimales es inválido."""
        from app.core.phone_hash import validate_phone_hash

        assert validate_phone_hash("g" * 64) is False
        assert validate_phone_hash("z" * 64) is False

    def test_validate_phone_hash_prefijo_signos(self) -> None:
        """Prefijos +, - y whitespace son rechazados.

        int(value, 16) acepta signos y espacios, pero regex no.
        Verifica que validate_phone_hash no tenga bypass via int().
        """
        from app.core.phone_hash import validate_phone_hash

        assert validate_phone_hash("+" + "0" * 63) is False
        assert validate_phone_hash("-0" + "0" * 62) is False
        assert validate_phone_hash(" " + "0" * 63) is False
        assert validate_phone_hash("\t" + "0" * 63) is False

    def test_validate_phone_hash_none_rechazado(self) -> None:
        """None o tipos no str retornan False sin crashear."""
        from app.core.phone_hash import validate_phone_hash

        # Probamos edge case en runtime: None y no-str retornan False sin crashear
        assert validate_phone_hash(None) is False  # type: ignore[arg-type]
        assert validate_phone_hash(123) is False  # type: ignore[arg-type]


# Ruta al alembic.ini y migrations/ relativas a este archivo de test.
# script_location en alembic.ini es relativo al CWD, no al archivo .ini.
# Para que los tests funcionen desde cualquier CWD, sobreescribimos
# script_location con la ruta absoluta.
_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


class TestMigraciones:
    """Verifica que alembic upgrade/downgrade funcione correctamente."""

    def test_upgrade_head_crea_tablas(self, tmp_path: Path) -> None:
        """alembic upgrade head crea las tablas consultations y odepa_prices."""
        from alembic import command
        from alembic.config import Config

        db_path = tmp_path / "test_agrovoz.db"

        alembic_cfg = Config(str(_ALEMBIC_INI))
        alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
        alembic_cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))

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
        alembic_cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))

        command.upgrade(alembic_cfg, "head")
        command.upgrade(alembic_cfg, "head")  # segunda vez: no debe lanzar excepción

        assert db_path.exists()

    def test_downgrade_funciona(self, tmp_path: Path) -> None:
        """Downgrade -1 revierte refine_column_types sin errores.

        Verifica que:
        - precio_kg vuelve a FLOAT (ya no Numeric)
        - intent vuelve a VARCHAR(20) (ya no String(50))
        - UniqueConstraint uq_odepa_producto_mercado_fecha sobrevive
        """
        from alembic import command
        from alembic.config import Config

        db_path = tmp_path / "test_agrovoz.db"

        alembic_cfg = Config(str(_ALEMBIC_INI))
        alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
        alembic_cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))

        # Aplicar todas las migraciones (head = 8f2a4c7e1d90 add_consultation_stage_timing)
        command.upgrade(alembic_cfg, "head")

        # Downgrade dos pasos → 9fad6bdb1443 unique_constraint_odepa.
        # -1 baja a 37086656cc4e (refine_column_types, precio_kg NUMERIC).
        # -2 baja a 9fad6bdb1443 (precio_kg FLOAT, lo que verifica este test).
        command.downgrade(alembic_cfg, "-2")

        engine = create_engine(f"sqlite:///{db_path}")
        inspector = inspect(engine)
        tablas = inspector.get_table_names()

        # DB y tablas siguen existiendo
        assert "alembic_version" in tablas
        assert "consultations" in tablas
        assert "odepa_prices" in tablas

        # Verificar que precio_kg volvió a FLOAT (downgrade de refine_column_types)
        columnas_odepa = {c["name"]: c["type"] for c in inspector.get_columns("odepa_prices")}
        precio_kg_type = columnas_odepa["precio_kg"]
        assert isinstance(precio_kg_type, Float), (
            f"precio_kg debería ser Float tras downgrade, es {precio_kg_type}"
        )

        # Verificar que intent volvió a VARCHAR(20) (downgrade de refine_column_types)
        columnas_cons = {c["name"]: c["type"] for c in inspector.get_columns("consultations")}
        intent_type = columnas_cons["intent"]
        assert isinstance(intent_type, String), (
            f"intent debería ser String/VARCHAR tras downgrade, es {type(intent_type)}"
        )
        assert intent_type.length == 20, (
            f"intent debería tener length=20 tras downgrade, tiene length={intent_type.length}"
        )

        # UniqueConstraint agregada en 9fad6bdb1443 debe sobrevivir al downgrade
        constraints = inspector.get_unique_constraints("odepa_prices")
        constraint_names = [c["name"] for c in constraints]
        assert "uq_odepa_producto_mercado_fecha" in constraint_names, (
            "UniqueConstraint uq_odepa_producto_mercado_fecha debe existir tras downgrade -1"
        )

        engine.dispose()

    def test_downgrade_completo_vuelve_a_base(self, tmp_path: Path) -> None:
        """Downgrade total revierte TODAS las migraciones y elimina tablas del modelo."""
        from alembic import command
        from alembic.config import Config

        db_path = tmp_path / "test_agrovoz.db"

        alembic_cfg = Config(str(_ALEMBIC_INI))
        alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
        alembic_cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))

        # Aplicar todas las migraciones
        command.upgrade(alembic_cfg, "head")

        # Downgrade a base (antes de la primera migración)
        command.downgrade(alembic_cfg, "base")

        # Solo queda alembic_version. Las tablas del modelo deben desaparecer.
        engine = create_engine(f"sqlite:///{db_path}")
        inspector = inspect(engine)
        tablas = inspector.get_table_names()

        assert "alembic_version" in tablas
        assert "consultations" not in tablas, (
            "consultations no debería existir tras downgrade a base"
        )
        assert "odepa_prices" not in tablas, (
            "odepa_prices no debería existir tras downgrade a base"
        )

        engine.dispose()
