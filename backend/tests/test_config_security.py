"""Regresiones de configuración sensible para producción.

La auditoría #207 reportó que el pepper público solo generaba un warning.
El guard ya bloquea producción; estos tests fijan ese contrato para evitar
que una refactorización vuelva a permitir hashes predecibles.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


class TestPhoneHashPepper:
    """Validación fail-fast del pepper usado para anonimizar teléfonos."""

    def test_produccion_rechaza_pepper_default(self) -> None:
        """El valor público del repositorio no puede iniciar producción."""
        config = Settings(
            app_env="production",
            phone_hash_pepper="agrovoz-dev-pepper",
        )

        with pytest.raises(ValueError, match="PHONE_HASH_PEPPER"):
            config.validate_pepper_not_default()

    def test_produccion_rechaza_pepper_vacio(self) -> None:
        """Un secret vacío inyectado por Docker tampoco puede iniciar."""
        config = Settings(
            app_env="production",
            phone_hash_pepper="",
        )

        with pytest.raises(ValueError, match="PHONE_HASH_PEPPER"):
            config.validate_pepper_not_default()

    def test_produccion_acepta_pepper_privado(self) -> None:
        """Un pepper configurado y distinto del default supera el guard."""
        config = Settings(
            app_env="production",
            phone_hash_pepper="pepper-privado-de-produccion",
        )

        config.validate_pepper_not_default()


class TestConversationStateConfig:
    """Configuración segura del dominio conversacional post-MVP."""

    def test_state_machine_esta_apagada_y_timeout_es_30_por_defecto(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """El bloque nuevo no activa retención ni cambia el pipeline actual."""
        monkeypatch.delenv("USE_CONVERSATION_STATE", raising=False)
        monkeypatch.delenv("CONVERSATION_TIMEOUT_MINUTES", raising=False)

        config = Settings(_env_file=None)

        assert config.use_conversation_state is False
        assert config.conversation_timeout_minutes == 30

    def test_variables_de_entorno_configuran_state_machine(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pydantic reconoce los nombres documentados para despliegue."""
        monkeypatch.setenv("USE_CONVERSATION_STATE", "true")
        monkeypatch.setenv("CONVERSATION_TIMEOUT_MINUTES", "45")

        config = Settings(_env_file=None)

        assert config.use_conversation_state is True
        assert config.conversation_timeout_minutes == 45

    @pytest.mark.parametrize("timeout_minutes", [0, 1441])
    def test_timeout_rechaza_valores_fuera_de_rango(
        self,
        timeout_minutes: int,
    ) -> None:
        """El dominio configurable acepta entre un minuto y un día."""
        with pytest.raises(ValidationError):
            Settings(conversation_timeout_minutes=timeout_minutes)

    @pytest.mark.parametrize("timeout_minutes", [1, 1440])
    def test_timeout_acepta_ambos_limites(
        self,
        timeout_minutes: int,
    ) -> None:
        """Los extremos documentados forman parte del rango válido."""
        config = Settings(conversation_timeout_minutes=timeout_minutes)

        assert config.conversation_timeout_minutes == timeout_minutes


class TestConsultationHistorySecurity:
    """Configuración fail-closed del historial de consultas."""

    def test_historial_esta_apagado_y_retiene_28_dias_por_defecto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Los defaults no activan tratamiento nuevo de datos personales."""
        monkeypatch.delenv("CONSULTATION_HISTORY_ENABLED", raising=False)
        monkeypatch.delenv("CONSULTATION_HISTORY_TTL_DAYS", raising=False)
        monkeypatch.delenv("CONSULTATION_HISTORY_AUDIT_KEY", raising=False)

        config = Settings(_env_file=None)

        assert config.consultation_history_enabled is False
        assert config.consultation_history_ttl_days == 28
        assert config.consultation_history_audit_key.get_secret_value() == ""

    def test_ttl_rechaza_cero(self) -> None:
        """La retención configurable nunca puede ser menor a un día."""
        with pytest.raises(ValidationError):
            Settings(consultation_history_ttl_days=0)

    def test_variables_de_entorno_configuran_historial(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pydantic reconoce los nombres documentados para despliegue."""
        monkeypatch.setenv("CONSULTATION_HISTORY_ENABLED", "true")
        monkeypatch.setenv("CONSULTATION_HISTORY_TTL_DAYS", "35")
        monkeypatch.setenv(
            "CONSULTATION_HISTORY_AUDIT_KEY",
            "a" * 32,
        )

        config = Settings(_env_file=None)

        assert config.consultation_history_enabled is True
        assert config.consultation_history_ttl_days == 35
        assert config.consultation_history_audit_key.get_secret_value() == "a" * 32

    def test_produccion_rechaza_clave_corta_si_historial_esta_activo(
        self,
    ) -> None:
        """Una auditoría con clave débil no puede iniciar en producción."""
        config = Settings(
            app_env="production",
            consultation_history_enabled=True,
            consultation_history_audit_key="clave-corta",
        )

        with pytest.raises(ValueError, match="CONSULTATION_HISTORY_AUDIT_KEY"):
            config.validate_consultation_history_security()

    def test_produccion_permite_clave_vacia_si_historial_esta_apagado(
        self,
    ) -> None:
        """El andamiaje inactivo no obliga a provisionar secretos aún."""
        config = Settings(
            app_env="production",
            consultation_history_enabled=False,
            consultation_history_audit_key="",
        )

        config.validate_consultation_history_security()

    def test_produccion_acepta_clave_de_32_caracteres(self) -> None:
        """El límite mínimo documentado permite activar el historial."""
        config = Settings(
            app_env="production",
            consultation_history_enabled=True,
            consultation_history_audit_key="a" * 32,
        )

        config.validate_consultation_history_security()

    def test_desarrollo_advierte_pero_no_bloquea_clave_corta(self) -> None:
        """Desarrollo mantiene feedback visible sin impedir el trabajo local."""
        config = Settings(
            app_env="development",
            consultation_history_enabled=True,
            consultation_history_audit_key="clave-corta",
        )

        with pytest.warns(RuntimeWarning, match="CONSULTATION_HISTORY_AUDIT_KEY"):
            config.validate_consultation_history_security()


class TestExpenseTrackingSecurity:
    """El prototipo de gastos no puede activarse por accidente."""

    def test_registro_gastos_esta_apagado_por_defecto(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sin variable explícita no se persisten datos económicos."""
        monkeypatch.delenv("EXPENSE_TRACKING_ENABLED", raising=False)

        config = Settings(_env_file=None)

        assert config.expense_tracking_enabled is False

    def test_variable_entorno_configura_gate(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pydantic reconoce el gate para pruebas controladas."""
        monkeypatch.setenv("EXPENSE_TRACKING_ENABLED", "true")

        config = Settings(_env_file=None)

        assert config.expense_tracking_enabled is True


class TestMcpSecurity:
    """Configuración fail-closed de la RPC administrativa interna tipo MCP."""

    def test_mcp_esta_apagado_y_sin_claves_por_defecto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Los defaults no habilitan una superficie administrativa nueva."""
        monkeypatch.delenv("MCP_ENABLED", raising=False)
        monkeypatch.delenv("MCP_API_KEY", raising=False)
        monkeypatch.delenv("MCP_ADMIN_KEY", raising=False)

        config = Settings(_env_file=None)

        assert config.mcp_enabled is False
        assert config.mcp_api_key == ""
        assert config.mcp_admin_key == ""
        config.validate_mcp_security()

    def test_mcp_apagado_permite_claves_vacias_en_produccion(self) -> None:
        """El gate inactivo no obliga a provisionar credenciales aún."""
        config = Settings(
            app_env="production",
            mcp_enabled=False,
            mcp_api_key="",
            mcp_admin_key="",
        )

        config.validate_mcp_security()

    @pytest.mark.parametrize("app_env", ["development", "test", "production"])
    @pytest.mark.parametrize("field_name", ["MCP_API_KEY", "MCP_ADMIN_KEY"])
    def test_mcp_activo_rechaza_claves_cortas_en_cualquier_entorno(self, app_env: str, field_name: str) -> None:
        """La activación insegura siempre falla, incluso fuera de producción."""
        config_values = {
            "mcp_api_key": "read-" + "a7f93c2e" * 4,
            "mcp_admin_key": "admin-" + "b7c92f1e" * 4,
        }
        config_values["mcp_api_key" if field_name == "MCP_API_KEY" else "mcp_admin_key"] = "clave-corta"
        config = Settings(app_env=app_env, mcp_enabled=True, **config_values)

        with pytest.raises(ValueError, match=field_name):
            config.validate_mcp_security()

    def test_mcp_activo_rechaza_claves_iguales(self) -> None:
        """Lectura y administración no pueden compartir credencial."""
        shared_key = "read-" + "a7f93c2e" * 4
        config = Settings(
            mcp_enabled=True,
            mcp_api_key=shared_key,
            mcp_admin_key=shared_key,
        )

        with pytest.raises(ValueError, match="deben ser distintas"):
            config.validate_mcp_security()

    @pytest.mark.parametrize(
        ("field_name", "placeholder"),
        [
            ("MCP_API_KEY", "change-me-" * 4),
            ("MCP_ADMIN_KEY", "x" * 32),
        ],
    )
    def test_mcp_activo_rechaza_placeholders_obvios(self, field_name: str, placeholder: str) -> None:
        """Valores largos pero previsibles tampoco superan el guard."""
        config_values = {
            "mcp_api_key": "read-" + "a7f93c2e" * 4,
            "mcp_admin_key": "admin-" + "b7c92f1e" * 4,
        }
        config_values["mcp_api_key" if field_name == "MCP_API_KEY" else "mcp_admin_key"] = placeholder
        config = Settings(mcp_enabled=True, **config_values)

        with pytest.raises(ValueError, match=field_name):
            config.validate_mcp_security()

    def test_variables_de_entorno_configuran_mcp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pydantic reconoce las variables documentadas para despliegue."""
        monkeypatch.setenv("MCP_ENABLED", "true")
        monkeypatch.setenv("MCP_API_KEY", "read-" + "a7f93c2e" * 4)
        monkeypatch.setenv("MCP_ADMIN_KEY", "admin-" + "b7c92f1e" * 4)

        config = Settings(_env_file=None)

        assert config.mcp_enabled is True
        config.validate_mcp_security()

    def test_mcp_activo_acepta_claves_fuertes_y_distintas(self) -> None:
        """Dos claves independientes permiten habilitar la RPC interna."""
        config = Settings(
            mcp_enabled=True,
            mcp_api_key="read-" + "a7f93c2e" * 4,
            mcp_admin_key="admin-" + "b7c92f1e" * 4,
        )

        config.validate_mcp_security()
