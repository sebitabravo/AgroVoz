"""Steps BDD para la consulta de precio ODEPA — golden path del producto.

Ejercita get_price_for_llm contra datos reales sembrados en SQLite, la misma
función que llama el LLM vía tool calling.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from pytest_bdd import given, parsers, scenarios, then, when
from sqlalchemy.orm import Session

from app.models.odepa_price import OdepaPrice
from app.services.odepa_service import get_price_for_llm

scenarios("../features/consulta_precio.feature")


@pytest.fixture
def contexto() -> dict[str, object]:
    """Guarda la última respuesta del sistema entre steps Given/When/Then."""
    return {}


@given(parsers.parse('que ODEPA reportó "{producto}" en "{mercado}" a "{precio}" pesos el kilo el "{fecha}"'))
def _sembrar_precio(producto: str, mercado: str, precio: str, fecha: str, db: Session) -> None:
    db.add(
        OdepaPrice(
            producto=producto,
            mercado=mercado,
            precio_kg=Decimal(precio),
            unidad="kilo",
            fecha=datetime.date.fromisoformat(fecha),
        )
    )
    db.commit()


@when(parsers.parse('el agricultor pregunta el precio de "{producto}" en "{mercado}"'), target_fixture="contexto")
def _preguntar_con_mercado(producto: str, mercado: str, db: Session, contexto: dict[str, object]) -> dict[str, object]:
    contexto["respuesta"] = get_price_for_llm(db, producto, mercado)
    return contexto


@when(parsers.parse('el agricultor pregunta el precio de "{producto}" sin nombrar mercado'), target_fixture="contexto")
def _preguntar_sin_mercado(producto: str, db: Session, contexto: dict[str, object]) -> dict[str, object]:
    contexto["respuesta"] = get_price_for_llm(db, producto)
    return contexto


@then(parsers.parse('la respuesta menciona el precio "{precio}"'))
def _valida_precio_en_respuesta(precio: str, contexto: dict[str, object]) -> None:
    respuesta = str(contexto["respuesta"])
    precio_formateado = f"{int(precio):,}".replace(",", ".")
    assert precio_formateado in respuesta, f"'{precio_formateado}' no aparece en: {respuesta!r}"


@then("la respuesta explica que no hay datos, sin lanzar error")
def _valida_respuesta_sin_datos(contexto: dict[str, object]) -> None:
    respuesta = str(contexto["respuesta"])
    assert "no tengo datos" in respuesta.lower()
