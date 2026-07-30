"""Tests del motor de reglas agronómicas citadas (C1+C2)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.core.config import settings
from app.services.agronomic_rules_service import get_agronomic_rule_for_llm

_VERIFICATION_DATE = date(2026, 7, 30)


@pytest.fixture(autouse=True)
def _enable_agronomic_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """El gate está apagado por defecto; estos tests ejercitan el motor con él prendido."""
    monkeypatch.setattr(settings, "agronomic_rules_enabled", True)


def _write_catalog(
    path: Path,
    *,
    review_before: str = "2027-01-30",
    sintomas: tuple[str, ...] = ("manchas marrones en las hojas",),
) -> None:
    """Crea un snapshot mínimo para probar validación de vigencia y matching."""
    sintomas_yaml = "\n".join(f'      - "{s}"' for s in sintomas)
    path.write_text(
        (
            "version: 1\n"
            'verificado_el: "2026-07-30"\n'
            f'revisar_antes_de: "{review_before}"\n'
            "reglas:\n"
            '  - id: "papa_tizon_tardio"\n'
            '    cultivo: "papa"\n'
            "    sintomas:\n"
            f"{sintomas_yaml}\n"
            '    fuente: "INIA Chile — Enfermedades de la papa: Tizón tardío"\n'
            '    fuente_url: "https://enfermedadespapa.inia.cl/tizonTardio.php"\n'
            '    fecha: "2024"\n'
            '    diagnostico: "Es tizón tardío, según INIA."\n'
            '    siguiente_paso: "Consulta a tu agrónomo PRODESAL."\n'
        ),
        encoding="utf-8",
    )


def test_sintoma_conocido_retorna_diagnostico_citado() -> None:
    """La respuesta real del snapshot cita fuente y fecha, no improvisa."""
    response = get_agronomic_rule_for_llm(
        "mis papas tienen manchas marrones en las hojas",
        "papa",
        today=_VERIFICATION_DATE,
    )

    assert "tizón tardío" in response
    assert "INIA" in response
    assert "30/07/2026" in response


def test_sintoma_desconocido_dice_que_no_tiene_el_dato(tmp_path: Path) -> None:
    """Sin una regla que calce, el sistema no rellena con una respuesta generica."""
    catalog_path = tmp_path / "reglas_agronomicas.yaml"
    _write_catalog(catalog_path)

    response = get_agronomic_rule_for_llm(
        "mi tractor no enciende",
        "papa",
        today=_VERIFICATION_DATE,
        corpus_path=catalog_path,
    )

    assert "no tengo una regla verificada" in response.lower()
    assert "tizón" not in response.lower()


def test_cultivo_distinto_no_hace_match(tmp_path: Path) -> None:
    """Un síntoma real de papa no se responde para un cultivo distinto."""
    catalog_path = tmp_path / "reglas_agronomicas.yaml"
    _write_catalog(catalog_path)

    response = get_agronomic_rule_for_llm(
        "mis papas tienen manchas marrones en las hojas",
        "trigo",
        today=_VERIFICATION_DATE,
        corpus_path=catalog_path,
    )

    assert "no tengo una regla verificada" in response.lower()


def test_sin_cultivo_busca_en_cualquiera(tmp_path: Path) -> None:
    """Sin cultivo declarado, el síntoma igual encuentra la regla."""
    catalog_path = tmp_path / "reglas_agronomicas.yaml"
    _write_catalog(catalog_path)

    response = get_agronomic_rule_for_llm(
        "mis papas tienen manchas marrones en las hojas",
        today=_VERIFICATION_DATE,
        corpus_path=catalog_path,
    )

    assert "tizón tardío" in response


def test_sintoma_vacio_pide_repetir() -> None:
    assert "no entendí" in get_agronomic_rule_for_llm("", "papa").lower()


def test_snapshot_vencido_falla_cerrado(tmp_path: Path) -> None:
    """Al pasar revisar_antes_de, se prefiere no responder a dar un dato viejo."""
    catalog_path = tmp_path / "reglas_agronomicas.yaml"
    _write_catalog(catalog_path, review_before="2026-07-01")

    response = get_agronomic_rule_for_llm(
        "mis papas tienen manchas marrones en las hojas",
        "papa",
        today=_VERIFICATION_DATE,
        corpus_path=catalog_path,
    )

    assert "no puedo detallar" not in response.lower()
    assert "prefiero no improvisarlo" in response.lower()
    assert "tizón" not in response.lower()


def test_yaml_roto_falla_cerrado(tmp_path: Path) -> None:
    catalog_path = tmp_path / "reglas_agronomicas.yaml"
    catalog_path.write_text("reglas: [", encoding="utf-8")

    response = get_agronomic_rule_for_llm(
        "mis papas tienen manchas marrones en las hojas",
        "papa",
        today=_VERIFICATION_DATE,
        corpus_path=catalog_path,
    )

    assert "prefiero no improvisarlo" in response.lower()


def test_archivo_inexistente_falla_cerrado(tmp_path: Path) -> None:
    response = get_agronomic_rule_for_llm(
        "mis papas tienen manchas marrones en las hojas",
        "papa",
        today=_VERIFICATION_DATE,
        corpus_path=tmp_path / "no-existe.yaml",
    )

    assert "prefiero no improvisarlo" in response.lower()


def test_id_duplicado_falla_cerrado(tmp_path: Path) -> None:
    catalog_path = tmp_path / "reglas_agronomicas.yaml"
    catalog_path.write_text(
        (
            "version: 1\n"
            'verificado_el: "2026-07-30"\n'
            'revisar_antes_de: "2027-01-30"\n'
            "reglas:\n"
            '  - id: "papa_tizon_tardio"\n'
            '    cultivo: "papa"\n'
            '    sintomas: ["manchas"]\n'
            '    fuente: "INIA"\n'
            '    fuente_url: "https://enfermedadespapa.inia.cl/tizonTardio.php"\n'
            '    fecha: "2024"\n'
            '    diagnostico: "x"\n'
            '  - id: "papa_tizon_tardio"\n'
            '    cultivo: "papa"\n'
            '    sintomas: ["otras manchas"]\n'
            '    fuente: "INIA"\n'
            '    fuente_url: "https://enfermedadespapa.inia.cl/tizonTardio.php"\n'
            '    fecha: "2024"\n'
            '    diagnostico: "y"\n'
        ),
        encoding="utf-8",
    )

    response = get_agronomic_rule_for_llm(
        "manchas",
        "papa",
        today=_VERIFICATION_DATE,
        corpus_path=catalog_path,
    )

    assert "prefiero no improvisarlo" in response.lower()


def test_snapshot_real_resuelve_las_cuatro_reglas_de_papa() -> None:
    """Las cuatro consultas cubiertas por corpus/reglas_agronomicas.yaml responden citando INIA."""
    casos = [
        ("mis papas tienen manchas marrones en las hojas", "tizón tardío"),
        ("cuando siembro la papa", "profundos"),
        ("que cultivo va antes de la papa", "rotación"),
        ("cuando cosecho la papa", "follaje"),
    ]
    for consulta, fragmento_esperado in casos:
        response = get_agronomic_rule_for_llm(consulta, "papa", today=_VERIFICATION_DATE)
        assert fragmento_esperado in response, f"'{consulta}' no encontró su regla: {response!r}"
        assert "INIA" in response


class TestGateApagado:
    """El motor debe fallar cerrado a nivel de servicio, no solo en el filtro del LLM.

    Bug encontrado en auditoría (30/07/2026): el gate solo se chequeaba en
    ``_offered_tools`` de llm_service — si el LLM alucinaba el nombre de la
    tool, esta se ejecutaba igual porque solo está validada contra
    WHITELIST_TOOLS, no contra el gate. Defensa en profundidad: ahora el
    propio servicio se niega, igual que expense_service/parcela_service.
    """

    def test_gate_apagado_no_responde_aunque_la_regla_exista(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "agronomic_rules_enabled", False)

        response = get_agronomic_rule_for_llm(
            "mis papas tienen manchas marrones en las hojas",
            "papa",
            today=_VERIFICATION_DATE,
        )

        assert "no está habilitado" in response.lower()
        assert "tizón" not in response.lower()

    def test_gate_apagado_se_chequea_antes_que_el_sintoma_vacio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "agronomic_rules_enabled", False)

        response = get_agronomic_rule_for_llm("", "papa")

        assert "no está habilitado" in response.lower()


class TestSintomasTempranosTizon:
    """Auditoría 30/07/2026: el corpus original solo matcheaba el síntoma tardío.

    INIA (enfermedadespapa.inia.cl) describe el síntoma inicial como manchas
    acuosas verde oscuro con halo amarillo pálido en el borde de las hojas
    inferiores — antes de que aparezcan las manchas marrones tardías. Perder
    ese matching pierde justo la ventana de detección temprana, donde el
    fungicida preventivo todavía sirve.
    """

    def test_sintoma_temprano_verde_oscuro_hace_match(self) -> None:
        response = get_agronomic_rule_for_llm(
            "las hojas de abajo tienen manchas verde oscuro en las hojas",
            "papa",
            today=_VERIFICATION_DATE,
        )

        assert "tizón tardío" in response

    def test_sintoma_temprano_halo_amarillo_hace_match(self) -> None:
        response = get_agronomic_rule_for_llm(
            "las manchas tienen un borde amarillo en las hojas",
            "papa",
            today=_VERIFICATION_DATE,
        )

        assert "tizón tardío" in response

    def test_rango_de_temperatura_corregido(self) -> None:
        """El rango real (INIA) es 15-25°C, no el 10-25°C del corpus original."""
        response = get_agronomic_rule_for_llm(
            "mis papas tienen manchas marrones en las hojas",
            "papa",
            today=_VERIFICATION_DATE,
        )

        assert "15°C y 25°C" in response
        assert "10°C" not in response

    def test_cita_incluye_fuente_url(self) -> None:
        response = get_agronomic_rule_for_llm(
            "mis papas tienen manchas marrones en las hojas",
            "papa",
            today=_VERIFICATION_DATE,
        )

        assert "https://enfermedadespapa.inia.cl/tizonTardio.php" in response
