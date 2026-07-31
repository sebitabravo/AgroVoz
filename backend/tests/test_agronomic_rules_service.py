"""Tests del motor de reglas agronómicas citadas (C1+C2)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.services.agronomic_rules_service import get_agronomic_rule_for_llm

_VERIFICATION_DATE = date(2026, 7, 30)


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
            '    fuente: "INIA Chile — Ficha técnica"\n'
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
            '    fecha: "2024"\n'
            '    diagnostico: "x"\n'
            '  - id: "papa_tizon_tardio"\n'
            '    cultivo: "papa"\n'
            '    sintomas: ["otras manchas"]\n'
            '    fuente: "INIA"\n'
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
