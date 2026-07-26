"""Tests de normalizacion de texto para sintesis de voz.

El productor no lee: el audio es la unica salida del producto. Si Piper deletrea
una unidad ("eme barra ese" en vez de "metros por segundo"), el dato se pierde.
Estos tests fijan las sustituciones verificadas contra el modelo real
es_MX-claude-high y, sobre todo, protegen los casos que NO hay que tocar.
"""

from app.services.tts_service import normalizar_para_voz


class TestUnidadesQuePiperDeletrea:
    """Abreviaturas que el fonemizador lee letra por letra."""

    def test_metros_por_segundo(self) -> None:
        assert "metros por segundo" in normalizar_para_voz("viento 3.94 m/s")
        assert "m/s" not in normalizar_para_voz("viento 3.94 m/s")

    def test_milimetros(self) -> None:
        assert normalizar_para_voz("12 mm de lluvia") == "12 milímetros de lluvia"

    def test_kilometros_por_hora(self) -> None:
        assert normalizar_para_voz("viento de 20 km/h") == "viento de 20 kilómetros por hora"

    def test_kilometros_por_hora_gana_sobre_kilometros(self) -> None:
        """km/h debe resolverse antes que km para no dejar 'kilómetros/h'."""
        assert "/" not in normalizar_para_voz("a 20 km/h")

    def test_kilos(self) -> None:
        assert normalizar_para_voz("100 kg de papa") == "100 kilos de papa"


class TestFechas:
    """dd/mm/aaaa se lee con 'barra' si no se reescribe."""

    def test_fecha_a_palabras(self) -> None:
        assert normalizar_para_voz("precio del 17/07/2026") == "precio del 17 de julio de 2026"

    def test_fecha_de_un_digito(self) -> None:
        assert normalizar_para_voz("el 1/1/2026") == "el 1 de enero de 2026"

    def test_mes_invalido_queda_intacto(self) -> None:
        """Ante algo que no es fecha, no inventar."""
        assert normalizar_para_voz("codigo 45/99/2026") == "codigo 45/99/2026"


class TestLoQueNoSeDebeTocar:
    """Regresiones: Piper ya resuelve bien estos casos."""

    def test_porcentaje_intacto(self) -> None:
        assert normalizar_para_voz("humedad 83%") == "humedad 83%"

    def test_miles_con_punto_intacto(self) -> None:
        assert normalizar_para_voz("13.000 pesos") == "13.000 pesos"

    def test_grados_celsius_intacto(self) -> None:
        assert normalizar_para_voz("12°C") == "12°C"

    def test_ha_como_verbo_no_se_convierte(self) -> None:
        """'ha' auxiliar es mucho mas frecuente que la hectarea: no se toca."""
        texto = "No ha llovido y ha subido el precio."
        assert normalizar_para_voz(texto) == texto

    def test_palabras_que_contienen_las_abreviaturas(self) -> None:
        """Los limites de palabra evitan destrozar palabras normales."""
        texto = "La hamaca y los kilogramos de la comuna."
        assert normalizar_para_voz(texto) == texto


class TestRespuestasReales:
    """Frases tal como las emiten las tools del pipeline."""

    def test_respuesta_de_clima(self) -> None:
        crudo = "En Traiguén ahora: 12°C, nublado, humedad 83%, viento 3.94 m/s, según OpenMeteo."
        salida = normalizar_para_voz(crudo)
        assert "metros por segundo" in salida
        assert "83%" in salida
        assert "12°C" in salida

    def test_respuesta_de_precio(self) -> None:
        crudo = "Papa está a 13.000 pesos por saco de 25 kilos, según ODEPA, precio del 17/07/2026."
        salida = normalizar_para_voz(crudo)
        assert "17 de julio de 2026" in salida
        assert "13.000" in salida
