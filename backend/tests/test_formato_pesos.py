"""Regresion: formato de pesos hablados (auditoria #207, hallazgos 2.1 y 4).

Habia dos implementaciones de ``_formatear_pesos`` y ninguna daba el resultado
correcto:

- ``odepa_service`` verbalizaba los decimales de la fuente: ODEPA publica
  promedios con 4-6 decimales (7132.3529 en la base real) y el productor
  escuchaba "7.132 coma 35 pesos". El peso chileno no tiene centavos en
  circulacion.
- ``alert_service`` truncaba con ``int()``: un umbral de 14.999,9 se anunciaba
  como "14.999 pesos" en vez de "15.000". En una alerta de precio ese peso es
  justo el que define si se cumplio la condicion.

Ahora ambas delegan en ``app.core.formato.formatear_pesos``.
"""

from decimal import Decimal

from app.core.formato import formatear_pesos
from app.services.alert_service import _formatear_pesos as formatear_alertas
from app.services.odepa_service import _formatear_pesos as formatear_odepa


class TestRedondeoAlPeso:
    """CLP no tiene centavos: la cifra hablada va al peso entero."""

    def test_decimales_de_odepa_no_se_verbalizan(self) -> None:
        """El caso que se escuchaba en la demo."""
        assert formatear_pesos(Decimal("14232.14")) == "14.232 pesos"

    def test_precio_real_de_la_base(self) -> None:
        """Valor textual de la base de produccion."""
        assert formatear_pesos(Decimal("7132.3529")) == "7.132 pesos"

    def test_redondea_hacia_arriba_desde_medio(self) -> None:
        """ROUND_HALF_UP: 9760,8695 -> 9.761, no 9.760."""
        assert formatear_pesos(Decimal("9760.8695")) == "9.761 pesos"

    def test_no_trunca(self) -> None:
        """El bug de alert_service: int(14999.9) daba 14.999."""
        assert formatear_pesos(Decimal("14999.9")) == "15.000 pesos"

    def test_entero_queda_igual(self) -> None:
        assert formatear_pesos(Decimal("1200")) == "1.200 pesos"

    def test_nunca_dice_coma(self) -> None:
        for v in ("14232.14", "7132.3529", "0.5", "1150.50"):
            assert "coma" not in formatear_pesos(Decimal(v))

    def test_separador_de_miles_chileno(self) -> None:
        """Punto para miles, no coma."""
        assert formatear_pesos(Decimal("1234567")) == "1.234.567 pesos"

    def test_dice_pesos_y_no_simbolo(self) -> None:
        """Con "$" el LLM tiende a leerlo como dolares antes del TTS."""
        assert "$" not in formatear_pesos(Decimal("1200"))
        assert formatear_pesos(Decimal("1200")).endswith("pesos")

    def test_acepta_float_e_int(self) -> None:
        assert formatear_pesos(1200) == "1.200 pesos"
        assert formatear_pesos(1200.4) == "1.200 pesos"


class TestUnaSolaFuenteDeVerdad:
    """Las dos implementaciones duplicadas ahora delegan en la misma."""

    def test_odepa_y_alertas_dan_el_mismo_resultado(self) -> None:
        """Antes diferian: una verbalizaba centavos y la otra truncaba."""
        for valor in ("14999.9", "7132.3529", "1200", "14232.14"):
            d = Decimal(valor)
            assert formatear_odepa(d) == formatear_alertas(d) == formatear_pesos(d)
