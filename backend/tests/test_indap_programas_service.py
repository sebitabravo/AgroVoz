"""Pruebas del catálogo determinista de programas INDAP en La Araucanía."""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlsplit

import pytest

from app.services.indap_credit_service import (
    _LIMITS_TEXT,
    format_indap_response_for_voice,
    get_programas_indap,
    is_programas_indap_query,
)

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


@pytest.mark.parametrize(
    "query",
    [
        "Necesito asesoría sobre una plaga",
        "¿Cómo hago agricultura sostenible?",
        "Busco una alianza para vender papas",
        "¿Qué maquinaria sirve para sembrar?",
    ],
)
def test_marcadores_ambiguos_no_secuestran_consultas(query: str) -> None:
    """Sin contexto INDAP/programa, los términos genéricos siguen al pipeline normal."""
    assert is_programas_indap_query(query) is False


def test_resumen_general_pide_aclaracion_y_audio_no_lee_urls() -> None:
    """La respuesta genérica no enumera cuatro fichas ni vocaliza enlaces."""
    response = get_programas_indap("¿Qué programas tiene INDAP?", today=_CATALOG_DATE)
    voice = format_indap_response_for_voice(response)

    assert "Dime cuál" in response
    assert len(voice) <= 600
    assert "https://" not in voice


def test_audio_truncado_conserva_el_descargo_completo() -> None:
    """El recorte a 600 caracteres nunca se come el descargo obligatorio.

    Regresión: una respuesta larga (motocultivador, ~1000 chars) truncaba el
    audio en medio del catálogo, antes de llegar al descargo que va al final
    del texto — el agricultor escuchaba detalle de créditos sin la aclaración
    de que AgroVoz no evalúa elegibilidad ni recomienda montos.
    """
    response = get_programas_indap(
        "¿Qué subsidio o crédito hay para comprar un motocultivador?",
        today=_CATALOG_DATE,
    )
    assert len(response) > 600  # confirma que el caso realmente ejercita el truncado

    voice = format_indap_response_for_voice(response)

    assert len(voice) <= 600
    assert _LIMITS_TEXT in voice


@pytest.mark.parametrize(
    "consulta",
    [
        "soy elegible para el SAT?",
        "que monto me dan en el PRODESAL",
    ],
)
def test_pregunta_de_elegibilidad_no_devuelve_ficha_de_programa(consulta: str) -> None:
    """get_programas_indap rechaza preguntas de elegibilidad/monto, no las responde.

    Regresión: el fast-path de programas corre antes que get_indap_credit_referral
    en el pipeline y no revisaba _ADVICE_MARKERS, así que "soy elegible para el
    SAT" devolvía la ficha del programa en vez de la negativa explícita — rozando
    evaluar elegibilidad o recomendar un instrumento (decisión #21).
    """
    response = get_programas_indap(consulta, today=_CATALOG_DATE)

    assert "no puede decir si calificas" in response
    assert "Fuente oficial INDAP:" not in response
