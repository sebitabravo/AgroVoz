"""Pruebas deterministas del calendario agrícola citado de INIA."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.core.config import settings
from app.services.agricultural_calendar_service import get_calendario_agricola

_VERIFICATION_DATE = date(2026, 8, 3)


@pytest.fixture(autouse=True)
def _enable_agronomic_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Los casos ejercitan el motor con su gate explícitamente habilitado."""
    monkeypatch.setattr(settings, "agronomic_rules_enabled", True)


def test_calendario_conocido_cita_ventanas_fuente_y_fecha() -> None:
    """Trigo en la comuna piloto devuelve solo la ventana publicada por INIA."""
    response = get_calendario_agricola("trigo", "Traiguén", today=_VERIFICATION_DATE)

    assert "15/04 a 30/05" in response
    assert "15/01 a 28/02" in response
    assert "INIA Carillanca" in response
    assert "01/03/2017" in response
    assert "https://planpredial.inia.cl/media/documentos/trigo_invernal.pdf" in response
    assert "03/08/2026" in response


def test_alias_de_cultivo_resuelve_la_regla_citada() -> None:
    """El alias choclo apunta a la ficha de maíz sin inferir otra fecha."""
    response = get_calendario_agricola("choclo", "Traiguen", today=_VERIFICATION_DATE)

    assert "maíz dulce" in response
    assert "01/10 a 30/10" in response
    assert "15/02 a 15/04" in response


@pytest.mark.parametrize(
    ("producto", "comuna"),
    [("cebolla", "Traiguén"), ("trigo", "Santiago"), ("cultivo inventado", "Traiguén")],
)
def test_cobertura_ausente_falla_cerrado(producto: str, comuna: str) -> None:
    """Sin fila INIA para ambos parámetros no se fabrican ventanas."""
    response = get_calendario_agricola(producto, comuna, today=_VERIFICATION_DATE)

    assert "no tengo una ventana" in response.lower()
    assert "15/04" not in response
    assert "fuente publicada" not in response.lower()


def test_entrada_incompleta_pide_cultivo_y_comuna() -> None:
    response = get_calendario_agricola("trigo", "", today=_VERIFICATION_DATE)

    assert "cultivo y la comuna" in response


def test_gate_apagado_falla_cerrado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agronomic_rules_enabled", False)

    response = get_calendario_agricola("trigo", "Traiguén", today=_VERIFICATION_DATE)

    assert "no está habilitado" in response.lower()
    assert "15/04" not in response


def test_snapshot_vencido_no_entrega_ventanas(tmp_path: Path) -> None:
    """Un snapshot vencido no se usa aunque contenga una regla coincidente."""
    catalog_path = tmp_path / "calendario_agricola.yaml"
    catalog_path.write_text(
        (
            "version: 1\n"
            'verificado_el: "2026-01-01"\n'
            'revisar_antes_de: "2026-02-01"\n'
            "reglas:\n"
            '  - id: "trigo"\n'
            '    cultivo: "trigo"\n'
            '    alias: ["trigo"]\n'
            '    zona: "secano interior"\n'
            '    siembra: "01/01 a 02/01"\n'
            '    cosecha: "03/01 a 04/01"\n'
            '    fuente: "INIA de prueba"\n'
            '    fuente_url: "https://inia.cl/prueba"\n'
            '    fecha: "2020-01-01"\n'
        ),
        encoding="utf-8",
    )

    response = get_calendario_agricola(
        "trigo",
        "Traiguén",
        today=_VERIFICATION_DATE,
        corpus_path=catalog_path,
    )

    assert "prefiero no" not in response.lower()
    assert "no puedo detallar" in response.lower()
    assert "01/01 a 02/01" not in response
