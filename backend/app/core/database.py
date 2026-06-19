"""Configuración de base de datos SQLite vía SQLAlchemy."""

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    """Base declarativa para todos los modelos SQLAlchemy.

    Los modelos heredan de esta clase y se registran automáticamente
    en Base.metadata para que Alembic los detecte con --autogenerate.
    """

# Motor SQLite con WAL mode para acceso concurrente.
# Sin WAL mode, lecturas y escrituras simultáneas causan SQLITE_BUSY.
# check_same_thread=False necesario porque FastAPI corre en múltiples hilos.
engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _optimize_sqlite(dbapi_connection: object, connection_record: object) -> None:
    """Activa WAL mode y PRAGMAs de performance al conectar.

    PRAGMAs aplicados:
    - journal_mode=WAL: escritura concurrente sin bloquear lectores
    - synchronous=NORMAL: seguro con WAL, duplica throughput de escritura
    - busy_timeout=5000: espera 5s en vez de fallar con SQLITE_BUSY
    - cache_size=-8000: 8 MB de cache de páginas (-8k * 1024 bytes)
    - temp_store=MEMORY: tablas temporales en RAM (~10x más rápido)
    - foreign_keys=ON: integridad referencial

    Los parámetros se tipan como object porque SQLAlchemy expone conexiones
    DBAPI genéricas (sqlite3.Connection, psycopg2.connection, etc.) sin un
    tipo común que exponga cursor(). Usamos type: ignore en vez de Any
    para cumplir la convención del proyecto.
    """
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA cache_size=-8000")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# Session factory síncrona (MVP no usa async SQLAlchemy)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Dependencia FastAPI: inyecta sesión de base de datos por request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
