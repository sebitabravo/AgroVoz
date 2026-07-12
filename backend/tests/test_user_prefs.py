"""Tests para app.models.user_prefs: modelo UserPrefs (#86).

Cubre: creacion, constraints (unique phone_hash, comuna nullable),
repr y created_at con server default.

Usa la fixture db (SQLite temporal con create_all desde los modelos).
"""

import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.user_prefs import UserPrefs


class TestUserPrefsModel:
    """Modelo UserPrefs: preferencias de productor por phone_hash."""

    def test_crear_user_prefs_con_comuna(self, db) -> None:  # type: ignore[no-untyped-def]
        """Crear UserPrefs con phone_hash y comuna persiste correctamente."""
        prefs = UserPrefs(
            phone_hash="a" * 64,
            comuna="Traiguén",
        )
        db.add(prefs)
        db.commit()

        result = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == "a" * 64))
        assert result is not None
        assert result.comuna == "Traiguén"

    def test_crear_user_prefs_sin_comuna(self, db) -> None:  # type: ignore[no-untyped-def]
        """comuna es nullable: se puede crear sin comuna (onboarding pendiente)."""
        prefs = UserPrefs(phone_hash="b" * 64)
        db.add(prefs)
        db.commit()

        result = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == "b" * 64))
        assert result is not None
        assert result.comuna is None

    def test_phone_hash_unique_duplicado_lanza_error(self, db) -> None:  # type: ignore[no-untyped-def]
        """No pueden existir dos UserPrefs con el mismo phone_hash."""
        prefs1 = UserPrefs(phone_hash="c" * 64, comuna="Temuco")
        db.add(prefs1)
        db.commit()

        prefs2 = UserPrefs(phone_hash="c" * 64, comuna="Traiguén")
        db.add(prefs2)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_phone_hashes_distintos_se_persisten(self, db) -> None:  # type: ignore[no-untyped-def]
        """Dos phone_hashes distintos pueden coexistir."""
        db.add(UserPrefs(phone_hash="d" * 64, comuna="Temuco"))
        db.add(UserPrefs(phone_hash="e" * 64, comuna="Traiguén"))
        db.commit()

        all_prefs = db.scalars(select(UserPrefs)).all()
        assert len(all_prefs) == 2

    def test_created_at_tiene_server_default(self, db) -> None:  # type: ignore[no-untyped-def]
        """created_at se setea automaticamente por server_default=func.now()."""
        prefs = UserPrefs(phone_hash="f" * 64, comuna="Curacautín")
        db.add(prefs)
        db.commit()
        db.refresh(prefs)

        assert prefs.created_at is not None
        # Verificar que es un datetime (no string, no None).
        assert isinstance(prefs.created_at, datetime.datetime)

    def test_repr_muestra_phone_hash_truncado_y_comuna(self) -> None:
        """__repr__ no leakea el phone_hash completo y muestra la comuna."""
        prefs = UserPrefs(phone_hash="0123456789abcdef" * 4, comuna="Traiguén")
        repr_str = repr(prefs)
        assert "phone_hash='01234567..." in repr_str
        assert "comuna='Traiguén'" in repr_str

    def test_repr_sin_comuna(self) -> None:
        """__repr__ muestra 'sin_comuna' cuando comuna es None."""
        prefs = UserPrefs(phone_hash="0123456789abcdef" * 4)
        repr_str = repr(prefs)
        assert "comuna='sin_comuna'" in repr_str
