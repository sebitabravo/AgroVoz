"""Pruebas unitarias e integración del directorio agrícola por comuna."""

import datetime

import pytest

from app.models.directorio_agricola import DirectorioAgricola
from app.services.directorio_agricola_service import get_directorio_agricola
from app.services.llm_keywords import _force_keyword_tool
from app.services.llm_service import _execute_tool


def _sede(
    *,
    tipo: str = "indap",
    comuna: str = "Traiguén",
    direccion: str | None = "Riveros #1059, Traiguén",
    telefono: str | None = "45 250 6151",
) -> DirectorioAgricola:
    """Construye una sede determinista para los escenarios de prueba."""
    return DirectorioAgricola(
        tipo=tipo,
        comuna=comuna,
        nombre=f"Sede {tipo} {comuna}",
        direccion=direccion,
        telefono=telefono,
        horario="Lunes a viernes de 8:30 a 17:00",
        fuente="Fuente oficial de prueba",
        fuente_url="https://oficial.example/directorio",
        fecha_fuente="2026-08-03",
        verificado_el=datetime.date(2026, 8, 3),
    )


def test_directorio_filtra_comuna_sin_exigir_tilde(db) -> None:
    """Una comuna escrita sin tilde encuentra el contacto oficial."""
    db.add_all([_sede(), _sede(comuna="Victoria")])
    db.commit()

    respuesta = get_directorio_agricola(db, "traiguen", "INDAP")

    assert "Sede indap Traiguén" in respuesta
    assert "Riveros #1059" in respuesta
    assert "45 250 6151" in respuesta
    assert "Fuente oficial de prueba" in respuesta
    assert "Sede indap Victoria" not in respuesta


def test_directorio_declara_contactos_ausentes_en_vez_de_inventarlos(db) -> None:
    """Los registros abiertos sin contacto se responden de forma explícita."""
    db.add(_sede(tipo="cooperativa", direccion=None, telefono=None))
    db.commit()

    respuesta = get_directorio_agricola(db, "Traiguén", "cooperativa")

    assert "Dirección: no publicado en la fuente oficial." in respuesta
    assert "Teléfono oficial: no publicado en la fuente oficial." in respuesta


def test_directorio_valida_tipo_y_comuna(db) -> None:
    """Los filtros inválidos no se convierten en una consulta amplia."""
    assert "Necesito el nombre" in get_directorio_agricola(db, "   ")
    assert "debe ser INDAP" in get_directorio_agricola(db, "Traiguén", "municipio")


@pytest.mark.asyncio
async def test_whitelist_dispatcha_directorio_con_sesion_sqlite(db) -> None:
    """La tool anunciada por el LLM llega al servicio y devuelve la sede."""
    db.add(_sede())
    db.commit()

    respuesta = await _execute_tool(
        "get_directorio_agricola",
        {"comuna": "TRAIGUÉN", "tipo": "indap"},
    )

    assert "Sede indap Traiguén" in respuesta
    assert "45 250 6151" in respuesta


@pytest.mark.asyncio
async def test_fallback_de_keywords_resuelve_directorio_de_texto(db) -> None:
    """Una consulta escrita o transcrita funciona aunque el LLM no llame tools."""
    db.add(_sede())
    db.commit()

    respuesta = await _force_keyword_tool("¿Dónde queda INDAP en Traiguén?")

    assert respuesta is not None
    assert "Riveros #1059" in respuesta
    assert "45 250 6151" in respuesta
