"""Tests para los endpoints admin de user_prefs (onboarding, #86).

Cubre:
- Auth: 401 sin key, 401 key invalido, 200 con key.
- PUT /comuna: crea user_prefs nueva, actualiza existente (upsert).
- GET /{phone_hash}: retorna prefs, 404 si no existe.
- Validacion: phone_hash invalido → 422, comuna vacia → 422.
- Privacidad: respuesta solo contiene phone_hash (no numero en claro).
"""

import logging
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.user_prefs import UserPrefs
from app.services.consultation_history_service import HistoryOperationError
from app.services.expense_service import ExpenseOperationError
from app.services.parcela_service import ParcelaOperationError

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
        assert data["identity_type"] == "individual"
        assert data["group_label"] is None
        assert data["localidad"] is None
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


# ── Identidad grupal PRODESAL (#173) ──────────────────────────────


class TestProdesalIdentity:
    """Upsert seguro de identidades compartidas por grupos PRODESAL."""

    async def test_crea_grupo_con_codigo_y_localidad(
        self,
        client: AsyncClient,
    ) -> None:
        """Crear un grupo exige código operativo y normaliza localidad."""
        resp = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={
                "comuna": "Traiguén",
                "identity_type": "prodesal_group",
                "group_label": "prodesal-traiguen-norte",
                "localidad": "  Quilquén  ",
            },
            headers=_ADMIN_HEADERS,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["identity_type"] == "prodesal_group"
        assert data["group_label"] == "prodesal-traiguen-norte"
        assert data["localidad"] == "Quilquén"

    async def test_actualiza_codigo_de_grupo_sin_repetir_tipo(
        self,
        client: AsyncClient,
    ) -> None:
        """Una etiqueta presente actualiza un grupo existente."""
        created = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={
                "comuna": "Traiguén",
                "identity_type": "prodesal_group",
                "group_label": "prodesal-traiguen-norte",
            },
            headers=_ADMIN_HEADERS,
        )
        assert created.status_code == 200

        updated = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={
                "comuna": "Traiguén",
                "group_label": "prodesal-traiguen-sur",
                "localidad": "El Maitén",
            },
            headers=_ADMIN_HEADERS,
        )

        assert updated.status_code == 200
        assert updated.json()["identity_type"] == "prodesal_group"
        assert updated.json()["group_label"] == "prodesal-traiguen-sur"
        assert updated.json()["localidad"] == "El Maitén"

    async def test_omitir_campos_grupales_conserva_valores(
        self,
        client: AsyncClient,
    ) -> None:
        """Actualizar datos legacy no pisa identidad, etiqueta ni localidad."""
        created = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={
                "comuna": "Traiguén",
                "identity_type": "prodesal_group",
                "group_label": "prodesal-traiguen-norte",
                "localidad": "Quilquén",
            },
            headers=_ADMIN_HEADERS,
        )
        assert created.status_code == 200

        updated = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={"comuna": "Temuco"},
            headers=_ADMIN_HEADERS,
        )

        assert updated.status_code == 200
        data = updated.json()
        assert data["identity_type"] == "prodesal_group"
        assert data["group_label"] == "prodesal-traiguen-norte"
        assert data["localidad"] == "Quilquén"

    async def test_cambiar_a_individual_limpia_solo_etiqueta(
        self,
        client: AsyncClient,
    ) -> None:
        """La transición a individual limpia la etiqueta y conserva localidad."""
        created = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={
                "comuna": "Traiguén",
                "identity_type": "prodesal_group",
                "group_label": "prodesal-traiguen-norte",
                "localidad": "Quilquén",
            },
            headers=_ADMIN_HEADERS,
        )
        assert created.status_code == 200

        updated = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={"comuna": "Traiguén", "identity_type": "individual"},
            headers=_ADMIN_HEADERS,
        )

        assert updated.status_code == 200
        data = updated.json()
        assert data["identity_type"] == "individual"
        assert data["group_label"] is None
        assert data["localidad"] == "Quilquén"

    @pytest.mark.parametrize(
        "payload",
        [
            {"identity_type": "prodesal_group"},
            {
                "identity_type": "individual",
                "group_label": "prodesal-traiguen-norte",
            },
            {"group_label": "prodesal-traiguen-norte"},
            {"identity_type": None},
            {
                "identity_type": "prodesal_group",
                "group_label": "Prodesal-Traiguen",
            },
            {
                "identity_type": "prodesal_group",
                "group_label": "prodesal-grupo norte",
            },
            {
                "identity_type": "prodesal_group",
                "group_label": "prodesal-../norte",
            },
            {
                "identity_type": "prodesal_group",
                "group_label": "prodesal-56912345678",
            },
            {"localidad": "   "},
            {"localidad": "x" * 121},
        ],
    )
    async def test_rechaza_payloads_grupales_inseguros(
        self,
        client: AsyncClient,
        payload: dict[str, object],
    ) -> None:
        """Rechaza estados incoherentes, texto libre, teléfonos y traversal."""
        resp = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={"comuna": "Traiguén", **payload},
            headers=_ADMIN_HEADERS,
        )

        assert resp.status_code == 422

    async def test_auth_bloquea_creacion_de_grupo(
        self,
        client: AsyncClient,
    ) -> None:
        """La identidad grupal no evita la dependencia X-Admin-Key."""
        resp = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={
                "comuna": "Traiguén",
                "identity_type": "prodesal_group",
                "group_label": "prodesal-traiguen-norte",
            },
        )

        assert resp.status_code == 401

    async def test_logs_no_exponen_identidad_operativa(
        self,
        client: AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Los logs propios omiten hash, etiqueta y localidad."""
        caplog.set_level(logging.INFO, logger="app.api.admin.user_admin")

        resp = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={
                "comuna": "Traiguén",
                "identity_type": "prodesal_group",
                "group_label": "prodesal-traiguen-norte",
                "localidad": "Quilquén",
            },
            headers=_ADMIN_HEADERS,
        )

        assert resp.status_code == 200
        own_logs = "\n".join(
            record.getMessage() for record in caplog.records if record.name == "app.api.admin.user_admin"
        )
        assert _VALID_HASH not in own_logs
        assert "prodesal-traiguen-norte" not in own_logs
        assert "Quilquén" not in own_logs


# ── dataset_consent (#96) ──────────────────────────────────────────


class TestDatasetConsent:
    """Upsert del flag dataset_consent para retención de voz rural."""

    async def test_set_consent_en_creacion(self, client: AsyncClient) -> None:
        """Se puede crear UserPrefs con comuna y consentimiento."""
        resp = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={"comuna": "Traiguén", "dataset_consent": True},
            headers=_ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dataset_consent"] is True

    async def test_set_consent_actualiza_existente(self, client: AsyncClient, tmp_path: Path) -> None:
        """Se puede actualizar solo el consentimiento de un UserPrefs existente."""
        with next(_session_test_db(tmp_path)) as db:
            db.add(UserPrefs(phone_hash=_VALID_HASH, comuna="Traiguén", dataset_consent=False))
            db.commit()

        resp = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={"comuna": "Traiguén", "dataset_consent": True},
            headers=_ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["dataset_consent"] is True

    async def test_omite_consent_si_no_se_envia(self, client: AsyncClient, tmp_path: Path) -> None:
        """Si no se envia dataset_consent, no se modifica el valor actual."""
        with next(_session_test_db(tmp_path)) as db:
            db.add(UserPrefs(phone_hash=_VALID_HASH, comuna="Traiguén", dataset_consent=True))
            db.commit()

        resp = await client.put(
            f"/api/v1/admin/users/{_VALID_HASH}/comuna",
            json={"comuna": "Temuco"},
            headers=_ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["comuna"] == "Temuco"
        assert data["dataset_consent"] is True

    async def test_revocar_dataset_no_borra_historial(
        self,
        client: AsyncClient,
    ) -> None:
        """El opt-out del dataset de voz no afecta el propósito historial."""
        with (
            patch.object(settings, "consultation_history_enabled", True),
            patch("app.api.admin.user_admin.delete_history") as delete_mock,
            patch("app.api.admin.user_admin.purge_dataset_for_subject") as purge_mock,
        ):
            resp = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén", "dataset_consent": False},
                headers=_ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        assert resp.json()["dataset_consent"] is False
        delete_mock.assert_not_called()
        purge_mock.assert_called_once_with(_VALID_HASH, event_id=None)


class TestHistoryConsent:
    """Upsert y revocación del consentimiento específico de historial."""

    async def test_revocacion_confirma_db_antes_de_borrar_historial(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """El servicio observa el consentimiento ya revocado en SQLite."""
        with next(_session_test_db(tmp_path)) as db:
            db.add(
                UserPrefs(
                    phone_hash=_VALID_HASH,
                    comuna="Traiguén",
                    history_consent=True,
                )
            )
            db.commit()

        def assert_revoked_before_cleanup(
            phone_hash: str,
            *,
            reason: str,
            requested_via: str,
        ) -> None:
            assert phone_hash == _VALID_HASH
            assert reason == "consent_revoked"
            assert requested_via == "admin_api"
            with next(_session_test_db(tmp_path)) as verification_db:
                prefs = verification_db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == _VALID_HASH))
                assert prefs is not None
                assert prefs.history_consent is False

        with (
            patch.object(settings, "consultation_history_enabled", True),
            patch(
                "app.api.admin.user_admin.delete_history",
                side_effect=assert_revoked_before_cleanup,
            ) as delete_mock,
        ):
            resp = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén", "history_consent": False},
                headers=_ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        assert resp.json()["history_consent"] is False
        delete_mock.assert_called_once_with(
            _VALID_HASH,
            reason="consent_revoked",
            requested_via="admin_api",
        )

    async def test_false_explicito_reintenta_limpieza_si_ya_estaba_revocado(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """Un retry idempotente vuelve a intentar una limpieza anterior."""
        with next(_session_test_db(tmp_path)) as db:
            db.add(
                UserPrefs(
                    phone_hash=_VALID_HASH,
                    comuna="Traiguén",
                    history_consent=False,
                )
            )
            db.commit()

        with (
            patch.object(settings, "consultation_history_enabled", True),
            patch("app.api.admin.user_admin.delete_history") as delete_mock,
        ):
            resp = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén", "history_consent": False},
                headers=_ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        delete_mock.assert_called_once_with(
            _VALID_HASH,
            reason="consent_revoked",
            requested_via="admin_api",
        )

    async def test_omitir_consentimiento_no_borra_historial(
        self,
        client: AsyncClient,
    ) -> None:
        """La ausencia del campo no se interpreta como revocación."""
        with (
            patch.object(settings, "consultation_history_enabled", True),
            patch("app.api.admin.user_admin.delete_history") as delete_mock,
        ):
            resp = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén"},
                headers=_ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        delete_mock.assert_not_called()

    async def test_gate_apagado_no_invoca_borrado(
        self,
        client: AsyncClient,
    ) -> None:
        """El historial deshabilitado conserva el comportamiento previo."""
        with (
            patch.object(settings, "consultation_history_enabled", False),
            patch("app.api.admin.user_admin.delete_history") as delete_mock,
        ):
            resp = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén", "history_consent": False},
                headers=_ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        assert resp.json()["history_consent"] is False
        delete_mock.assert_not_called()

    async def test_fallo_de_limpieza_conserva_revocacion_y_respuesta_estable(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """Un fallo parcial no reactiva consentimiento ni filtra detalles."""
        with next(_session_test_db(tmp_path)) as db:
            db.add(
                UserPrefs(
                    phone_hash=_VALID_HASH,
                    comuna="Traiguén",
                    history_consent=True,
                )
            )
            db.commit()

        private_error = "detalle-interno-con-datos-sensibles"
        with (
            patch.object(settings, "consultation_history_enabled", True),
            patch(
                "app.api.admin.user_admin.delete_history",
                side_effect=HistoryOperationError(private_error),
            ),
        ):
            resp = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén", "history_consent": False},
                headers=_ADMIN_HEADERS,
            )

        assert resp.status_code == 503
        assert resp.json() == {
            "detail": ("Consentimiento revocado; limpieza de historial pendiente. Reintente la solicitud.")
        }
        assert _VALID_HASH not in resp.text
        assert private_error not in resp.text
        with next(_session_test_db(tmp_path)) as verification_db:
            prefs = verification_db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == _VALID_HASH))
            assert prefs is not None
            assert prefs.history_consent is False

    async def test_retry_completa_limpieza_despues_de_fallo_parcial(
        self,
        client: AsyncClient,
    ) -> None:
        """Repetir false permite recuperar la limpieza sin reactivar datos."""
        with (
            patch.object(settings, "consultation_history_enabled", True),
            patch(
                "app.api.admin.user_admin.delete_history",
                side_effect=[
                    HistoryOperationError("fallo transitorio"),
                    None,
                ],
            ) as delete_mock,
        ):
            first = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén", "history_consent": False},
                headers=_ADMIN_HEADERS,
            )
            retry = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén", "history_consent": False},
                headers=_ADMIN_HEADERS,
            )

        assert first.status_code == 503
        assert retry.status_code == 200
        assert retry.json()["history_consent"] is False
        assert delete_mock.call_count == 2

    async def test_revocacion_sin_auth_no_invoca_limpieza(
        self,
        client: AsyncClient,
    ) -> None:
        """La dependencia de auth bloquea antes de tocar prefs o historial."""
        with (
            patch.object(settings, "consultation_history_enabled", True),
            patch("app.api.admin.user_admin.delete_history") as delete_mock,
        ):
            resp = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén", "history_consent": False},
            )

        assert resp.status_code == 401
        delete_mock.assert_not_called()


class TestExpenseConsent:
    """Upsert y revocación del consentimiento de gastos declarados (#170)."""

    async def test_otorgar_consentimiento_persiste_y_no_borra(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """Conceder el opt-in nunca dispara limpieza."""
        with patch("app.api.admin.user_admin.delete_expenses_for_subject") as delete_mock:
            resp = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén", "expense_consent": True},
                headers=_ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        assert resp.json()["expense_consent"] is True
        delete_mock.assert_not_called()
        with next(_session_test_db(tmp_path)) as db:
            prefs = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == _VALID_HASH))
            assert prefs is not None
            assert prefs.expense_consent is True

    async def test_revocacion_borra_gastos_del_sujeto(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """El servicio observa el consentimiento ya revocado en SQLite."""
        with next(_session_test_db(tmp_path)) as db:
            db.add(
                UserPrefs(
                    phone_hash=_VALID_HASH,
                    comuna="Traiguén",
                    expense_consent=True,
                )
            )
            db.commit()

        def assert_revoked_before_cleanup(phone_hash: str) -> int:
            assert phone_hash == _VALID_HASH
            with next(_session_test_db(tmp_path)) as verification_db:
                prefs = verification_db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == _VALID_HASH))
                assert prefs is not None
                assert prefs.expense_consent is False
            return 0

        with patch(
            "app.api.admin.user_admin.delete_expenses_for_subject",
            side_effect=assert_revoked_before_cleanup,
        ) as delete_mock:
            resp = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén", "expense_consent": False},
                headers=_ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        assert resp.json()["expense_consent"] is False
        delete_mock.assert_called_once_with(_VALID_HASH)

    async def test_revocacion_borra_aunque_el_gate_este_apagado(
        self,
        client: AsyncClient,
    ) -> None:
        """Apagar las escrituras nuevas no exime de borrar lo ya registrado."""
        with (
            patch.object(settings, "expense_tracking_enabled", False),
            patch("app.api.admin.user_admin.delete_expenses_for_subject") as delete_mock,
        ):
            resp = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén", "expense_consent": False},
                headers=_ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        delete_mock.assert_called_once_with(_VALID_HASH)

    async def test_omitir_consentimiento_no_borra_gastos(
        self,
        client: AsyncClient,
    ) -> None:
        """La ausencia del campo no se interpreta como revocación."""
        with patch("app.api.admin.user_admin.delete_expenses_for_subject") as delete_mock:
            resp = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén"},
                headers=_ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        delete_mock.assert_not_called()

    async def test_fallo_de_limpieza_conserva_revocacion_y_respuesta_estable(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """Un fallo parcial no reactiva consentimiento ni filtra detalles."""
        with next(_session_test_db(tmp_path)) as db:
            db.add(
                UserPrefs(
                    phone_hash=_VALID_HASH,
                    comuna="Traiguén",
                    expense_consent=True,
                )
            )
            db.commit()

        private_error = "detalle-interno-con-datos-sensibles"
        with patch(
            "app.api.admin.user_admin.delete_expenses_for_subject",
            side_effect=ExpenseOperationError(private_error),
        ):
            resp = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén", "expense_consent": False},
                headers=_ADMIN_HEADERS,
            )

        assert resp.status_code == 503
        assert resp.json() == {
            "detail": ("Consentimiento revocado; limpieza de gastos pendiente. Reintente la solicitud.")
        }
        assert _VALID_HASH not in resp.text
        assert private_error not in resp.text
        with next(_session_test_db(tmp_path)) as verification_db:
            prefs = verification_db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == _VALID_HASH))
            assert prefs is not None
            assert prefs.expense_consent is False


class TestParcelaConsent:
    """Upsert y revocación del consentimiento de parcelas (C5)."""

    async def test_otorgar_consentimiento_persiste_y_no_borra(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """Conceder el opt-in nunca dispara limpieza."""
        with patch("app.api.admin.user_admin.delete_parcelas_for_subject") as delete_mock:
            resp = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén", "parcela_consent": True},
                headers=_ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        assert resp.json()["parcela_consent"] is True
        delete_mock.assert_not_called()
        with next(_session_test_db(tmp_path)) as db:
            prefs = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == _VALID_HASH))
            assert prefs is not None
            assert prefs.parcela_consent is True

    async def test_revocacion_borra_parcelas_del_sujeto(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """El servicio observa el consentimiento ya revocado en SQLite."""
        with next(_session_test_db(tmp_path)) as db:
            db.add(
                UserPrefs(
                    phone_hash=_VALID_HASH,
                    comuna="Traiguén",
                    parcela_consent=True,
                )
            )
            db.commit()

        def assert_revoked_before_cleanup(phone_hash: str) -> int:
            assert phone_hash == _VALID_HASH
            with next(_session_test_db(tmp_path)) as verification_db:
                prefs = verification_db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == _VALID_HASH))
                assert prefs is not None
                assert prefs.parcela_consent is False
            return 0

        with patch(
            "app.api.admin.user_admin.delete_parcelas_for_subject",
            side_effect=assert_revoked_before_cleanup,
        ) as delete_mock:
            resp = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén", "parcela_consent": False},
                headers=_ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        assert resp.json()["parcela_consent"] is False
        delete_mock.assert_called_once_with(_VALID_HASH)

    async def test_revocacion_borra_aunque_el_gate_este_apagado(
        self,
        client: AsyncClient,
    ) -> None:
        """Apagar las escrituras nuevas no exime de borrar lo ya registrado."""
        with (
            patch.object(settings, "parcela_tracking_enabled", False),
            patch("app.api.admin.user_admin.delete_parcelas_for_subject") as delete_mock,
        ):
            resp = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén", "parcela_consent": False},
                headers=_ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        delete_mock.assert_called_once_with(_VALID_HASH)

    async def test_omitir_consentimiento_no_borra_parcelas(
        self,
        client: AsyncClient,
    ) -> None:
        """La ausencia del campo no se interpreta como revocación."""
        with patch("app.api.admin.user_admin.delete_parcelas_for_subject") as delete_mock:
            resp = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén"},
                headers=_ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        delete_mock.assert_not_called()

    async def test_fallo_de_limpieza_conserva_revocacion_y_respuesta_estable(
        self,
        client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """Un fallo parcial no reactiva consentimiento ni filtra detalles."""
        with next(_session_test_db(tmp_path)) as db:
            db.add(
                UserPrefs(
                    phone_hash=_VALID_HASH,
                    comuna="Traiguén",
                    parcela_consent=True,
                )
            )
            db.commit()

        private_error = "detalle-interno-con-datos-sensibles"
        with patch(
            "app.api.admin.user_admin.delete_parcelas_for_subject",
            side_effect=ParcelaOperationError(private_error),
        ):
            resp = await client.put(
                f"/api/v1/admin/users/{_VALID_HASH}/comuna",
                json={"comuna": "Traiguén", "parcela_consent": False},
                headers=_ADMIN_HEADERS,
            )

        assert resp.status_code == 503
        assert resp.json() == {
            "detail": ("Consentimiento revocado; limpieza de parcelas pendiente. Reintente la solicitud.")
        }
        assert _VALID_HASH not in resp.text
        assert private_error not in resp.text
        with next(_session_test_db(tmp_path)) as verification_db:
            prefs = verification_db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == _VALID_HASH))
            assert prefs is not None
            assert prefs.parcela_consent is False


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
        assert data["identity_type"] == "individual"
        assert data["group_label"] is None
        assert data["localidad"] is None
        assert data["history_consent"] is False
        assert data["alert_consent"] is False

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
        # Solo preferencias declaradas; nunca el número en claro.
        assert set(data.keys()) == {
            "phone_hash",
            "comuna",
            "dataset_consent",
            "history_consent",
            "alert_consent",
            "expense_consent",
            "parcela_consent",
            "location_consent",
            "identity_type",
            "group_label",
            "localidad",
            "created_at",
            "cultivos",
        }
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
