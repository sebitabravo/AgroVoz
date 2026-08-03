"""Pruebas del catálogo determinista de programas INDAP en La Araucanía."""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlsplit

import pytest

from app.services.indap_credit_service import get_programas_indap

_CATALOG_DATE = date(2026, 8, 3)


def test_consulta_motocultivador_deriva_sin_recomendar() -> None:
    """Una inversión concreta muestra hechos de PDI y crédito de inversión."""
    response = get_programas_indap(
        "¿Qué subsidio o crédito hay para comprar un motocultivador?",
        today=_CATALOG_DATE,
    )

    assert "Programa de Desarrollo de Inversiones" in response
    assert "Crédito Largo Plazo" in response
    assert "Riveros #1059" in response
    assert "no puede decir si calificas" in response
    assert "te conviene" not in response.lower()


@pytest.mark.parametrize(
    ("consulta", "programa"),
    [
        ("¿Cómo pido un crédito INDAP?", "Crédito Corto Plazo"),
        ("¿Qué apoyo entrega PRODESAL?", "Programa de Desarrollo Local"),
        ("¿Hay financiamiento verde?", "Transición a la Agricultura Sostenible"),
    ],
)
def test_consulta_nombra_programa_y_conserva_fuente(
    consulta: str,
    programa: str,
) -> None:
    """Los alias de voz se resuelven al documento oficial correspondiente."""
    response = get_programas_indap(consulta, today=_CATALOG_DATE)

    assert programa in response
    assert "Fuente oficial INDAP:" in response
    urls = re.findall(r"https://[^\s]+", response)
    assert urls
    assert all(urlsplit(url.rstrip(".,;:!?)]}")).hostname == "www.indap.gob.cl" for url in urls)


def test_catalogo_vencido_falla_cerrado() -> None:
    """No se vocalizan programas cuando el snapshot supera su revisión."""
    response = get_programas_indap("programas INDAP", today=date(2026, 9, 3))

    assert "catálogo local no está vigente" in response
    assert "Programa de Desarrollo de Inversiones" not in response
    assert "Riveros #1059" in response


def test_credito_fuera_de_alcance_no_se_deriva_a_programas() -> None:
    """Una consulta hipotecaria conserva el límite de financiamiento agrícola."""
    response = get_programas_indap("¿Qué programa hipotecario ofrece INDAP?")

    assert "no entrega información sobre tarjetas, hipotecarios" in response
    assert "Programa de Desarrollo de Inversiones" not in response
