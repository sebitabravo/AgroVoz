"""Pruebas deterministas de procedencia del corpus agronómico."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.services.agricultural_calendar_service import _load_catalog as load_calendar
from app.services.source_validation import parse_source_date, validate_source_url


@pytest.mark.parametrize(
    "url",
    [
        "https://enfermedadespapa.inia.cl/tizonTardio.php",
        "https://biblioteca.inia.cl/bitstream/handle/20.500.14001/4831/NR40902.pdf?sequence=1",
        "https://planpredial.inia.cl/media/documentos/trigo_invernal.pdf",
        "https://inia.cl/fuente",
        "https://biblioteca.inia.cl:443/fuente",
    ],
)
def test_url_oficial_https_es_aceptada(url: str) -> None:
    assert validate_source_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://enfermedadespapa.inia.cl/tizonTardio.php",
        "https://www.indap.gob.cl/plataforma-de-servicios/credito-corto-plazo",
        "https://www.odepa.gob.cl/publicaciones/boletines",
        "https://example.com/fuente-inventada",
        "https://inia.cl.evil.example/fuente",
        "https://inia.cl@evil.example/fuente",
        "https://user:secret@biblioteca.inia.cl/fuente",
        "https://biblioteca.inia.cl:8443/fuente",
        "https://biblioteca.inia.cl:not-a-port/fuente",
    ],
)
def test_url_no_oficial_o_insegura_es_rechazada(url: str) -> None:
    with pytest.raises(ValueError, match="fuente_url"):
        validate_source_url(url)


def test_fecha_futura_es_rechazada() -> None:
    with pytest.raises(ValueError, match="futura"):
        parse_source_date("2026-08-04", today=date(2026, 8, 3))


def test_fecha_no_iso_es_rechazada() -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_source_date("2024", today=date(2026, 8, 3))


def test_snapshots_reales_validan_url_fecha_y_vigencia() -> None:
    corpus_dir = Path(__file__).resolve().parents[1] / "corpus"
    today = date(2026, 8, 9)

    load_calendar(corpus_dir / "calendario_agricola.yaml", today)


def test_calendario_con_fuente_fuera_de_allowlist_falla_cerrado(tmp_path: Path) -> None:
    path = tmp_path / "calendario.yaml"
    path.write_text(
        """version: 1
verificado_el: "2026-08-01"
revisar_antes_de: "2027-01-01"
reglas:
  - id: "calendario-invalido"
    cultivo: "papa"
    alias: ["papa"]
    zona: "secano interior"
    siembra: "01/09 a 30/09"
    cosecha: "01/02 a 28/02"
    fuente: "Fuente no oficial"
    fuente_url: "https://example.com/fuente"
    fecha: "2026-08-01"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="allowlist"):
        load_calendar(path, date(2026, 8, 9))
