"""Fixtures compartidas para tests del backend."""

from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Forzar construccion del middleware stack al importar conftest para que
# RateLimitMiddleware.__init__ registre su instancia en _active_rate_limiter.
# Sin esto, el primer reset_rate_limiter_for_tests() seria no-op (None).
from app.main import app as _app

_ = _app.middleware_stack


@pytest.fixture
def db(tmp_path: Path) -> Generator[Session, None, None]:
    """Engine SQLite temporal con tablas creadas desde los modelos.

    Cada test recibe una DB fresh en un archivo temporal distinto.
    Los modelos se importan dentro de la fixture para garantizar que
    Base.metadata los incluya antes de create_all().
    """
    from app.core.database import Base
    from app.models import Consultation, OdepaPrice  # noqa: F401 — registra modelos en Base.metadata

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


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncGenerator[AsyncClient, None]:
    """Cliente HTTP asíncrono con transporte ASGI para tests sin red.

    Aisla la base de datos vía dependency_overrides: cada test usa
    una DB SQLite temporal en vez de data/agrovoz.db.
    """
    # Import local: el engine de app.core.database ya se creó al importar
    # app.main, pero podemos sobreescribir la dependencia get_db para
    # que cualquier endpoint que toque la DB use un engine temporal.
    from app.core.database import get_db as original_get_db
    from app.main import app

    db_path = tmp_path / "test_agrovoz.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    # Crear tablas para que los endpoints que tocan DB funcionen en tests.
    from app.core.database import Base
    from app.models import Consultation, OdepaPrice  # noqa: F401

    Base.metadata.create_all(test_engine)

    test_session_local = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_db() -> Generator[Session, None, None]:
        db = test_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[original_get_db] = override_get_db

    # _check_db() en health.py usa el engine global directamente.
    # Lo redirigimos al test_engine para aislamiento completo de tests.
    import app.api.health as health_module

    _original_health_engine = health_module.engine  # type: ignore[attr-defined]
    health_module.engine = test_engine  # type: ignore[attr-defined]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        try:
            yield c
        finally:
            # try/finally asegura que el estado se restaura incluso si el test falla.
            # Sin esto, una excepción durante el test dejaría health_module.engine
            # apuntando al test_engine (ya dispuesto) y dependency_overrides sucio,
            # contaminando los tests siguientes.
            health_module.engine = _original_health_engine  # type: ignore[attr-defined]
            test_engine.dispose()
            # Pop solo el override que creamos, sin limpiar otros que
            # otros test files hayan seteado (ej: test_weather_api.py
            # sobreescribe check_weather_rate_limit a nivel de modulo).
            app.dependency_overrides.pop(original_get_db, None)


@pytest.fixture(autouse=True)
def _reset_global_rate_limiter() -> Generator[None, None, None]:
    """Resetea el RateLimitMiddleware global antes y despues de cada test.

    El middleware vive en el singleton `app` y su estado `_requests` persiste
    entre tests. Sin reset, la suite completa satura el contador (60/min/IP,
    todos los tests comparten IP) y los tests que vienen despues reciben 429
    inesperado (ej: test_rate_limiter espera 30 x 200, test_weather_api).
    """
    from app.core.security import reset_rate_limiter_for_tests

    reset_rate_limiter_for_tests()
    yield
    reset_rate_limiter_for_tests()
