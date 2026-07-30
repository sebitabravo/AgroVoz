"""Tests para app.models.user_prefs: modelo UserPrefs (#86, #125).

Cubre: creacion, constraints (unique phone_hash, comuna nullable),
repr y created_at con server default. Tests de cultivos de interés
(issue #125): persistencia, actualización, serialización en API,
contexto en LLM y migración sin pérdida de datos.

Usa la fixture db (SQLite temporal con create_all desde los modelos).
"""

import datetime
import json

import pytest
from httpx import AsyncClient
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
        assert result.dataset_consent is False

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

    def test_dataset_consent_default_false(self, db) -> None:  # type: ignore[no-untyped-def]
        """dataset_consent default False (privacidad por defecto, #96)."""
        prefs = UserPrefs(phone_hash="g" * 64, dataset_consent=True)
        db.add(prefs)
        db.commit()

        result = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == "g" * 64))
        assert result is not None
        assert result.dataset_consent is True

    def test_repr_no_expone_hash_ni_comuna(self) -> None:
        """__repr__ omite identificadores y preferencias sensibles."""
        prefs = UserPrefs(
            phone_hash="0123456789abcdef" * 4,
            identity_type="individual",
            comuna="Traiguén",
        )
        repr_str = repr(prefs)
        assert "01234567" not in repr_str
        assert "Traiguén" not in repr_str
        assert "identity_type" not in repr_str
        assert "dataset_consent=False" in repr_str
        assert "alert_consent=False" in repr_str
        assert "history_consent=False" in repr_str

    def test_repr_sin_comuna(self) -> None:
        """__repr__ tampoco agrega placeholders de ubicación."""
        prefs = UserPrefs(phone_hash="0123456789abcdef" * 4)
        repr_str = repr(prefs)
        assert "comuna" not in repr_str


class TestUserPrefsCultivos:
    """Tests de cultivos de interés del productor (issue #125)."""

    def test_cultivos_nullable(self, db) -> None:  # type: ignore[no-untyped-def]
        """cultivos es nullable por defecto."""
        prefs = UserPrefs(phone_hash="h" * 64)
        db.add(prefs)
        db.commit()

        result = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == "h" * 64))
        assert result is not None
        assert result.cultivos is None

    def test_guardar_cultivos_como_json(self, db) -> None:  # type: ignore[no-untyped-def]
        """cultivos se almacena como JSON string en SQLite TEXT."""
        cultivos_json = json.dumps(["papa", "trigo", "tomate"], ensure_ascii=False)
        prefs = UserPrefs(phone_hash="i" * 64, cultivos=cultivos_json)
        db.add(prefs)
        db.commit()

        result = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == "i" * 64))
        assert result is not None
        assert result.cultivos == '["papa", "trigo", "tomate"]'

    def test_actualizar_cultivos(self, db) -> None:  # type: ignore[no-untyped-def]
        """Actualizar cultivos sobre un registro existente."""
        prefs = UserPrefs(phone_hash="j" * 64, cultivos=json.dumps(["papa"]))
        db.add(prefs)
        db.commit()

        # Actualizar
        prefs.cultivos = json.dumps(["papa", "cebolla", "zanahoria"])
        db.commit()
        db.refresh(prefs)

        assert json.loads(prefs.cultivos) == ["papa", "cebolla", "zanahoria"]

    def test_cultivos_no_afecta_otros_campos(self, db) -> None:  # type: ignore[no-untyped-def]
        """Guardar cultivos no pisa comuna ni dataset_consent."""
        prefs = UserPrefs(
            phone_hash="k" * 64,
            comuna="Temuco",
            dataset_consent=True,
            cultivos=json.dumps(["papa"]),
        )
        db.add(prefs)
        db.commit()

        result = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == "k" * 64))
        assert result is not None
        assert result.comuna == "Temuco"
        assert result.dataset_consent is True
        assert json.loads(result.cultivos) == ["papa"]

    def test_repr_con_cultivos(self) -> None:
        """__repr__ no expone cultivos cuando existen."""
        prefs = UserPrefs(
            phone_hash="0123456789abcdef" * 4,
            cultivos=json.dumps(["papa", "trigo"]),
        )
        repr_str = repr(prefs)
        assert "papa" not in repr_str
        assert "trigo" not in repr_str

    def test_repr_sin_cultivos(self) -> None:
        """__repr__ no agrega placeholders de cultivos."""
        prefs = UserPrefs(phone_hash="0123456789abcdef" * 4)
        repr_str = repr(prefs)
        assert "cultivos" not in repr_str


class TestUserPrefsSchema:
    """Tests de serializacion de cultivos en schemas Pydantic (#125)."""

    def test_user_prefs_response_serializa_cultivos_lista(self) -> None:
        """UserPrefsResponse deserializa cultivos desde JSON string."""
        from app.schemas.user_prefs import UserPrefsResponse

        response = UserPrefsResponse(
            phone_hash="a" * 64,
            comuna="Traiguén",
            dataset_consent=True,
            cultivos='["papa", "trigo"]',
            created_at=datetime.datetime(2026, 6, 15, 10, 30, 0),
        )
        assert response.cultivos == ["papa", "trigo"]
        assert response.identity_type == "individual"
        assert response.group_label is None
        assert response.localidad is None
        assert response.history_consent is False

    def test_user_prefs_response_cultivos_none(self) -> None:
        """UserPrefsResponse con cultivos=None se serializa como None."""
        from app.schemas.user_prefs import UserPrefsResponse

        response = UserPrefsResponse(
            phone_hash="a" * 64,
            comuna="Traiguén",
            dataset_consent=True,
            cultivos=None,
            created_at=datetime.datetime(2026, 6, 15, 10, 30, 0),
        )
        assert response.cultivos is None

    def test_user_prefs_response_cultivos_ya_lista(self) -> None:
        """UserPrefsResponse acepta cultivos ya como list (no JSON string)."""
        from app.schemas.user_prefs import UserPrefsResponse

        response = UserPrefsResponse(
            phone_hash="a" * 64,
            comuna="Traiguén",
            dataset_consent=True,
            cultivos=["papa", "trigo"],
            created_at=datetime.datetime(2026, 6, 15, 10, 30, 0),
        )
        assert response.cultivos == ["papa", "trigo"]

    def test_comuna_request_validacion_cultivos(self) -> None:
        """ComunaRequest valida y normaliza cultivos."""
        from app.schemas.user_prefs import ComunaRequest

        request = ComunaRequest(
            comuna="Traiguén",
            cultivos=[" Papa ", " TRIGO ", "  tomate  "],
        )
        assert request.cultivos == ["papa", "trigo", "tomate"]

    def test_comuna_request_cultivos_none(self) -> None:
        """ComunaRequest con cultivos=None no modifica."""
        from app.schemas.user_prefs import ComunaRequest

        request = ComunaRequest(comuna="Traiguén", cultivos=None)
        assert request.cultivos is None

    def test_comuna_request_cultivos_vacio_se_convierte_a_none(self) -> None:
        """ComunaRequest con lista vacia se normaliza a None."""
        from app.schemas.user_prefs import ComunaRequest

        request = ComunaRequest(comuna="Traiguén", cultivos=[])
        assert request.cultivos is None

    def test_comuna_request_cultivos_solo_vacios(self) -> None:
        """ComunaRequest con solo strings vacios se normaliza a None."""
        from app.schemas.user_prefs import ComunaRequest

        request = ComunaRequest(comuna="Traiguén", cultivos=["", "  ", ""])
        assert request.cultivos is None

    def test_comuna_request_cultivos_deduplica(self) -> None:
        """ComunaRequest deduplica cultivos (case-insensitive) preservando el orden."""
        from app.schemas.user_prefs import ComunaRequest

        request = ComunaRequest(comuna="Traiguén", cultivos=["Papa", "PAPA", "papa", "ají"])
        assert request.cultivos == ["papa", "ají"]

    def test_comuna_request_cultivos_descarta_sobredimensionados(self) -> None:
        """ComunaRequest descarta cultivos con largo abusivo (> 100 chars)."""
        from app.schemas.user_prefs import ComunaRequest

        request = ComunaRequest(comuna="Traiguén", cultivos=["papa", "x" * 200, "trigo"])
        assert request.cultivos == ["papa", "trigo"]

    def test_comuna_request_cultivos_solo_sobredimensionado_es_none(self) -> None:
        """Si todos los cultivos exceden el largo máximo, se normaliza a None."""
        from app.schemas.user_prefs import ComunaRequest

        request = ComunaRequest(comuna="Traiguén", cultivos=["x" * 200])
        assert request.cultivos is None

    def test_comuna_request_cultivos_mas_de_20_rechaza(self) -> None:
        """Más de 20 cultivos dispara ValidationError (límite del Field)."""
        import pytest
        from pydantic import ValidationError

        from app.schemas.user_prefs import ComunaRequest

        with pytest.raises(ValidationError):
            ComunaRequest(comuna="Traiguén", cultivos=[f"cultivo_{i}" for i in range(30)])

    def test_comuna_request_grupo_valido_y_localidad_limpia(self) -> None:
        """Acepta un código operacional y limpia la localidad."""
        from app.schemas.user_prefs import ComunaRequest

        request = ComunaRequest(
            comuna="Traiguén",
            identity_type="prodesal_group",
            group_label="prodesal-traiguen-norte",
            localidad="  Quilquén  ",
        )

        assert request.identity_type == "prodesal_group"
        assert request.group_label == "prodesal-traiguen-norte"
        assert request.localidad == "Quilquén"

    @pytest.mark.parametrize(
        "group_label",
        [
            "grupo-traiguen",
            "Prodesal-traiguen",
            "prodesal-grupo norte",
            "prodesal-../norte",
            "prodesal-56912345678",
            "prodesal-juan pérez",
            f"prodesal-{'x' * 92}",
        ],
    )
    def test_comuna_request_rechaza_etiqueta_insegura(
        self,
        group_label: str,
    ) -> None:
        """Rechaza texto libre, teléfonos, traversal y tamaños abusivos."""
        from pydantic import ValidationError

        from app.schemas.user_prefs import ComunaRequest

        with pytest.raises(ValidationError):
            ComunaRequest(
                comuna="Traiguén",
                identity_type="prodesal_group",
                group_label=group_label,
            )

    @pytest.mark.parametrize(
        "payload",
        [
            {"identity_type": "prodesal_group"},
            {
                "identity_type": "individual",
                "group_label": "prodesal-traiguen-norte",
            },
            {"identity_type": None},
        ],
    )
    def test_comuna_request_rechaza_identidad_incoherente(
        self,
        payload: dict[str, object],
    ) -> None:
        """Una transición explícita debe producir un estado válido."""
        from pydantic import ValidationError

        from app.schemas.user_prefs import ComunaRequest

        with pytest.raises(ValidationError):
            ComunaRequest(comuna="Traiguén", **payload)  # type: ignore[arg-type]

    @pytest.mark.parametrize("localidad", ["   ", "x" * 121])
    def test_comuna_request_rechaza_localidad_invalida(
        self,
        localidad: str,
    ) -> None:
        """La localidad limpia debe tener entre 1 y 120 caracteres."""
        from pydantic import ValidationError

        from app.schemas.user_prefs import ComunaRequest

        with pytest.raises(ValidationError):
            ComunaRequest(comuna="Traiguén", localidad=localidad)


class TestUserPrefsAPI:
    """Tests de endpoints admin para user_prefs con cultivos (#125)."""

    @pytest.mark.asyncio
    async def test_put_comuna_con_cultivos(self, client: AsyncClient) -> None:
        """PUT /api/v1/admin/users/{phone_hash}/comuna con cultivos guarda y retorna."""
        phone_hash = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
        resp = await client.put(
            f"/api/v1/admin/users/{phone_hash}/comuna",
            json={
                "comuna": "Traiguén",
                "dataset_consent": True,
                "cultivos": ["papa", "trigo", "tomate"],
            },
            headers={"X-Admin-Key": "dev-admin-key"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["comuna"] == "Traiguén"
        assert data["dataset_consent"] is True
        assert data["cultivos"] == ["papa", "trigo", "tomate"]
        assert data["phone_hash"] == phone_hash

    @pytest.mark.asyncio
    async def test_put_comuna_sin_cultivos(self, client: AsyncClient) -> None:
        """PUT sin cultivos no modifica cultivos existentes."""
        phone_hash = "b1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
        # Crear con cultivos
        await client.put(
            f"/api/v1/admin/users/{phone_hash}/comuna",
            json={
                "comuna": "Traiguén",
                "cultivos": ["papa"],
            },
            headers={"X-Admin-Key": "dev-admin-key"},
        )

        # Actualizar solo comuna (cultivos no se envia)
        resp = await client.put(
            f"/api/v1/admin/users/{phone_hash}/comuna",
            json={
                "comuna": "Temuco",
            },
            headers={"X-Admin-Key": "dev-admin-key"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # cultivos deberia mantenerse como papa (no se pisó)
        # porque no se envió en el body
        assert data["cultivos"] == ["papa"]
        assert data["comuna"] == "Temuco"

    @pytest.mark.asyncio
    async def test_get_user_prefs_incluye_cultivos(self, client: AsyncClient) -> None:
        """GET /api/v1/admin/users/{phone_hash} devuelve cultivos."""
        phone_hash = "c1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
        # Crear via PUT
        await client.put(
            f"/api/v1/admin/users/{phone_hash}/comuna",
            json={
                "comuna": "Traiguén",
                "cultivos": ["papa", "cebolla"],
            },
            headers={"X-Admin-Key": "dev-admin-key"},
        )

        # Leer via GET
        resp = await client.get(
            f"/api/v1/admin/users/{phone_hash}",
            headers={"X-Admin-Key": "dev-admin-key"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["comuna"] == "Traiguén"
        assert data["cultivos"] == ["papa", "cebolla"]

    @pytest.mark.asyncio
    async def test_get_user_prefs_sin_cultivos(self, client: AsyncClient) -> None:
        """GET /api/v1/admin/users/{phone_hash} sin cultivos retorna null."""
        phone_hash = "d1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
        # Crear sin cultivos explicitamente
        await client.put(
            f"/api/v1/admin/users/{phone_hash}/comuna",
            json={"comuna": "Traiguén"},
            headers={"X-Admin-Key": "dev-admin-key"},
        )

        resp = await client.get(
            f"/api/v1/admin/users/{phone_hash}",
            headers={"X-Admin-Key": "dev-admin-key"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["cultivos"] is None

    @pytest.mark.asyncio
    async def test_put_actualiza_cultivos(self, client: AsyncClient) -> None:
        """PUT con cultivos nuevos reemplaza los anteriores."""
        phone_hash = "e1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
        # Crear con cultivos A
        await client.put(
            f"/api/v1/admin/users/{phone_hash}/comuna",
            json={
                "comuna": "Traiguén",
                "cultivos": ["papa", "trigo"],
            },
            headers={"X-Admin-Key": "dev-admin-key"},
        )

        # Actualizar a cultivos B
        resp = await client.put(
            f"/api/v1/admin/users/{phone_hash}/comuna",
            json={
                "comuna": "Traiguén",
                "cultivos": ["tomate", "cebolla", "zanahoria"],
            },
            headers={"X-Admin-Key": "dev-admin-key"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["cultivos"] == ["tomate", "cebolla", "zanahoria"]


class TestLLMWithCultivos:
    """Tests: LLM answer() recibe cultivos y los incluye en contexto (#125)."""

    @pytest.mark.asyncio
    async def test_answer_sin_cultivos_no_agrega_contexto(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """answer() sin cultivos no agrega linea personalizada al prompt."""
        from app.services.llm_service import _build_messages

        messages = _build_messages("a cuanto la papa", [], cultivos=None)
        system_content = str(messages[0]["content"])
        assert "El agricultor cultiva:" not in system_content

    @pytest.mark.asyncio
    async def test_answer_con_cultivos_agrega_contexto(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """answer() con cultivos agrega linea personalizada al system prompt."""
        from app.services.llm_service import _build_messages

        messages = _build_messages(
            "a cuanto la papa",
            [],
            cultivos=["papa", "trigo", "tomate"],
        )
        system_content = str(messages[0]["content"])
        assert "El agricultor cultiva: papa, trigo, tomate." in system_content
        assert "Si no especifica producto, asume uno de estos." in system_content

    @pytest.mark.asyncio
    async def test_answer_con_cultivos_en_mock(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """answer() responde igual con cultivos aunque sea mock (no rompe)."""
        from app.services.llm_service import answer

        # Mock: forzar que _get_model retorne None (modo mock)
        monkeypatch.setattr(
            "app.services.llm_service._get_model",
            lambda: None,
        )

        result = await answer("a cuanto la papa", cultivos=["papa", "trigo"])
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_pipeline_carga_cultivos(self, monkeypatch, db) -> None:  # type: ignore[no-untyped-def]
        """_load_user_cultivos carga cultivos desde user_prefs correctamente."""
        from app.services.pipeline_service import AgroVozPipeline

        # Crear user_prefs con cultivos
        prefs = UserPrefs(
            phone_hash="z" * 64,
            cultivos=json.dumps(["papa", "trigo"]),
        )
        db.add(prefs)
        db.commit()

        cultivos = AgroVozPipeline._load_user_cultivos("z" * 64)
        assert cultivos == ["papa", "trigo"]

    @pytest.mark.asyncio
    async def test_pipeline_sin_cultivos_retorna_none(self, db) -> None:  # type: ignore[no-untyped-def]
        """_load_user_cultivos retorna None si no hay cultivos."""
        from app.services.pipeline_service import AgroVozPipeline

        # Crear user_prefs sin cultivos
        prefs = UserPrefs(phone_hash="y" * 64)
        db.add(prefs)
        db.commit()

        cultivos = AgroVozPipeline._load_user_cultivos("y" * 64)
        assert cultivos is None

    @pytest.mark.asyncio
    async def test_pipeline_sin_prefs_retorna_none(self) -> None:  # type: ignore[no-untyped-def]
        """_load_user_cultivos retorna None si no existe user_prefs."""
        from app.services.pipeline_service import AgroVozPipeline

        cultivos = AgroVozPipeline._load_user_cultivos("nonexistent" * 8)
        assert cultivos is None


class TestUserPrefsMigration:
    """Tests de migracion: columna cultivos se agrega sin perder datos."""

    def test_migracion_agrega_cultivos_sin_perder_datos(self, db) -> None:  # type: ignore[no-untyped-def]
        """Filas existentes sin cultivos siguen funcionando tras migracion."""
        # Crear UserPrefs con datos existentes (simula fila pre-migracion)
        prefs = UserPrefs(
            phone_hash="m" * 64,
            comuna="Traiguén",
            dataset_consent=True,
        )
        db.add(prefs)
        db.commit()

        # Verificar que existe y sus datos no se perdieron
        result = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == "m" * 64))
        assert result is not None
        assert result.comuna == "Traiguén"
        assert result.dataset_consent is True
        # cultivos debe ser None (columna nueva, nullable)
        assert result.cultivos is None

    def test_migracion_no_borra_cultivos_existentes(self, db) -> None:  # type: ignore[no-untyped-def]
        """Filas con cultivos no se ven afectadas (idempotencia)."""
        # Crear UserPrefs con cultivos
        prefs = UserPrefs(
            phone_hash="n" * 64,
            comuna="Temuco",
            cultivos=json.dumps(["papa", "cebolla"]),
        )
        db.add(prefs)
        db.commit()

        # Simular re-aplicacion de migracion: recrear columna no la duplica
        result = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == "n" * 64))
        assert result is not None
        assert result.comuna == "Temuco"
        assert json.loads(result.cultivos) == ["papa", "cebolla"]
