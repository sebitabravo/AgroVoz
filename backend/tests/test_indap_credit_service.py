"""Tests de derivación informativa a financiamiento INDAP."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.services.indap_credit_service import get_indap_credit_referral

_VERIFICATION_DATE = date(2026, 7, 29)


def _write_catalog(
    path: Path,
    *,
    source_url: str,
    review_before: str = "2026-08-28",
    summary: str = "Información oficial segura.",
) -> None:
    """Crea un snapshot mínimo para probar validación de fuente y vigencia."""
    documents = (
        ("credito_corto_plazo", "Corto"),
        ("credito_largo_plazo", "Largo"),
        ("programa_desarrollo_inversiones", "PDI"),
        ("acreditacion_indap", "Acreditación"),
    )
    blocks = "\n".join(
        (
            f'  - id: "{document_id}"\n'
            f'    titulo: "{title}"\n'
            '    fuente: "INDAP"\n'
            f'    fuente_url: "{source_url}"\n'
            '    fecha: "2026-07-29"\n'
            "    chunks:\n"
            f'      - texto: "{summary}"'
        )
        for document_id, title in documents
    )
    path.write_text(
        (f'version: 1\nverificado_el: "2026-07-29"\nrevisar_antes_de: "{review_before}"\ndocumentos:\n{blocks}\n'),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "query",
    [
        "Necesito un crédito de INDAP para comprar semillas",
        "¿Qué financiamiento tiene INDAP?",
        "¿Hay préstamos agrícolas?",
        "Cuéntame del PDI",
    ],
)
def test_detecta_intencion_financiera_explicita(query: str) -> None:
    """Crédito y sus sinónimos reciben una derivación determinista."""
    response = get_indap_credit_referral(query, today=_VERIFICATION_DATE)

    assert response is not None
    assert "INDAP" in response


def test_consulta_no_financiera_no_es_interceptada() -> None:
    """Una consulta normal de precio continúa por su pipeline habitual."""
    assert get_indap_credit_referral("¿A cuánto está la papa?", today=_VERIFICATION_DATE) is None


@pytest.mark.parametrize(
    "query",
    [
        "¿Qué tarjeta de crédito me conviene?",
        "Necesito un crédito hipotecario",
        "Necesito un hipotecario",
        "Busco un crédito de consumo",
    ],
)
def test_diferencia_creditos_no_agricolas(query: str) -> None:
    """Productos financieros genéricos se declaran fuera de alcance."""
    response = get_indap_credit_referral(query, today=_VERIFICATION_DATE)

    assert response is not None
    assert "no entrega información" in response
    assert "Corto Plazo" not in response
    assert "359" not in response


@pytest.mark.parametrize(
    "query",
    [
        "¿Califico para un crédito INDAP?",
        "¿Qué crédito me conviene?",
        "¿Cuánto debería pedir prestado?",
    ],
)
def test_no_evalua_elegibilidad_ni_recomienda(query: str) -> None:
    """Preguntas de decisión reciben límite explícito y derivación humana."""
    response = get_indap_credit_referral(query, today=_VERIFICATION_DATE)

    assert response is not None
    assert "no puede decir si calificas" in response
    assert "ni recomendar" in response
    assert all(term not in response for term in ("RUT", "ingresos", "tus deudas"))


def test_fuentes_del_snapshot_real_son_https_indap_y_vigentes() -> None:
    """La respuesta solo expone URLs del dominio oficial validado."""
    response = get_indap_credit_referral(
        "Información sobre crédito INDAP",
        today=_VERIFICATION_DATE,
    )

    assert response is not None
    urls = re.findall(r"https://[^\s]+", response)
    assert urls
    assert all(urlsplit(url).hostname == "www.indap.gob.cl" for url in urls)
    assert "29/07/2026" in response


def test_snapshot_real_solo_entrega_hechos_verificados() -> None:
    """Los instrumentos específicos no reintroducen las cifras retiradas."""
    short_term = get_indap_credit_referral(
        "Explícame el crédito de corto plazo",
        today=_VERIFICATION_DATE,
    )
    pdi = get_indap_credit_referral(
        "Explícame el PDI",
        today=_VERIFICATION_DATE,
    )

    assert short_term is not None
    assert "359 días" in short_term
    assert pdi is not None
    assert "No es un crédito" in pdi
    forbidden = ("200 UF", "3,5%", "100 UF", "6 meses", "80%", "3.500 UF")
    assert all(value not in short_term + pdi for value in forbidden)


def test_snapshot_vencido_falla_cerrado_sin_entregar_detalles() -> None:
    """Al pasar revisar_antes_de se ocultan plazos y programas."""
    response = get_indap_credit_referral(
        "¿Cómo es el crédito de corto plazo?",
        today=date(2026, 8, 29),
    )

    assert response is not None
    assert "información financiera desactualizada" in response
    assert "359" not in response
    assert "Corto Plazo" not in response


def test_dominio_no_oficial_falla_cerrado(tmp_path: Path) -> None:
    """Un YAML manipulado no puede convertir un enlace externo en fuente."""
    catalog_path = tmp_path / "indap_creditos.yaml"
    _write_catalog(catalog_path, source_url="https://indap.example.com/credito")

    response = get_indap_credit_referral(
        "crédito INDAP",
        today=_VERIFICATION_DATE,
        corpus_path=catalog_path,
    )

    assert response is not None
    assert "información financiera desactualizada" in response
    assert "example.com" not in response


def test_yaml_roto_falla_cerrado(tmp_path: Path) -> None:
    """Un snapshot ilegible conserva una derivación oficial mínima."""
    catalog_path = tmp_path / "indap_creditos.yaml"
    catalog_path.write_text("documentos: [", encoding="utf-8")

    response = get_indap_credit_referral(
        "financiamiento INDAP",
        today=_VERIFICATION_DATE,
        corpus_path=catalog_path,
    )

    assert response is not None
    assert "información financiera desactualizada" in response
    assert "https://www.indap.gob.cl/" in response


def test_resumen_con_monto_o_solicitud_de_pii_falla_cerrado(
    tmp_path: Path,
) -> None:
    """Una edición futura insegura del corpus nunca se vocaliza al agricultor."""
    catalog_path = tmp_path / "indap_creditos.yaml"
    unsafe_text = "Te recomendamos pedir 200 UF y envía tu RUT."
    _write_catalog(
        catalog_path,
        source_url="https://www.indap.gob.cl/credito",
        summary=unsafe_text,
    )

    response = get_indap_credit_referral(
        "crédito INDAP",
        today=_VERIFICATION_DATE,
        corpus_path=catalog_path,
    )

    assert response is not None
    assert "información financiera desactualizada" in response
    assert "200 UF" not in response
    assert "RUT" not in response
