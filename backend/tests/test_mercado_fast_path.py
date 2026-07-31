"""Regresion: mercado nombrado en la consulta usa fast-path, no LLM.

Antes "precio de la papa en la Vega Central" caia al LLM (~25s) porque el
atajo no leia el mercado de la consulta y respondia Lo Valledor. Ahora:

1. ``_extract_mercado_from_query`` saca el alias hablado.
2. ``get_price_for_llm`` resuelve por substring si el nombre ODEPA es largo.
3. ``_puede_usar_fast_path`` permite la consulta (ya no la bloquea).
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.odepa_price import OdepaPrice
from app.schemas.variables import ExtractedVariables
from app.services.llm_keywords import (
    _extract_mercado_from_query,
    _force_keyword_tool,
)
from app.services.odepa_service import get_price_for_llm
from app.services.pipeline_service import AgroVozPipeline


def _insertar(
    db: Session,
    *,
    producto: str = "papa",
    mercado: str = "Lo Valledor",
    precio_kg: Decimal = Decimal("1200"),
) -> None:
    db.add(
        OdepaPrice(
            producto=producto,
            mercado=mercado,
            precio_kg=precio_kg,
            unidad="$/kilo",
            fecha=datetime.date(2026, 7, 20),
            fuente="test",
        )
    )
    db.commit()


class TestExtractMercado:
    def test_vega_central(self) -> None:
        assert _extract_mercado_from_query("papa en la Vega Central") == "vega central"

    def test_lo_valledor(self) -> None:
        assert _extract_mercado_from_query("precio en Lo Valledor") == "valledor"

    def test_sin_mercado_especifico(self) -> None:
        assert _extract_mercado_from_query("a cuanto esta la papa") is None

    def test_mercado_generico_no_cuenta(self) -> None:
        """'en el mercado' a secas no es un mercado ODEPA nombrado."""
        assert _extract_mercado_from_query("papa en el mercado") is None


class TestGetPriceSubstring:
    """Alias hablado resuelve el nombre largo de ODEPA."""

    def test_valledor_alias_encuentra_nombre_completo(self, db: Session) -> None:
        _insertar(
            db,
            mercado="Mercado Mayorista Lo Valledor de Santiago",
            precio_kg=Decimal("8833.33"),
        )
        texto = get_price_for_llm(db, "papa", "valledor")
        assert "8.833" in texto
        assert "Valledor" in texto

    def test_vega_central_alias(self, db: Session) -> None:
        _insertar(db, mercado="Vega Central", precio_kg=Decimal("1500"))
        _insertar(
            db,
            mercado="Mercado Mayorista Lo Valledor de Santiago",
            precio_kg=Decimal("2000"),
        )
        texto = get_price_for_llm(db, "papa", "vega central")
        assert "1.500" in texto
        assert "Vega Central" in texto


class TestFastPathConMercado:
    def test_gate_permite_vega_central(self) -> None:
        extracted = ExtractedVariables(producto="papa", consulta_tipo="precio")
        assert AgroVozPipeline._puede_usar_fast_path(
            "precio de la papa en la Vega Central", extracted, None
        )

    async def test_force_keyword_pasa_mercado(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core import database as db_module
        from app.services import odepa_service

        calls: list[dict[str, str]] = []

        class _FakeSession:
            def close(self) -> None:
                pass

        def _capture_price(
            session: object,
            producto: str,
            mercado: str = "",
            phone_hash: str | None = None,
        ) -> str:
            calls.append({"producto": producto, "mercado": mercado})
            return "Papa está a 1.500 pesos el kilo en Vega Central, según ODEPA."

        monkeypatch.setattr(db_module, "SessionLocal", lambda: _FakeSession())
        monkeypatch.setattr(odepa_service, "get_price_for_llm", _capture_price)

        resp = await _force_keyword_tool(
            "precio de la papa en la Vega Central",
            phone_hash="a" * 64,
        )
        assert resp is not None
        assert "Vega Central" in resp
        assert calls == [{"producto": "papa", "mercado": "vega central"}]
