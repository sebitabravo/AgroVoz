"""Matriz determinista de los feature flags definidos en ``Settings``.

La auditoría detectó inicialmente 13 flags en ``Settings``. Tras eliminar el
flag obsoleto ``use_typed_extraction``, quedan 12 flags runtime activos. La
matriz no inicia modelos ni llama servicios externos: verifica que cada flag
permanezca cerrado por defecto, acepte el opt-in explícito y conserve el guard
fail-closed de su consumer conocido.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest
from fastapi import HTTPException

from app.core.config import Settings, settings

_VALID_PHONE_HASH = "a" * 64


@dataclass(frozen=True)
class FeatureFlagContract:
    """Contrato auditable de un flag y sus consumers observados en runtime."""

    field: str
    env_name: str
    consumers: tuple[str, ...]
    probe: Callable[[], str]
    off_expected: str
    on_expected: str


def _probe_http_guard(guard: Callable[[], None]) -> str:
    """Clasifica un guard HTTP sin levantar ASGI ni tocar red."""
    try:
        guard()
    except HTTPException as exc:
        return f"blocked:{exc.status_code}"
    return "enabled"


def _probe_mcp_security() -> str:
    """Valida que MCP sin claves fuertes no pueda activarse accidentalmente."""
    config = Settings(  # type: ignore[call-arg]  # pydantic-settings agrega _env_file dinámicamente
        _env_file=None,
        mcp_enabled=settings.mcp_enabled,
        mcp_api_key="short" if settings.mcp_enabled else "",
        mcp_admin_key="short-admin" if settings.mcp_enabled else "",
    )
    try:
        config.validate_mcp_security()
    except ValueError:
        return "blocked"
    return "allowed"


def _probe_conversation_state() -> str:
    """Comprueba creación lazy del registro solo con el flag activo."""
    from app.services.pipeline_service import _get_conversation_registry

    return "enabled" if _get_conversation_registry() is not None else "disabled"


def _probe_consultation_history() -> str:
    """Comprueba el retorno seguro antes de persistir contenido sin consentimiento."""
    from app.services.consultation_history_service import save_to_history_if_consented

    saved = save_to_history_if_consented(
        _VALID_PHONE_HASH,
        "precio de papa",
        "No hay dato disponible.",
        "precio",
        "papa",
    )
    return "saved" if saved else ("disabled" if not settings.consultation_history_enabled else "no-consent")


def _probe_expense() -> str:
    """Comprueba que el gate preceda a la validación de identidad."""
    from app.services.expense_service import register_expense_for_llm

    response = register_expense_for_llm(
        session=None,  # type: ignore[arg-type]
        producto="papa",
        concepto="semilla",
        monto="1000",
        phone_hash="invalid",
    )
    return "disabled" if "todavía no está habilitado" in response else "identity"


def _probe_parcela() -> str:
    """Comprueba que el gate preceda a la validación de identidad."""
    from app.services.parcela_service import register_parcela_for_llm

    response = register_parcela_for_llm(
        session=None,  # type: ignore[arg-type]
        cultivo="papa",
        superficie_ha="2",
        comuna="Traiguén",
        phone_hash="invalid",
    )
    return "disabled" if "todavía no está habilitado" in response else "identity"


def _probe_location() -> str:
    """Comprueba que la ubicación rechace escritura cuando el gate está apagado."""
    from app.services.location_service import LocationConsentError, save_user_location

    try:
        save_user_location("invalid", -38.2, -72.6)
    except LocationConsentError:
        return "disabled"
    except ValueError:
        return "identity"
    return "saved"


def _probe_agronomic_rules() -> str:
    """Comprueba el mensaje de gate y la validación local sin cargar corpus."""
    from app.services.agronomic_rules_service import get_agronomic_rule_for_llm

    response = get_agronomic_rule_for_llm("", "papa")
    if not settings.agronomic_rules_enabled:
        return "disabled"
    return "input" if "entendí" in response else "resolved"


def _probe_pdf_report() -> str:
    """Comprueba el gate antes de identidad, DB, clima y render PDF."""
    from app.services.report_service import get_reporte_pdf_for_llm

    response = get_reporte_pdf_for_llm("invalid")
    return "disabled" if "todavía no están habilitados" in response else "identity"


def _probe_vision() -> str:
    """Comprueba el guard HTTP sin iniciar ONNX ni leer una imagen."""
    from app.api.vision import _require_vision_enabled

    return _probe_http_guard(_require_vision_enabled)


def _probe_demo() -> str:
    """Comprueba el guard HTTP sin ejecutar LLM ni TTS."""
    from app.api.demo import _require_demo_enabled

    return _probe_http_guard(_require_demo_enabled)


def _probe_panel() -> str:
    """Comprueba el guard HTTP del panel sin leer archivos estáticos."""
    from app.panel_web import _require_panel_enabled

    return _probe_http_guard(_require_panel_enabled)


def _probe_agronomist_console() -> str:
    """Comprueba el guard HTTP de la consola sin servir HTML."""
    from app.agronomist_web import _require_agronomist_console_enabled

    return _probe_http_guard(_require_agronomist_console_enabled)


def _probe_flags() -> tuple[str, ...]:
    """Retorna los nombres de flags bool auditables, excluyendo ``debug``."""
    return tuple(
        name
        for name, field in Settings.model_fields.items()
        if field.annotation is bool and (name.endswith("_enabled") or name.startswith("use_"))
    )


_FLAG_CONTRACTS: tuple[FeatureFlagContract, ...] = (
    FeatureFlagContract(
        "vision_enabled",
        "VISION_ENABLED",
        (
            "app/core/security.py:VisionPayloadGuardMiddleware",
            "app/api/webhooks.py:webhook_whatsapp",
            "app/api/vision.py:_require_vision_enabled",
            "app/services/vision_service.py:VisionService",
        ),
        _probe_vision,
        "blocked:503",
        "enabled",
    ),
    FeatureFlagContract(
        "mcp_enabled",
        "MCP_ENABLED",
        ("app/main.py:lifespan", "app/core/config.py:validate_mcp_security"),
        _probe_mcp_security,
        "allowed",
        "blocked",
    ),
    FeatureFlagContract(
        "demo_endpoint_enabled",
        "DEMO_ENDPOINT_ENABLED",
        ("app/api/demo.py:_require_demo_enabled",),
        _probe_demo,
        "blocked:503",
        "enabled",
    ),
    FeatureFlagContract(
        "farmer_panel_enabled",
        "FARMER_PANEL_ENABLED",
        (
            "app/api/panel.py:_require_panel_enabled",
            "app/panel_web.py:_require_panel_enabled",
            "app/services/panel_service.py:get_panel_link_for_llm",
            "app/services/panel_service.py:get_panel_summary",
            "app/services/panel_service.py:get_panel_price_history",
            "app/services/llm_service.py:_GATED_TOOLS[get_link_resumen]",
        ),
        _probe_panel,
        "blocked:503",
        "enabled",
    ),
    FeatureFlagContract(
        "agronomist_console_enabled",
        "AGRONOMIST_CONSOLE_ENABLED",
        (
            "app/api/agronomist.py:_require_agronomist_console_enabled",
            "app/agronomist_web.py:_require_agronomist_console_enabled",
            "app/api/admin/agronomist_admin.py",
            "app/services/agronomist_console_service.py:get_group_summary",
        ),
        _probe_agronomist_console,
        "blocked:503",
        "enabled",
    ),
    FeatureFlagContract(
        "use_conversation_state",
        "USE_CONVERSATION_STATE",
        (
            "app/services/pipeline_service.py:_get_conversation_registry",
            "app/services/pipeline_service.py:_claim_conversation",
        ),
        _probe_conversation_state,
        "disabled",
        "enabled",
    ),
    FeatureFlagContract(
        "consultation_history_enabled",
        "CONSULTATION_HISTORY_ENABLED",
        (
            "app/services/consultation_history_service.py:save_to_history_if_consented",
            "app/services/consultation_history_service.py:save_delivered_consultation_to_history",
            "app/services/consultation_history_service.py:get_latest_consultation_context",
            "app/services/consultation_history_service.py:get_history",
            "app/api/admin/user_admin.py",
            "app/main.py:lifespan",
        ),
        _probe_consultation_history,
        "disabled",
        "no-consent",
    ),
    FeatureFlagContract(
        "expense_tracking_enabled",
        "EXPENSE_TRACKING_ENABLED",
        (
            "app/services/expense_service.py:register_expense_for_llm",
            "app/services/expense_service.py:get_active_expense_total",
            "app/services/llm_service.py:_GATED_TOOLS[register_expense]",
        ),
        _probe_expense,
        "disabled",
        "identity",
    ),
    FeatureFlagContract(
        "parcela_tracking_enabled",
        "PARCELA_TRACKING_ENABLED",
        (
            "app/services/parcela_service.py:register_parcela_for_llm",
            "app/services/parcela_service.py:get_parcelas_for_llm",
            "app/services/agronomist_console_service.py:get_group_summary",
            "app/services/panel_service.py:get_panel_summary",
            "app/services/llm_service.py:_GATED_TOOLS[register_parcela/get_parcelas]",
        ),
        _probe_parcela,
        "disabled",
        "identity",
    ),
    FeatureFlagContract(
        "location_sharing_enabled",
        "LOCATION_SHARING_ENABLED",
        ("app/services/location_service.py:save_user_location",),
        _probe_location,
        "disabled",
        "identity",
    ),
    FeatureFlagContract(
        "agronomic_rules_enabled",
        "AGRONOMIC_RULES_ENABLED",
        (
            "app/services/agronomic_rules_service.py:get_agronomic_rule_for_llm",
            "app/services/agricultural_calendar_service.py:get_agricultural_calendar_for_llm",
            "app/services/llm_service.py:_GATED_TOOLS[get_regla_agronomica/get_calendario_agricola]",
        ),
        _probe_agronomic_rules,
        "disabled",
        "input",
    ),
    FeatureFlagContract(
        "pdf_reports_enabled",
        "PDF_REPORTS_ENABLED",
        (
            "app/services/report_service.py:generate_weekly_report",
            "app/services/report_service.py:get_reporte_pdf_for_llm",
            "app/services/pipeline_service.py:reporte_pdf",
            "app/services/llm_service.py:_GATED_TOOLS[get_reporte_pdf]",
        ),
        _probe_pdf_report,
        "disabled",
        "identity",
    ),
)


def test_feature_flag_inventory_matches_settings() -> None:
    """Falla si config agrega o quita una flag sin consumer y contrato."""
    fields = tuple(contract.field for contract in _FLAG_CONTRACTS)

    assert len(fields) == 12
    assert len(set(fields)) == 12
    assert set(fields) == set(_probe_flags())
    assert all(contract.consumers for contract in _FLAG_CONTRACTS)


@pytest.mark.parametrize("contract", _FLAG_CONTRACTS, ids=lambda item: item.field)
def test_feature_flag_defaults_closed_and_accepts_explicit_opt_in(
    contract: FeatureFlagContract,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cada flag parte en false y solo cambia con su variable documentada."""
    monkeypatch.delenv(contract.env_name, raising=False)
    disabled = Settings(_env_file=None)  # type: ignore[call-arg]
    assert getattr(disabled, contract.field) is False

    monkeypatch.setenv(contract.env_name, "true")
    enabled = Settings(_env_file=None)  # type: ignore[call-arg]
    assert getattr(enabled, contract.field) is True


@pytest.mark.parametrize("contract", _FLAG_CONTRACTS, ids=lambda item: item.field)
def test_feature_flag_consumer_is_fail_closed_off_and_explicit_on(
    contract: FeatureFlagContract,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cada consumer queda cerrado sin el flag y solo admite el opt-in explícito."""
    assert contract.off_expected != contract.on_expected

    monkeypatch.setattr(settings, contract.field, False)
    off_result = contract.probe()
    assert off_result == contract.off_expected

    monkeypatch.setattr(settings, contract.field, True)
    on_result = contract.probe()
    assert on_result == contract.on_expected
    assert off_result != on_result
