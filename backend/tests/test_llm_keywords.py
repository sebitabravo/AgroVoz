"""Tests para app.services.llm_keywords: detección de saludos y fuzzy matching.

Cobertura:
- _detect_greeting: saludos puros vs. consultas con pregunta.
- _extract_product_from_query: substring exacto y fuzzy match para typos.
- Tolerancia a variaciones de escritura (acentos, mayúsculas).

Sin dependencias externas (DB, LLM, red).
"""

import pytest

from app.services.llm_keywords import _detect_greeting, _extract_product_from_query


class TestDetectGreeting:
    """Tests para detección de saludos puros (sin pregunta real)."""

    def test_detects_simple_hello(self) -> None:
        """Detecta 'hola' como saludo puro."""
        assert _detect_greeting("hola") is True

    def test_detects_buenos_dias(self) -> None:
        """Detecta 'buenos días' como saludo puro."""
        assert _detect_greeting("buenos días") is True
        assert _detect_greeting("buenos dias") is True

    def test_detects_buenas_tardes(self) -> None:
        """Detecta 'buenas tardes' como saludo puro."""
        assert _detect_greeting("buenas tardes") is True

    def test_detects_buenas_noches(self) -> None:
        """Detecta 'buenas noches' como saludo puro."""
        assert _detect_greeting("buenas noches") is True

    def test_detects_alo_greeting(self) -> None:
        """Detecta 'aló' (saludo chileno) como saludo puro."""
        assert _detect_greeting("aló") is True
        assert _detect_greeting("alo") is True

    def test_detects_hello_english(self) -> None:
        """Detecta 'hi' como saludo puro."""
        assert _detect_greeting("hi") is True

    def test_ignores_greeting_with_product_question(self) -> None:
        """Ignora 'hola a cómo está la papa' (tiene pregunta de producto)."""
        assert _detect_greeting("hola a cómo está la papa") is False
        assert _detect_greeting("hola cuánto cuesta la papa") is False

    def test_ignores_greeting_with_price_keywords(self) -> None:
        """Ignora 'hola cuál es el precio' (tiene pregunta de precio)."""
        assert _detect_greeting("hola cuál es el precio") is False
        assert _detect_greeting("buenos días a cuánto está") is False

    def test_ignores_greeting_with_weather_keywords(self) -> None:
        """Ignora 'hola cómo está el clima' (tiene pregunta de clima)."""
        assert _detect_greeting("hola cómo está el clima") is False
        assert _detect_greeting("buenos días qué tiempo hace") is False

    def test_ignores_greeting_with_sale_quantity(self) -> None:
        """Ignora 'hola voy a vender 30 kilos' (tiene cantidad)."""
        assert _detect_greeting("hola voy a vender 30 kilos") is False
        assert _detect_greeting("buenos días tengo 50 kg de papa") is False

    def test_returns_false_on_empty(self) -> None:
        """Retorna False para texto vacío."""
        assert _detect_greeting("") is False
        assert _detect_greeting("   ") is False

    def test_returns_false_on_only_question(self) -> None:
        """Retorna False si es solo pregunta (sin saludo)."""
        assert _detect_greeting("cuánto cuesta") is False
        assert _detect_greeting("cuál es el precio") is False

    def test_case_insensitive(self) -> None:
        """Detección case-insensitive."""
        assert _detect_greeting("HOLA") is True
        assert _detect_greeting("HoLa") is True
        assert _detect_greeting("BUENOS DÍAS") is True

    def test_greeting_with_comma(self) -> None:
        """Detecta saludos con puntuación."""
        assert _detect_greeting("hola,") is True
        assert _detect_greeting("buenos días,") is True

    def test_multiple_greetings(self) -> None:
        """Detecta múltiples saludos repetidos como saludo puro."""
        assert _detect_greeting("hola hola") is True

    def test_greeting_with_product_name_regression(self) -> None:
        """Regresión: 'hola papa' NO es saludo puro (contiene producto).

        Antes del fix, esto retornaba True y cortaba el pipeline.
        Ahora debe retornar False para que el LLM/fallback extraiga el precio.
        """
        assert _detect_greeting("hola papa") is False
        assert _detect_greeting("buenos días tomate") is False
        assert _detect_greeting("hola lechuga") is False


class TestExtractProductFromQuery:
    """Tests para extracción de productos (substring exacto y fuzzy match)."""

    def test_extract_exact_product_simple(self) -> None:
        """Extrae producto por substring exacto: 'papa'."""
        assert _extract_product_from_query("a cuánto está la papa") == "papa"
        assert _extract_product_from_query("cuál es el precio de la papa") == "papa"

    def test_extract_exact_product_tomate(self) -> None:
        """Extrae producto exacto: 'tomate'."""
        assert _extract_product_from_query("tomate") == "tomate"
        assert _extract_product_from_query("a cuánto el tomate") == "tomate"

    def test_extract_accented_product(self) -> None:
        """Extrae producto con acento: 'sandía'."""
        result = _extract_product_from_query("cuánto cuesta la sandía")
        assert result == "sandía"

    def test_extract_prioritizes_longer_match(self) -> None:
        """Prioriza substring más largo ('pimentón' antes que 'pimiento')."""
        # Solo si ambos estuvieran en _COMMON_PRODUCTS
        result = _extract_product_from_query("pimentón rojo")
        assert result == "pimentón"

    def test_fuzzy_match_typo_celga_to_acelga(self) -> None:
        """Fuzzy match detecta typo 'celga' como 'acelga' (cutoff 0.75)."""
        result = _extract_product_from_query("celga")
        assert result == "acelga"

    def test_fuzzy_match_typo_in_sentence(self) -> None:
        """Fuzzy match detecta typo en oración: 'a cuanto la celga'."""
        result = _extract_product_from_query("a cuanto cuesta la celga")
        assert result == "acelga"

    def test_fuzzy_match_tolerates_single_char_diff(self) -> None:
        """Fuzzy match tolera diferencias de 1-2 caracteres."""
        # "pap" debería matchear "papa" (3 chars → 4 chars, ~0.75)
        result = _extract_product_from_query("pap")
        assert result == "papa"

    def test_no_match_returns_none(self) -> None:
        """Retorna None si no hay producto ni typo cercano."""
        assert _extract_product_from_query("qué tal") is None
        assert _extract_product_from_query("buenos días") is None

    def test_empty_query_returns_none(self) -> None:
        """Retorna None para consulta vacía."""
        assert _extract_product_from_query("") is None
        assert _extract_product_from_query("   ") is None

    def test_case_insensitive_extraction(self) -> None:
        """Extracción case-insensitive."""
        assert _extract_product_from_query("PAPA") == "papa"
        assert _extract_product_from_query("PaPa") == "papa"
        assert _extract_product_from_query("TOMATE") == "tomate"

    def test_fuzzy_match_respects_cutoff(self) -> None:
        """Fuzzy match rechaza matches con baja similitud (< 0.75)."""
        # "xyz" no debería matchear ningún producto (< 0.75).
        result = _extract_product_from_query("xyz")
        assert result is None

    def test_extract_multiple_products_returns_first(self) -> None:
        """Si hay múltiples productos, retorna el primero (substring exacto)."""
        # Ordenamiento es por largo desc, así que debería haber prioridad.
        result = _extract_product_from_query("papa y tomate")
        # Ambos son substring exactos, pero _COMMON_PRODUCTS tiene orden
        # y se retorna el primero encontrado (por largo descendente).
        assert result in ("papa", "tomate")

    def test_fuzzy_match_short_word_minimum(self) -> None:
        """Fuzzy match ignora palabras muy cortas (< 3 chars)."""
        # "la" (2 chars) no debería fuzzy-matchear.
        result = _extract_product_from_query("la")
        assert result is None

    def test_exact_match_priority_over_fuzzy(self) -> None:
        """Substring exacto se detecta primero (sin fuzzy)."""
        # "papa" es exacto, no necesita fuzzy.
        result = _extract_product_from_query("papa")
        assert result == "papa"

    def test_fuzzy_match_with_accent_variations(self) -> None:
        """Fuzzy match maneja variaciones de acentos (ej: 'sandia' → 'sandía')."""
        # Si el usuario escribe "sandia" (sin acento) debería fuzzy-matchear.
        result = _extract_product_from_query("sandia")
        # Pueden haber dos entradas: "sandía" (con acento) y "sandia" (sin).
        # Dependiendo del orden, puede retornar una u otra.
        # El punto es que no retorne None.
        assert result is not None
        assert result in ("sandía", "sandia")
