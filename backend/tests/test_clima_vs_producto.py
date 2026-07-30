"""Regresion: consultas de clima que devolvian precio de un producto.

Dos bugs reales encontrados probando la demo interactiva:

1. "¿va a llover mañana?" respondia el PRECIO DE LA MANZANA. El fuzzy match
   de productos resolvia "mañana" -> "manzana" (ratio 0.769 contra un cutoff
   de 0.75; sin tilde, como transcribe Whisper, "manana" da 0.923). Como el
   bloque de precio corre ANTES que el de clima, ganaba el precio y el clima
   nunca se evaluaba.

2. "llover" no estaba en las keywords de clima —solo "lluvia" y "lloviendo"—,
   asi que la forma mas natural de preguntar no matcheaba nada y la consulta
   caia al LLM, que en 1 vCPU termina en timeout.

Subir el cutoff del fuzzy NO era opcion: "manana"/"manzana" (0.923) puntua mas
alto que typos legitimos como "celga"/"acelga" (0.909).
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.llm_keywords import (
    _PALABRAS_NO_PRODUCTO,
    _extract_product_from_query,
    _force_keyword_tool,
)


class TestPalabrasComunesNoSonProductos:
    """El fuzzy match tolera typos, no adivina productos donde no los hay."""

    def test_manana_no_es_manzana(self) -> None:
        assert _extract_product_from_query("¿va a llover mañana?") is None

    def test_manana_sin_tilde_tampoco(self) -> None:
        """Whisper transcribe sin tilde: es el caso que llega en produccion."""
        assert _extract_product_from_query("va a llover manana") is None

    def test_semana_no_es_producto(self) -> None:
        assert _extract_product_from_query("¿cómo viene la semana?") is None

    def test_para_no_es_pera(self) -> None:
        assert _extract_product_from_query("crédito para semillas") is None

    def test_habla_no_es_haba(self) -> None:
        assert _extract_product_from_query("documento que habla de crédito") is None

    def test_las_palabras_comunes_estan_listadas(self) -> None:
        for palabra in ("mañana", "manana", "semana", "para", "habla"):
            assert palabra in _PALABRAS_NO_PRODUCTO


class TestNoSeRompioLaDeteccionReal:
    """El filtro no puede costar la deteccion legitima de productos."""

    def test_manzana_de_verdad_se_detecta(self) -> None:
        """Substring exacto: corre antes del fuzzy, no lo afecta el filtro."""
        assert _extract_product_from_query("¿a cuánto está la manzana?") == "manzana"

    def test_typo_celga_sigue_resolviendo_a_acelga(self) -> None:
        assert _extract_product_from_query("a cuanto la celga") == "acelga"

    def test_typo_tomat_sigue_resolviendo_a_tomate(self) -> None:
        assert _extract_product_from_query("cuanto esta el tomat") == "tomate"

    def test_papa_se_detecta(self) -> None:
        assert _extract_product_from_query("¿a cuánto está la papa?") == "papa"


class TestRuteoDeClima:
    """Una pregunta de clima se responde con clima, no con un precio."""

    async def test_va_a_llover_manana_no_devuelve_precio(self) -> None:
        """El bug original: esta consulta devolvia el precio de la manzana."""
        with patch(
            "app.services.weather_service.get_pronostico",
            new_callable=AsyncMock,
            return_value="Mañana en Traiguén: máxima de 14 grados. Según OpenMeteo.",
        ):
            resp = await _force_keyword_tool("¿va a llover mañana?", phone_hash="a" * 64)

        assert resp is not None
        assert "pesos" not in resp.lower(), "una pregunta de clima no puede devolver un precio"
        assert "Traiguén" in resp

    async def test_llover_dispara_el_bloque_de_clima(self) -> None:
        """El verbo faltaba en las keywords; solo estaban los sustantivos."""
        with patch(
            "app.services.weather_service.get_pronostico",
            new_callable=AsyncMock,
            return_value="pronostico",
        ) as mock_pron:
            await _force_keyword_tool("¿va a llover?", phone_hash="a" * 64)
        mock_pron.assert_awaited()

    async def test_futuro_usa_pronostico_no_clima_actual(self) -> None:
        """'¿va a llover manana?' pide pronostico, no como esta ahora."""
        with (
            patch(
                "app.services.weather_service.get_pronostico",
                new_callable=AsyncMock,
                return_value="pronostico",
            ) as mock_pron,
            patch(
                "app.services.weather_service.get_weather",
                new_callable=AsyncMock,
                return_value="clima actual",
            ) as mock_actual,
        ):
            await _force_keyword_tool("¿va a llover mañana?", phone_hash="a" * 64)

        mock_pron.assert_awaited()
        mock_actual.assert_not_awaited()

    async def test_presente_usa_clima_actual_no_pronostico(self) -> None:
        with (
            patch(
                "app.services.weather_service.get_pronostico",
                new_callable=AsyncMock,
                return_value="pronostico",
            ) as mock_pron,
            patch(
                "app.services.weather_service.get_weather",
                new_callable=AsyncMock,
                return_value="clima actual",
            ) as mock_actual,
        ):
            await _force_keyword_tool("¿cómo está el tiempo?", phone_hash="a" * 64)

        mock_actual.assert_awaited()
        mock_pron.assert_not_awaited()

    async def test_helada_se_entiende_como_clima(self) -> None:
        """La helada es la consulta critica del productor antes de una noche fria."""
        with patch(
            "app.services.weather_service.get_pronostico",
            new_callable=AsyncMock,
            return_value="pronostico",
        ) as mock_pron:
            await _force_keyword_tool("¿va a helar mañana?", phone_hash="a" * 64)
        mock_pron.assert_awaited()

    async def test_comuna_nombrada_se_pasa_al_pronostico(self) -> None:
        """'tiempo en Temuco mañana' no debe defaultar a Traiguén."""
        with patch(
            "app.services.weather_service.get_pronostico",
            new_callable=AsyncMock,
            return_value="Mañana en Temuco: ...",
        ) as mock_pron:
            await _force_keyword_tool(
                "¿va a llover mañana en Temuco?", phone_hash="a" * 64
            )
        mock_pron.assert_awaited_once()
        assert mock_pron.await_args is not None
        assert mock_pron.await_args.args[0] == "Temuco"

    async def test_clima_actual_en_temuco_usa_coords_de_temuco(self) -> None:
        with patch(
            "app.services.weather_service.get_weather",
            new_callable=AsyncMock,
            return_value="En Temuco ahora: ...",
        ) as mock_actual:
            await _force_keyword_tool("¿cómo está el tiempo en Temuco?")
        mock_actual.assert_awaited_once()
        assert mock_actual.await_args is not None
        kwargs = mock_actual.await_args.kwargs
        # Temuco: (-38.74, -72.59), no Traiguén (-38.23, -72.68)
        assert kwargs["lat"] == pytest.approx(-38.74)
        assert kwargs["lon"] == pytest.approx(-72.59)
