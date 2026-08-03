"""Contratos LLM de la tool de programas INDAP."""

from __future__ import annotations

import pytest

from app.services.llm_service import TOOLS, WHITELIST_TOOLS, _get_tool_handlers


def test_get_programas_indap_esta_en_whitelist_y_definiciones() -> None:
    """La tool se anuncia y se valida con la misma fuente de nombres."""
    assert "get_programas_indap" in WHITELIST_TOOLS
    names = [str(tool["function"]["name"]) for tool in TOOLS]
    assert names.count("get_programas_indap") == 1
    assert "get_programas_indap" in _get_tool_handlers()


@pytest.mark.asyncio
async def test_get_programas_indap_se_ejecuta_desde_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El dispatcher puede ejecutar la tool sin invocar al modelo."""
    from app.services.llm_service import _execute_tool

    def fake_programas(consulta: str) -> str:
        return f"INDAP: {consulta}; Agencia de Área Traiguén, Riveros #1059."

    monkeypatch.setattr(
        "app.services.indap_credit_service.get_programas_indap",
        fake_programas,
    )
    response = await _execute_tool(
        "get_programas_indap",
        {"consulta": "programa para comprar maquinaria"},
    )

    assert "INDAP" in response
    assert "Riveros #1059" in response
