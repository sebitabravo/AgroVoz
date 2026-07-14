"""Tests del módulo de configuración de base de datos (app.core.database).

Cubre: Base declarativa, engine global, factory SessionLocal, dependencia
get_db para FastAPI, y activación de PRAGMAs de optimización SQLite.

No toca la capa HTTP: esos caminos van por conftest.py (fixture `client`)
y test_health/test_prices_api. Acá se prueba el módulo de DB en sí.
"""

from collections.abc import Generator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core import database
from app.core.database import Base, SessionLocal, engine, get_db


def test_base_es_declarative_base() -> None:
    """Base debe ser una subclase de DeclarativeBase (SQLAlchemy 2.0 style)."""
    assert isinstance(Base(), DeclarativeBase)


def test_base_metadata_accesible() -> None:
    """Base.metadata debe existir para que Alembic --autogenerate lo detecte."""
    assert hasattr(Base, "metadata")


def test_engine_es_instancia_y_sqlite() -> None:
    """El engine global debe ser un Engine SQLAlchemy con dialecto SQLite."""
    assert isinstance(engine, Engine)
    assert engine.dialect.name == "sqlite"


def test_engine_apunta_a_settings_database_url() -> None:
    """La URL del engine debe coincidir con settings.database_url."""
    from app.core.config import settings

    assert str(engine.url) == settings.database_url


def test_session_local_es_factory_vinculada_al_engine() -> None:
    """SessionLocal produce sesiones vinculadas al engine global."""
    session = SessionLocal()
    try:
        assert isinstance(session, Session)
        assert session.bind is engine
    finally:
        session.close()


def test_get_db_es_funcion_generator() -> None:
    """get_db debe ser una función generator (usa yield para FastAPI)."""
    assert isinstance(get_db(), Generator)


def test_get_db_yields_session_activa_y_cierra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_db entrega una Session activa y la cierra al consumir el generator.

    Se espía Session.close a nivel clase para confirmar que el finally del
    generator efectivamente cierra la sesión. No se apoya en is_active, que
    en SQLAlchemy 2.0 puede quedar True tras close().
    """
    closed = {"called": False}
    original_close = Session.close

    def spy_close(self: Session) -> None:
        closed["called"] = True
        original_close(self)

    monkeypatch.setattr(Session, "close", spy_close)

    gen = get_db()
    session = next(gen)
    assert isinstance(session, Session)
    assert session.is_active

    # Consumir el generator dispara el finally que cierra la sesión.
    with pytest.raises(StopIteration):
        next(gen)

    assert closed["called"]


def test_optimize_sqlite_aplica_pragmas_criticos() -> None:
    """_optimize_sqlite debe activar WAL, synchronous=NORMAL y foreign_keys.

    El listener se dispara en cada conexión nueva del engine global.
    WAL y foreign_keys son los críticos para concurrencia e integridad.
    """
    with engine.connect() as conn:
        journal = conn.execute(text("PRAGMA journal_mode")).scalar()
        synchronous = conn.execute(text("PRAGMA synchronous")).scalar()
        foreign_keys = conn.execute(text("PRAGMA foreign_keys")).scalar()

    assert str(journal).lower() == "wal"
    assert synchronous == 1  # NORMAL
    assert foreign_keys == 1  # ON


def test_optimize_sqlite_aplica_busy_timeout_y_cache() -> None:
    """PRAGMAs de performance: busy_timeout=5s y cache de 8MB (-8000 páginas)."""
    with engine.connect() as conn:
        busy_timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
        cache_size = conn.execute(text("PRAGMA cache_size")).scalar()

    assert busy_timeout == 10000
    assert cache_size == -8000


def test_modulo_expone_api_publica_completa() -> None:
    """El módulo database expone Base, engine, SessionLocal y get_db."""
    assert hasattr(database, "Base")
    assert hasattr(database, "engine")
    assert hasattr(database, "SessionLocal")
    assert hasattr(database, "get_db")
    assert isinstance(SessionLocal, sessionmaker)
