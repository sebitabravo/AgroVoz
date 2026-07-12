"""Tests para los endpoints admin de user_prefs (onboarding, #86).

Cubre:
- Auth: 401 sin key, 401 key invalido, 200 con key.
- PUT /comuna: crea user_prefs nueva, actualiza existente (upsert).
- GET /{phone_hash}: retorna prefs, 404 si no existe.
- Validacion: phone_hash invalido → 422, comuna vacia → 422.
- Privacidad: respuesta solo contiene phone_hash (no numero en claro).
"""

from collections.abc import Generator
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.user_prefs import UserPrefs

_ADMIN_HEADERS = {"X-Admin-Key": settings.admin_api_key}

# Phone_hash valido de ejemplo (64 hex lowercase).
_VALID_HASH = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
_INVALID_HASH = "no-es-un-hash-valido"


def _session_test_db(tmp_path: Path) -> Generator[Session, None, None]:
    """Sesion SQLite temporal con tablas creadas desde los modelos.

    Usa el mismo nombre de archivo que el client fixture (test_agrovoz.db)
    para que los datos escritos aqui sean visibles para los endpoints.
    """
    db_path = tmp_path / "test_agrovoz.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    from app.core.database import Base
    from app.models import Consultation, OdepaPrice, UserPrefs  # noqa: F401

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ── Auth ───────────────────────────────────────────────────────────


class TestUserAdminAuth:
    """Autenticacion con X-Admin-Key."""

    async def test_sin_key_retorna_401(self, client: AsyncClient) -> None:
        resp = await client.get(f"/api/v1/admin/users/{_VALID_HASH}")
        assert resp.status_code == 401

    async def test_key_invalido_retorna_401(self, client: AsyncClient) -> None:
        resp = await client.get(f"/api/v1/admin/users/{_VALID_HASH}", headers={"X-Admin-Key": "x"})
        assert resp.status_code == 401


# ── PUT /admin/users/{phone_hash}/comuna ────────────────────────────


class TestSetComuna:
    """Upsert de comuna para un phone_hash."""

    async def test_crea_user_prefs_nueva(self, client: AsyncClient) -> None:
        """PUT en phone_hash sin prefs crea una fila nueva con la comuna."""
        resp = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={"comuna": "Traiguén"},
            headers=_ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["phone_hash"] == _VALID_HASH
        assert data["comuna"] == "Traiguén"
        assert data["created_at"] is not None

    async def test_actualiza_comuna_existente(self, client: AsyncClient, tmp_path: Path) -> None:
        """PUT en phone_hash con prefs existentes actualiza la comuna."""
        # Crear user_prefs directamente en la DB temporal del test.
        with next(_session_test_db(tmp_path)) as db:
            db.add(UserPrefs(phone_hash=_VALID_HASH, comuna="Temuco"))
            db.commit()

        resp = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={"comuna": "Traiguén"},
            headers=_ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["comuna"] == "Traiguén"
        # phone_hash no cambia.
        assert data["phone_hash"] == _VALID_HASH

    async def test_idempotente_mismo_put_dos_veces(self, client: AsyncClient) -> None:
        """Dos PUTs con la misma comuna no crean duplicados."""
        resp1 = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={"comuna": "Traiguén"},
            headers=_ADMIN_HEADERS,
        )
        assert resp1.status_code == 200

        resp2 = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={"comuna": "Traiguén"},
            headers=_ADMIN_HEADERS,
        )
        assert resp2.status_code == 200
        # Ambas respuestas tienen el mismo phone_hash (no duplicado).
        assert resp1.json()["phone_hash"] == resp2.json()["phone_hash"]

    async def test_phone_hash_invalido_retorna_422(self, client: AsyncClient) -> None:
        """phone_hash que no es 64 hex lowercase retorna 422."""
        resp = await client.put(
            f"/api/v1/admin/users/{_INVALID_HASH}/comuna",
            json={"comuna": "Traiguén"},
            headers=_ADMIN_HEADERS,
        )
        assert resp.status_code == 422

    async def test_comuna_vacia_retorna_422(self, client: AsyncClient) -> None:
        """comuna vacia no pasa validacion de Pydantic (min_length=1)."""
        resp = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={"comuna": ""},
            headers=_ADMIN_HEADERS,
        )
        assert resp.status_code == 422

    async def test_comuna_con_espacios_se_strippeda(self, client: AsyncClient) -> None:
        """comuna con espacios al final se limpia (strip)."""
        resp = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={"comuna": "  Traiguén  "},
            headers=_ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["comuna"] == "Traiguén"

    async def test_comuna_solo_espacios_retorna_422(self, client: AsyncClient) -> None:
        """comuna con solo espacios pasa Pydantic pero falla post-strip (fix #105)."""
        resp = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={"comuna": "   "},
            headers=_ADMIN_HEADERS,
        )
        assert resp.status_code == 422


# ── GET /admin/users/{phone_hash} ───────────────────────────────────


class TestGetUserPrefs:
    """Obtiene preferencias de un productor."""

    async def test_retorna_prefs_existente(self, client: AsyncClient, tmp_path: Path) -> None:
        """GET en phone_hash con prefs retorna los datos."""
        with next(_session_test_db(tmp_path)) as db:
            db.add(UserPrefs(phone_hash=_VALID_HASH, comuna="Traiguén"))
            db.commit()

        resp = await client.get(f"/api/v1/admin/users/{_VALID_HASH}", headers=_ADMIN_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["phone_hash"] == _VALID_HASH
        assert data["comuna"] == "Traiguén"

    async def test_no_existe_retorna_404(self, client: AsyncClient) -> None:
        """GET en phone_hash sin prefs retorna 404."""
        resp = await client.get(f"/api/v1/admin/users/{_VALID_HASH}", headers=_ADMIN_HEADERS)
        assert resp.status_code == 404

    async def test_phone_hash_invalido_retorna_422(self, client: AsyncClient) -> None:
        """phone_hash invalido retorna 422 (no 404)."""
        resp = await client.get(f"/api/v1/admin/users/{_INVALID_HASH}", headers=_ADMIN_HEADERS)
        assert resp.status_code == 422


# ── Privacidad ─────────────────────────────────────────────────────


class TestPrivacidad:
    """La respuesta solo contiene phone_hash, no el numero en claro."""

    async def test_respuesta_no_contiene_numero_claro(self, client: AsyncClient) -> None:
        """La respuesta PUT no expone datos personales del productor."""
        resp = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={"comuna": "Traiguén"},
            headers=_ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Solo phone_hash (64 hex), created_at, comuna. Sin campos extra.
        assert set(data.keys()) == {"phone_hash", "comuna", "created_at"}
        # phone_hash es exactamente 64 chars hex.
        assert len(data["phone_hash"]) == 64
        assert all(c in "0123456789abcdef" for c in data["phone_hash"])

    async def test_phone_hash_en_path_no_se_refleja_en_claro(self, client: AsyncClient) -> None:
        """El path param es el hash, no el numero. La respuesta lo devuelve igual."""
        resp = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={"comuna": "Curacautín"},
            headers=_ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Lo que va en el path es lo mismo que viene en la respuesta.
        assert data["phone_hash"] == _VALID_HASH
