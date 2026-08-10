"""Tests para extracción tipada de variables (issue #191)."""

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.variables import ExtractedVariables
from app.services.pipeline_service import AgroVozPipeline


class TestExtractedVariablesSchema:
    """Tests del schema Pydantic ExtractedVariables."""

    def test_defaults_correctos(self) -> None:
        """ExtractedVariables debe tener defaults razonables."""
        v = ExtractedVariables()
        assert v.producto is None
        assert v.mercado is None
        assert v.ubicacion == "Traiguén"
        assert v.consulta_tipo == "desconocido"
        assert v.urgencia is None

    def test_campos_opcionales(self) -> None:
        """Los campos producto, mercado y urgencia deben aceptar None."""
        v = ExtractedVariables(producto=None, mercado=None, urgencia=None)
        assert v.producto is None
        assert v.mercado is None
        assert v.urgencia is None

    def test_consulta_tipo_valores_validos(self) -> None:
        """consulta_tipo solo acepta valores del Literal."""
        for tipo in ("precio", "clima", "agronomica", "ambos", "desconocido"):
            v = ExtractedVariables(consulta_tipo=tipo)  # type: ignore[arg-type]
            assert v.consulta_tipo == tipo

    def test_consulta_tipo_invalido_rechazado(self) -> None:
        """consulta_tipo debe rechazar valores fuera del Literal."""
        with pytest.raises(ValidationError):
            ExtractedVariables(consulta_tipo="invalido")  # type: ignore[arg-type]

    def test_producto_con_valor(self) -> None:
        """producto debe aceptar strings válidos."""
        v = ExtractedVariables(producto="papa")
        assert v.producto == "papa"

    def test_ubicacion_default_traiguen(self) -> None:
        """La ubicación por defecto debe ser Traiguén."""
        v = ExtractedVariables()
        assert v.ubicacion == "Traiguén"


class TestExtractVariablesDegradacion:
    """Tests de degradación del método _extract_variables."""

    def test_flag_off_usa_keyword_matching(self) -> None:
        """Consulta de precio detecta producto y consulta_tipo='precio'."""
        result = AgroVozPipeline._extract_variables(
            "precio de la papa en Santiago",
        )
        assert isinstance(result, ExtractedVariables)
        assert result.producto == "papa"
        assert result.consulta_tipo == "precio"

    def test_detecta_producto(self) -> None:
        """Debe detectar producto por keyword."""
        result = AgroVozPipeline._extract_variables("¿a cómo está la papa?")
        assert result.producto == "papa"
        assert result.consulta_tipo == "precio"

    def test_producto_no_encontrado(self) -> None:
        """Sin producto ni keywords reconocibles: producto None, tipo desconocido."""
        result = AgroVozPipeline._extract_variables("hola buenos días")
        assert result.producto is None
        assert result.consulta_tipo == "desconocido"

    def test_consulta_clima(self) -> None:
        """Consulta de clima infiere consulta_tipo='clima'."""
        result = AgroVozPipeline._extract_variables(
            "¿va a llover mañana en Traiguén?",
        )
        assert result.consulta_tipo == "clima"

    def test_consulta_ambos(self) -> None:
        """Consulta mixta (precio + clima) infiere consulta_tipo='ambos'."""
        result = AgroVozPipeline._extract_variables(
            "¿a cuánto la papa y cómo va a estar el clima?",
        )
        assert result.consulta_tipo == "ambos"

    def test_keyword_precio_sin_producto(self) -> None:
        """Keyword de precio sin producto explícito igual infiere 'precio'."""
        result = AgroVozPipeline._extract_variables("¿cuánto cuesta el kilo?")
        assert result.consulta_tipo == "precio"

    def test_consulta_calendario_es_agronomica(self) -> None:
        """El nombre del cultivo no deriva una consulta de precio por sí solo."""
        result = AgroVozPipeline._extract_variables("¿cuándo siembro trigo en Traiguén?")

        assert result.producto == "trigo"
        assert result.consulta_tipo == "agronomica"

    def test_pedido_de_calendario_sin_verbo_es_agronomico(self) -> None:
        result = AgroVozPipeline._extract_variables("calendario agrícola del trigo en Traiguén")

        assert result.producto == "trigo"
        assert result.consulta_tipo == "agronomica"

    @pytest.mark.parametrize(
        ("query", "expected_type"),
        [
            ("a cuanto esta la cosecha de trigo", "precio"),
            ("como esta el clima para la cosecha", "clima"),
        ],
    )
    def test_gate_agronomico_apagado_no_tapa_precio_ni_clima(
        self,
        monkeypatch: pytest.MonkeyPatch,
        query: str,
        expected_type: str,
    ) -> None:
        """Una keyword agronómica no debe secuestrar una consulta normal."""
        monkeypatch.setattr(settings, "agronomic_rules_enabled", False)

        result = AgroVozPipeline._extract_variables(query)

        assert result.consulta_tipo == expected_type

    def test_gate_agronomico_habilitado_conserva_precedencia(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Con reglas activas, una consulta mixta sigue siendo agronómica."""
        monkeypatch.setattr(settings, "agronomic_rules_enabled", True)

        result = AgroVozPipeline._extract_variables("a cuanto esta la cosecha de trigo")

        assert result.consulta_tipo == "agronomica"
