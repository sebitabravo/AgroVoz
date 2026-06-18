"""Fixtures compartidas para tests del backend."""

from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


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
    test_session_local = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_db() -> Generator[Session, None, None]:
        db = test_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[original_get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
