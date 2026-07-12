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
    _TOOLS_SECTION,
    _VENTA_KILOS_RE,
    FALLBACK_TEXT,
    MAX_TOOL_ITERATIONS,
    NO_RESPONSE_TEXT,
    SYSTEM_PROMPT,
    TOOLS,
    WHITELIST_TOOLS,
    _build_messages,
    _execute_tool,
    _filter_handler_args,
    _force_keyword_tool,
    _mock_answer,
    _parse_content,
    _parse_text_tool_calls,
    _parse_tool_calls,
    _strip_tool_tags,
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
        assert "Tienes CUATRO herramientas" in SYSTEM_PROMPT
        assert "get_price_history" in SYSTEM_PROMPT
        assert "calculate_sale_value" in SYSTEM_PROMPT
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

    def test_whitelist_cuatro_tools(self) -> None:
        """La whitelist permite las cuatro tools de precio, venta y clima."""
        assert (
            frozenset(
                {
                    "get_price",
                    "get_price_history",
                    "calculate_sale_value",
                    "get_weather",
                }
            )
            == WHITELIST_TOOLS
        )

    def test_tools_definition_formato_openai(self) -> None:
        """Las tool definitions siguen el formato OpenAI function-calling."""
        assert len(TOOLS) == 4
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


# ── Parseo de <tool_call> desde texto (formato nativo Qwen2.5) ──


class TestParseTextToolCalls:
    """_parse_text_tool_calls extrae tool calls del texto de Qwen2.5."""

    def test_tool_call_simple(self) -> None:
        """Extrae un tool_call basico del texto."""
        content = (
            '<tool_call>\n'
            '{"name": "get_price", "arguments": {"producto": "papa", "mercado": "Lo Valledor"}}\n'
            '</tool_call>'
        )
        result = _parse_text_tool_calls(content)
        assert len(result) == 1
        assert result[0]["function"]["name"] == "get_price"
        assert "papa" in result[0]["function"]["arguments"]

    def test_tool_call_con_texto_adyacente(self) -> None:
        """Ignora texto alrededor del tool_call."""
        content = (
            'Voy a consultar el precio para ti.\n'
            '<tool_call>\n'
            '{"name": "get_price", "arguments": {"producto": "tomate", "mercado": "La Vega"}}\n'
            '</tool_call>\n'
            'Un momento por favor.'
        )
        result = _parse_text_tool_calls(content)
        assert len(result) == 1
        assert result[0]["function"]["name"] == "get_price"

    def test_sin_tool_call(self) -> None:
        """Retorna lista vacia si no hay <tool_call>."""
        assert _parse_text_tool_calls("Hola, cómo estás?") == []

    def test_content_vacio(self) -> None:
        """Retorna lista vacia con contenido vacio."""
        assert _parse_text_tool_calls("") == []
        assert _parse_text_tool_calls(None) == []  # type: ignore[arg-type]

    def test_json_invalido_dentro_de_tool_call(self) -> None:
        """JSON invalido dentro del tag no rompe el parseo."""
        content = (
            '<tool_call>\n'
            'esto no es json\n'
            '</tool_call>'
        )
        result = _parse_text_tool_calls(content)
        assert result == []

    def test_multiple_tool_calls(self) -> None:
        """Soporta multiples tool calls en un mismo texto."""
        content = (
            '<tool_call>\n'
            '{"name": "get_price", "arguments": {"producto": "papa", "mercado": "Lo Valledor"}}\n'
            '</tool_call>\n'
            '<tool_call>\n'
            '{"name": "get_weather", "arguments": {"lat": -38.23, "lon": -72.68}}\n'
            '</tool_call>'
        )
        result = _parse_text_tool_calls(content)
        assert len(result) == 2
        assert result[0]["function"]["name"] == "get_price"
        assert result[1]["function"]["name"] == "get_weather"


class TestStripToolTags:
    """_strip_tool_tags elimina tags XML residuales del texto."""

    def test_strip_tool_call_tag(self) -> None:
        """Elimina bloque <tool_call> completo."""
        text = 'Hola <tool_call>{"name": "test"}</tool_call> mundo'
        result = _strip_tool_tags(text)
        assert "<tool_call>" not in result
        assert "Hola" in result
        assert "mundo" in result

    def test_strip_tool_response_tag(self) -> None:
        """Elimina bloque <tool_response> completo."""
        text = '<tool_response>42</tool_response> La respuesta es 42'
        result = _strip_tool_tags(text)
        assert "<tool_response>" not in result
        assert "respuesta" in result

    def test_strip_tools_tag(self) -> None:
        """Elimina bloque <tools> completo."""
        text = 'Info <tools>{"fn": "x"}</tools> resto'
        result = _strip_tool_tags(text)
        assert "<tools>" not in result

    def test_strip_im_start_end(self) -> None:
        """Elimina tokens <|im_start|> y <|im_end|>."""
        text = '<|im_start|>assistant\nHola<|im_end|>'
        result = _strip_tool_tags(text)
        assert "<|im_start|>" not in result
        assert "<|im_end|>" not in result
        assert "Hola" in result

    def test_sin_tags(self) -> None:
        """Texto sin tags se mantiene igual."""
        text = "La papa está a $1.200 el kilo."
        assert _strip_tool_tags(text) == text

    def test_texto_vacio(self) -> None:
        """Texto vacio se mantiene vacio."""
        assert _strip_tool_tags("") == ""


# ── TOOLS section contien tools en formato nativo Qwen2.5 ───────


class TestToolsSection:
    """_TOOLS_SECTION incluye definiciones de tools en formato nativo."""

    def test_tools_section_contiene_xml_tools(self) -> None:
        """La seccion de tools usa formato <tools> XML."""
        assert "<tools>" in _TOOLS_SECTION
        assert "</tools>" in _TOOLS_SECTION
        assert "get_price" in _TOOLS_SECTION
        assert "get_weather" in _TOOLS_SECTION
        assert "calculate_sale_value" in _TOOLS_SECTION

    def test_tools_section_tiene_tool_call_example(self) -> None:
        """Incluye ejemplo de como hacer tool_call."""
        assert "<tool_call>" in _TOOLS_SECTION
        assert "<tool_response>" in _TOOLS_SECTION


# ── Construccion de mensajes ────────────────────────────────────


class TestBuildMessages:
    """_build_messages construye la lista de mensajes para el LLM."""

    def test_mensaje_base_sin_historial(self) -> None:
        """Primer mensaje es system prompt + tools, ultimo es el usuario."""
        messages = _build_messages("¿Cuál es el precio de la papa?", [])
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert SYSTEM_PROMPT in str(messages[0]["content"])
        assert "<tools>" in str(messages[0]["content"])
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

    async def test_filter_descarta_args_alucinados(self) -> None:
        """Args no presentes en la firma del handler se descartan (regresión P1).

        El LLM alucina params extra (ej: 'unidad') que el handler no acepta.
        Sin _filter_handler_args, handler(**arguments) lanzaria TypeError porque
        get_price_for_llm tiene firma estricta sin **kwargs.
        """

        def _handler(producto: str, mercado: str = "") -> str:
            return f"{producto}@{mercado}"

        filtered = _filter_handler_args(_handler, {"producto": "papa", "unidad": "kilo"})
        assert filtered == {"producto": "papa"}

    async def test_filter_con_kwargs_acepta_todo(self) -> None:
        """Si el handler acepta **kwargs, no se filtra nada."""

        def _handler(**kwargs: object) -> str:
            return "ok"

        filtered = _filter_handler_args(_handler, {"producto": "papa", "unidad": "kilo"})
        assert filtered == {"producto": "papa", "unidad": "kilo"}

    async def test_execute_tool_no_lanza_typeerror_con_arg_extra(self) -> None:
        """get_price con arg alucinado 'unidad' no propaga TypeError (regresión P1).

        Sin fix, handler(session=..., producto=..., unidad=...) lanzaba TypeError
        porque get_price_for_llm no acepta 'unidad'. Con _filter_handler_args el
        arg se descarta antes de la llamada.
        """
        result = await _execute_tool("get_price", {"producto": "papa", "unidad": "kilo"})
        # No lanzo TypeError, y get_price SI esta en whitelist -> no es FALLBACK_TEXT.
        assert result != FALLBACK_TEXT


# ── Fallback keyword detection (_force_keyword_tool) ────────────


class TestForceKeywordToolDbError:
    """_force_keyword_tool no propaga errores de DB al pipeline.

    Regresión para P1 del CI review (commit 5adfd60): el bloque de precio
    llamaba get_price_for_llm sin asyncio.to_thread ni except SQLAlchemyError,
    crasheando el pipeline si SQLite lanzaba 'database is locked'.
    """

    async def test_precio_db_error_no_propaga(self, monkeypatch) -> None:
        """SQLAlchemyError en get_price_for_llm se captura y retorna None."""
        from sqlalchemy.exc import SQLAlchemyError

        from app.core import database as db_module
        from app.services import odepa_service

        class _FakeSession:
            def close(self) -> None:
                pass

        monkeypatch.setattr(db_module, "SessionLocal", lambda: _FakeSession())

        def _raise_db_error(session, producto):
            raise SQLAlchemyError("database is locked")

        monkeypatch.setattr(odepa_service, "get_price_for_llm", _raise_db_error)

        # La query menciona "papa" (producto) pero ningun keyword de clima,
        # asi que cae al None final sin tocar el bloque de clima.
        result = await _force_keyword_tool("a cuanto esta la papa")
        assert result is None


class TestVentaKilosRegex:
    """Regex determinista para deteccion de venta (Issue #104).

    El regex _VENTA_KILOS_RE captura "N kilos de <producto>" sin depender
    del LLM. El bloque va antes que el de precio en _force_keyword_tool.
    """

    def test_regex_captura_cantidad_kilos(self) -> None:
        match = _VENTA_KILOS_RE.search("voy a vender 30 kilos de papa")
        assert match is not None
        assert match.group(1) == "30"

    def test_regex_captura_kg_abreviatura(self) -> None:
        match = _VENTA_KILOS_RE.search("30 kg de tomate")
        assert match is not None
        assert match.group(1) == "30"

    def test_regex_captura_kilo_singular(self) -> None:
        match = _VENTA_KILOS_RE.search("1 kilo de papa")
        assert match is not None
        assert match.group(1) == "1"

    def test_regex_no_matchea_sin_cantidad(self) -> None:
        """'kilos de papa' sin numero no dispara el calculo de venta."""
        assert _VENTA_KILOS_RE.search("kilos de papa") is None

    def test_regex_no_matchea_sin_de(self) -> None:
        """'tengo 30 kilos' (sin 'de') no dispara venta (reduce falsos positivos)."""
        assert _VENTA_KILOS_RE.search("tengo 30 kilos") is None

    def test_regex_case_insensitive(self) -> None:
        assert _VENTA_KILOS_RE.search("VOY A VENDER 30 KILOS DE PAPA") is not None


class TestForceKeywordToolVenta:
    """_force_keyword_tool enruta 'N kilos de producto' a calculate_sale_value.

    Verifica que el fallback deterministico llame a la tool correcta con
    los argumentos correctos, sin depender del LLM.
    """

    async def test_venta_llama_calculate_sale_value(self, monkeypatch) -> None:
        """'voy a vender 30 kilos de papa' -> calculate_sale_value(papa, 30)."""
        from app.core import database as db_module
        from app.services import odepa_service

        calls: list[dict[str, str]] = []

        class _FakeSession:
            def close(self) -> None:
                pass

        def _capture_sale(session, producto, cantidad_kg, mercado=""):
            calls.append(
                {
                    "producto": producto,
                    "cantidad_kg": cantidad_kg,
                    "mercado": mercado,
                }
            )
            return (
                "Papa está a unos 850 pesos el kilo según ODEPA. "
                "Por 30 kilos recibirás unos 25.500 pesos como referencia mayorista."
            )

        monkeypatch.setattr(db_module, "SessionLocal", lambda: _FakeSession())
        monkeypatch.setattr(
            odepa_service, "calculate_sale_value_for_llm", _capture_sale
        )

        result = await _force_keyword_tool("voy a vender 30 kilos de papa")
        assert result is not None
        assert "25.500 pesos" in result
        assert calls == [
            {"producto": "papa", "cantidad_kg": "30", "mercado": ""}
        ]

    async def test_venta_kg_abreviatura(self, monkeypatch) -> None:
        """'30 kg de tomate' tambien enruta a calculate_sale_value."""
        from app.core import database as db_module
        from app.services import odepa_service

        calls: list[dict[str, str]] = []

        class _FakeSession:
            def close(self) -> None:
                pass

        def _capture_sale(session, producto, cantidad_kg, mercado=""):
            calls.append({"producto": producto, "cantidad_kg": cantidad_kg})
            return (
                "Tomate está a unos 1.000 pesos el kilo según ODEPA. "
                "Por 30 kilos recibirás unos 30.000 pesos como referencia mayorista."
            )

        monkeypatch.setattr(db_module, "SessionLocal", lambda: _FakeSession())
        monkeypatch.setattr(
            odepa_service, "calculate_sale_value_for_llm", _capture_sale
        )

        result = await _force_keyword_tool("30 kg de tomate")
        assert result is not None
        assert calls == [{"producto": "tomate", "cantidad_kg": "30"}]

    async def test_venta_sin_datos_caea_precio(self, monkeypatch) -> None:
        """Si calculate_sale_value dice 'no hay datos', cae al bloque de precio."""
        from app.core import database as db_module
        from app.services import odepa_service

        sale_calls: list[dict[str, str]] = []
        price_calls: list[dict[str, str]] = []

        class _FakeSession:
            def close(self) -> None:
                pass

        def _sale_no_data(session, producto, cantidad_kg, mercado=""):
            sale_calls.append({"producto": producto, "cantidad_kg": cantidad_kg})
            return "No tengo datos de precio para papa."

        def _price_ok(session, producto, mercado=""):
            price_calls.append({"producto": producto, "mercado": mercado})
            return "Papa está a 850 pesos el kilo en Lo Valledor."

        monkeypatch.setattr(db_module, "SessionLocal", lambda: _FakeSession())
        monkeypatch.setattr(odepa_service, "calculate_sale_value_for_llm", _sale_no_data)
        monkeypatch.setattr(odepa_service, "get_price_for_llm", _price_ok)

        result = await _force_keyword_tool("30 kilos de papa")
        # Cae al bloque de precio: get_price_for_llm fue llamado.
        assert len(sale_calls) == 1
        assert len(price_calls) == 1
        assert result is not None
        assert "850 pesos" in result

    async def test_venta_db_error_no_propaga(self, monkeypatch) -> None:
        """SQLAlchemyError en calculate_sale_value se captura y cae a precio."""
        from sqlalchemy.exc import SQLAlchemyError

        from app.core import database as db_module
        from app.services import odepa_service

        class _FakeSession:
            def close(self) -> None:
                pass

        def _raise_db_error(session, producto, cantidad_kg, mercado=""):
            raise SQLAlchemyError("database is locked")

        price_calls: list[dict[str, str]] = []

        def _price_ok(session, producto, mercado=""):
            price_calls.append({"producto": producto})
            return "Papa está a 850 pesos el kilo en Lo Valledor."

        monkeypatch.setattr(db_module, "SessionLocal", lambda: _FakeSession())
        monkeypatch.setattr(odepa_service, "calculate_sale_value_for_llm", _raise_db_error)
        monkeypatch.setattr(odepa_service, "get_price_for_llm", _price_ok)

        result = await _force_keyword_tool("30 kilos de papa")
        # Error en venta -> cae a precio, no propaga la excepción.
        assert len(price_calls) == 1
        assert result is not None
        assert "850 pesos" in result
