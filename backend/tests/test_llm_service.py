"""Tests para app.services.llm_service: Tool Calling, whitelist, mock mode.

Cobertura: constantes (SYSTEM_PROMPT, FALLBACK_TEXT, WHITELIST_TOOLS),
parseo de respuestas (_parse_tool_calls, _parse_content), construccion
de mensajes (_build_messages), mock_answer con keywords, answer() sin modelo,
funciones utilitarias (is_model_available, reset_model, get_model_error),
y _execute_tool con whitelist enforcement.

Sin modelo real: todos los tests corren en CI sin llama-cpp-python ni GGUF.
"""

import pytest

from app.services.llm_service import (
    FALLBACK_TEXT,
    MAX_TOOL_ITERATIONS,
    NO_RESPONSE_TEXT,
    SYSTEM_PROMPT,
    TOOLS,
    WHITELIST_TOOLS,
    _build_messages,
    _execute_tool,
    _mock_answer,
    _parse_content,
    _parse_tool_calls,
    answer,
    get_model_error,
    is_model_available,
    reset_model,
)

# ── Constantes ──────────────────────────────────────────────────


class TestConstantes:
    """Verifica que las constantes del modulo no se modifiquen accidentalmente."""

    def test_system_prompt_contiene_reglas_estrictas(self) -> None:
        """El system prompt debe contener las 6 reglas del issue #18."""
        assert "REGLAS ESTRICTAS" in SYSTEM_PROMPT
        assert "Tienes DOS herramientas" in SYSTEM_PROMPT
        assert "NUNCA das recomendaciones" in SYSTEM_PROMPT
        assert "NUNCA inventas precios" in SYSTEM_PROMPT
        assert "español chileno" in SYSTEM_PROMPT
        assert "pesos chilenos" in SYSTEM_PROMPT

    def test_fallback_text_no_vacio(self) -> None:
        """El texto de fallback es un mensaje informativo no vacio."""
        assert len(FALLBACK_TEXT) > 20
        assert "ODEPA" in FALLBACK_TEXT
        assert "clima" in FALLBACK_TEXT

    def test_no_response_text_no_vacio(self) -> None:
        """El texto de no-respuesta pide reformular."""
        assert len(NO_RESPONSE_TEXT) > 10
        assert "reformular" in NO_RESPONSE_TEXT.lower()

    def test_whitelist_solo_dos_tools(self) -> None:
        """La whitelist solo permite get_price y get_weather."""
        assert frozenset({"get_price", "get_weather"}) == WHITELIST_TOOLS

    def test_tools_definition_formato_openai(self) -> None:
        """Las tool definitions siguen el formato OpenAI function-calling."""
        assert len(TOOLS) == 2
        for tool in TOOLS:
            assert tool["type"] == "function"
            fn = tool["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn
            assert fn["name"] in WHITELIST_TOOLS  # type: ignore[index]

    def test_max_tool_iterations_razonable(self) -> None:
        """El maximo de iteraciones del loop debe ser >= 1 y <= 10."""
        assert 1 <= MAX_TOOL_ITERATIONS <= 10


# ── Parseo de respuestas ────────────────────────────────────────


class TestParseToolCalls:
    """_parse_tool_calls extrae tool calls de respuestas del LLM."""

    def test_respuesta_con_tool_calls(self) -> None:
        """Extrae tool calls cuando la respuesta las incluye."""
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": '{"lat": -38.23, "lon": -72.68}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
        result = _parse_tool_calls(response)
        assert len(result) == 1
        assert result[0]["function"]["name"] == "get_weather"

    def test_respuesta_sin_tool_calls(self) -> None:
        """Retorna lista vacia cuando no hay tool calls."""
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "El precio de la papa es $1.200.",
                    }
                }
            ]
        }
        result = _parse_tool_calls(response)
        assert result == []

    def test_respuesta_vacia(self) -> None:
        """Retorna lista vacia con respuesta sin choices."""
        assert _parse_tool_calls({}) == []
        assert _parse_tool_calls({"choices": []}) == []

    def test_tool_calls_no_es_lista(self) -> None:
        """Retorna lista vacia si tool_calls no es una lista."""
        response = {
            "choices": [
                {
                    "message": {
                        "tool_calls": "no_soy_lista",
                    }
                }
            ]
        }
        assert _parse_tool_calls(response) == []

    def test_respuesta_con_dos_tool_calls(self) -> None:
        """Soporta multiples tool calls en una respuesta."""
        response = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "get_price",
                                    "arguments": '{"producto": "papa", "mercado": "Lo Valledor"}',
                                },
                            },
                            {
                                "id": "call_2",
                                "type": "function",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": '{"lat": -38.23, "lon": -72.68}',
                                },
                            },
                        ]
                    }
                }
            ]
        }
        result = _parse_tool_calls(response)
        assert len(result) == 2


class TestParseContent:
    """_parse_content extrae texto de contenido de respuestas del LLM."""

    def test_respuesta_con_contenido(self) -> None:
        """Extrae el texto de contenido normalmente."""
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "La papa está a $1.200 el kilo.",
                    }
                }
            ]
        }
        assert _parse_content(response) == "La papa está a $1.200 el kilo."

    def test_respuesta_sin_contenido(self) -> None:
        """Retorna cadena vacia si no hay contenido."""
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                    }
                }
            ]
        }
        assert _parse_content(response) == ""

    def test_respuesta_vacia(self) -> None:
        """Retorna cadena vacia con respuesta sin choices."""
        assert _parse_content({}) == ""
        assert _parse_content({"choices": []}) == ""

    def test_respuesta_stripped(self) -> None:
        """Elimina whitespace alrededor del contenido."""
        response = {
            "choices": [
                {
                    "message": {
                        "content": "  Hola mundo  ",
                    }
                }
            ]
        }
        assert _parse_content(response) == "Hola mundo"


# ── Construccion de mensajes ────────────────────────────────────


class TestBuildMessages:
    """_build_messages construye la lista de mensajes para el LLM."""

    def test_mensaje_base_sin_historial(self) -> None:
        """Primer mensaje es system prompt, ultimo es el usuario."""
        messages = _build_messages("¿Cuál es el precio de la papa?", [])
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == SYSTEM_PROMPT
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "¿Cuál es el precio de la papa?"

    def test_mensaje_con_historial(self) -> None:
        """El historial se inserta entre system y user."""
        history: list[dict[str, object]] = [
            {"role": "user", "content": "Hola"},
            {"role": "assistant", "content": "¡Hola! ¿En qué te ayudo?"},
        ]
        messages = _build_messages("Quiero saber el clima", history)
        assert len(messages) == 4
        assert messages[0]["role"] == "system"
        assert messages[1] == history[0]
        assert messages[2] == history[1]
        assert messages[3]["role"] == "user"
        assert messages[3]["content"] == "Quiero saber el clima"


# ── Mock answer ─────────────────────────────────────────────────


class TestMockAnswer:
    """_mock_answer responde por keyword sin modelo LLM."""

    def test_keyword_clima(self) -> None:
        """Detecta intencion de clima por keywords."""
        result = _mock_answer("¿Cómo está el clima en Traiguén?")
        assert "Traiguén" in result
        assert "°C" in result
        assert "humedad" in result

    def test_keyword_temperatura(self) -> None:
        """'temperatura' dispara respuesta de clima."""
        result = _mock_answer("¿Qué temperatura hace hoy?")
        assert "°C" in result

    def test_keyword_lluvia(self) -> None:
        """'lluvia' dispara respuesta de clima."""
        result = _mock_answer("¿Hay lluvia para mañana?")
        assert "lluvia" in result.lower()

    def test_keyword_precio(self) -> None:
        """Detecta intencion de precio por keyword 'precio'."""
        result = _mock_answer("¿Cuál es el precio de la papa?")
        assert "papa" in result.lower() or "Papa" in result
        assert "$" in result
        assert "kilo" in result

    def test_keyword_cuanto_cuesta(self) -> None:
        """'cuánto cuesta' dispara respuesta de precio."""
        result = _mock_answer("¿Cuánto cuesta la cebolla?")
        assert "$" in result

    def test_fuera_de_scope(self) -> None:
        """Consulta fuera de scope retorna FALLBACK_TEXT."""
        result = _mock_answer("¿Debo regar mis papas hoy?")
        assert result == FALLBACK_TEXT

    def test_query_vacia(self) -> None:
        """Query sin keywords reconocibles retorna fallback."""
        result = _mock_answer("hola buenos días")
        assert result == FALLBACK_TEXT

    def test_keywords_insensibles_a_mayusculas(self) -> None:
        """Deteccion case-insensitive."""
        result = _mock_answer("CLIMA EN SANTIAGO")
        assert "°C" in result


# ── answer() sin modelo (mock path) ─────────────────────────────


class TestAnswerMockPath:
    """answer() usa _mock_answer cuando no hay modelo LLM cargado."""

    async def test_answer_sin_modelo_clima(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sin modelo, answer() delega en _mock_answer para clima."""
        monkeypatch.setattr("app.services.llm_service._get_model", lambda: None)
        result = await answer("¿Cómo está el clima?")
        assert "°C" in result

    async def test_answer_sin_modelo_precio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sin modelo, answer() delega en _mock_answer para precio."""
        monkeypatch.setattr("app.services.llm_service._get_model", lambda: None)
        result = await answer("¿Cuál es el precio de la papa?")
        assert "$" in result

    async def test_answer_sin_modelo_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sin modelo, query fuera de scope retorna fallback."""
        monkeypatch.setattr("app.services.llm_service._get_model", lambda: None)
        result = await answer("¿Debo regar?")
        assert result == FALLBACK_TEXT

    async def test_answer_query_vacia(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Query vacia o solo whitespace retorna NO_RESPONSE_TEXT."""
        monkeypatch.setattr("app.services.llm_service._get_model", lambda: None)
        assert await answer("") == NO_RESPONSE_TEXT
        assert await answer("   ") == NO_RESPONSE_TEXT

    async def test_answer_query_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Query None (type checker bypass) tratado como texto 'None'."""
        monkeypatch.setattr("app.services.llm_service._get_model", lambda: None)
        # Si alguien pasa None, str(None) = "None" que cae en fallback
        result = await answer("None")
        # "None" no matchea keywords, asi que cae en fallback
        assert result == FALLBACK_TEXT

    async def test_answer_con_historial_vacio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """answer con historial vacio funciona igual."""
        monkeypatch.setattr("app.services.llm_service._get_model", lambda: None)
        result = await answer("clima en Traiguén", history=[])
        assert "°C" in result


# ── Funciones utilitarias ───────────────────────────────────────


class TestUtilidades:
    """is_model_available, get_model_error, reset_model."""

    def test_is_model_available_sin_modelo(self) -> None:
        """Sin modelo cargado, is_model_available retorna False."""
        reset_model()
        assert is_model_available() is False

    def test_get_model_error_inicial(self) -> None:
        """Antes de intentar cargar, get_model_error retorna None."""
        reset_model()
        assert get_model_error() is None

    def test_reset_model_idempotente(self) -> None:
        """reset_model se puede llamar multiples veces sin error."""
        reset_model()
        reset_model()
        assert is_model_available() is False
        assert get_model_error() is None


# ── Whitelist enforcement en _execute_tool ──────────────────────


class TestExecuteToolWhitelist:
    """_execute_tool rechaza tools fuera del whitelist."""

    async def test_tool_no_whitelisteada(self) -> None:
        """Tool fuera del whitelist retorna FALLBACK_TEXT."""
        result = await _execute_tool("get_advisory", {"topic": "riego"})
        assert result == FALLBACK_TEXT

    async def test_tool_nombre_vacio(self) -> None:
        """Tool con nombre vacio retorna FALLBACK_TEXT."""
        result = await _execute_tool("", {})
        assert result == FALLBACK_TEXT

    async def test_tool_desconocida_case_sensitive(self) -> None:
        """Nombres similares pero no exactos son rechazados."""
        result = await _execute_tool("Get_Price", {"producto": "papa", "mercado": "Lo Valledor"})
        assert result == FALLBACK_TEXT

    async def test_tool_get_price_en_whitelist_se_ejecuta(self) -> None:
        """get_price esta en whitelist, por lo tanto NO retorna FALLBACK_TEXT.

        A diferencia de las tools no-whitelisteadas, get_price intenta ejecutarse.
        El resultado concreto depende de si hay DB con datos, pero nunca es
        FALLBACK_TEXT (que solo se retorna para tools fuera del whitelist).
        """
        result = await _execute_tool("get_price", {"producto": "papa", "mercado": "Lo Valledor"})
        # No debe ser FALLBACK_TEXT: get_price SI esta en el whitelist.
        assert result != FALLBACK_TEXT
