"""Tests para app.services.llm_service: Tool Calling, whitelist, mock mode.

Cobertura: constantes (SYSTEM_PROMPT, FALLBACK_TEXT, WHITELIST_TOOLS),
parseo de respuestas (_parse_tool_calls, _parse_content), construccion
de mensajes (_build_messages), mock_answer con keywords, answer() sin modelo,
funciones utilitarias (is_model_available, reset_model, get_model_error),
y _execute_tool con whitelist enforcement.

Sin modelo real: todos los tests corren en CI sin llama-cpp-python ni GGUF.
"""

import pytest

from app.services.llm_keywords import _VENTA_KILOS_RE, _force_keyword_tool
from app.services.llm_service import (
    _N_CTX,
    _N_THREADS,
    _TOOLS_SECTION,
    FALLBACK_TEXT,
    MAX_TOOL_ITERATIONS,
    NO_RESPONSE_TEXT,
    SYSTEM_PROMPT,
    TOOLS,
    WHITELIST_TOOLS,
    _build_messages,
    _execute_tool,
    _filter_handler_args,
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
        """El system prompt comprimido conserva las 6 reglas del issue #18."""
        assert "REGLAS ESTRICTAS" in SYSTEM_PROMPT
        assert "Tienes CUATRO herramientas" in SYSTEM_PROMPT
        assert "get_price_history" in SYSTEM_PROMPT
        assert "calculate_sale_value" in SYSTEM_PROMPT
        assert "NUNCA recomendaciones agronomicas" in SYSTEM_PROMPT
        assert "NUNCA inventes precios" in SYSTEM_PROMPT
        assert "espanol chileno" in SYSTEM_PROMPT
        assert "pesos chilenos" in SYSTEM_PROMPT

    def test_system_prompt_instruye_conservar_cita_fuente(self) -> None:
        """Issue #95: el system prompt comprimido debe instruir conservar la
        mencion de la fuente (ODEPA / OpenMeteo) al reformular respuestas.

        Sin esta regla, el LLM tiende a resumir omitiendo la fuente, perdiendo
        el respaldo institucional del dato.
        """
        assert "CONSERVA" in SYSTEM_PROMPT
        assert "ODEPA" in SYSTEM_PROMPT
        assert "OpenMeteo" in SYSTEM_PROMPT
        assert "segun ODEPA" in SYSTEM_PROMPT
        assert "segun OpenMeteo" in SYSTEM_PROMPT

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


# ── Configuracion de latencia ──────────────────────────────────────


class TestLlmConfig:
    """Regresion: constantes de configuracion para latencia <15s (Issue B-14).

    Valores hardcodeados que impactan directamente el tiempo de inferencia
    en CPU (VPS CX43, 8 vCPU, sin GPU). Si alguien los modifica sin medir
    el impacto, estos tests fallan.
    """

    def test_n_ctx_leq_1024(self) -> None:
        """n_ctx no debe exceder 1024 para mantener latencia en CPU.

        Cada token de contexto suma al prefill. A 2048, el decode en CPU
        toma ~77s. A 1024 con prompt comprimido, el target es <15s E2E.
        """
        assert _N_CTX <= 1024, (
            f"_N_CTX={_N_CTX} excede el limite de 1024. "
            "Aumentar sin medir impacto degrada latencia a >60s en CPU."
        )

    def test_n_threads_minimo_4(self) -> None:
        """n_threads debe usar al menos 4 hilos para CPU moderna.

        En VPS CX43 (8 vCPU), usar menos de 4 hilos desperdicia capacidad
        de computo paralelo. cpu_count() puede retornar None en entornos
        restringidos (Docker sin --cpuset-cpus), cayendo a fallback 4.
        """
        assert _N_THREADS >= 4, (
            f"_N_THREADS={_N_THREADS} es menor a 4. "
            "Pocos hilos incrementan latencia de decode en CPU."
        )

    def test_n_threads_no_excede_16(self) -> None:
        """Limite superior para evitar oversubscription.

        Mas hilos que nucleos fisicos causa contention y degrada
        rendimiento. 16 es el doble de los 8 vCPU del CX43, margen
        para hyperthreading.
        """
        assert _N_THREADS <= 16, (
            f"_N_THREADS={_N_THREADS} muy alto. "
            "Oversubscription de hilos degrada inferencia."
        )

    def test_system_prompt_char_count_razonable(self) -> None:
        """El system prompt comprimido debe ser compacto (Issue B-14).

        Cada char sumado al system prompt incrementa el prefill del LLM.
        Original era ~2000 chars, comprimido debe ser menos.
        Limite: 1400 chars. Justificar aumento con datos de latencia.
        """
        assert len(SYSTEM_PROMPT) <= 1400, (
            f"SYSTEM_PROMPT={len(SYSTEM_PROMPT)} chars excede el limite "
            "de 1400. Comprime o justifica con datos de latencia."
        )

    def test_total_prompt_chars_under_limit(self) -> None:
        """El prompt total (system + tools) no debe exceder un limite.

        Para n_ctx=1024 con Qwen2.5 (3-5 chars/token), el prompt total
        deberia estar bajo ~5000 chars. Es un guard suave contra
        regresiones que inflan el contexto sin ajustar n_ctx.
        """
        total_chars = len(SYSTEM_PROMPT) + len(_TOOLS_SECTION)
        assert total_chars <= 5500, (
            f"Prompt total={total_chars} chars demasiado grande "
            f"para n_ctx={_N_CTX}. Reduce o aumenta n_ctx."
        )


# ── Parseo de respuestas ────────────────────────────────────────


class TestParseToolCalls:
    """_parse_tool_calls extrae tool calls de respuestas del LLM.

    Nota: estos tests verifican que la instrucción sobre conservar citas
    (ODEPA/OpenMeteo) existe en el SYSTEM_PROMPT, no el comportamiento E2E
    del LLM con modelo real. La garantía determinista de la cita viene del
    hardcode en format_price_text() y _format_weather() (ver test_prices_api
    línea 260 y test_weather_service línea 153). El system prompt es defensa
    en profundidad: refuerza que el LLM no borre la cita si reformula.
    """

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

        def _raise_db_error(session, producto, phone_hash=None):
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

        def _price_ok(session, producto, mercado="", phone_hash=None):
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

        def _price_ok(session, producto, mercado="", phone_hash=None):
            price_calls.append({"producto": producto})
            return "Papa está a 850 pesos el kilo en Lo Valledor."

        monkeypatch.setattr(db_module, "SessionLocal", lambda: _FakeSession())
        monkeypatch.setattr(odepa_service, "calculate_sale_value_for_llm", _raise_db_error)
        monkeypatch.setattr(odepa_service, "get_price_for_llm", _price_ok)

        await _force_keyword_tool("30 kilos de papa")
        # Error en venta -> cae a precio, no propaga la excepción.
        assert len(price_calls) == 1


# ── ToolResultCache ───────────────────────────────────────────────


class TestToolResultCache:
    """Cache de resultados de tools con TTL."""

    def test_cache_hit_retorna_resultado(self) -> None:
        """Segundo get con mismos params retorna el valor cacheado."""
        from app.services.llm_service import ToolResultCache
        cache = ToolResultCache(ttl_seconds=60)
        cache.set("get_price", "450 pesos el kilo", producto="papa", mercado="lo valledor")
        result = cache.get("get_price", producto="papa", mercado="lo valledor")
        assert result == "450 pesos el kilo"

    def test_cache_miss_retorna_none(self) -> None:
        """Params distintos retornan None."""
        from app.services.llm_service import ToolResultCache
        cache = ToolResultCache(ttl_seconds=60)
        assert cache.get("get_price", producto="tomate") is None

    def test_cache_expirado_retorna_none(self) -> None:
        """TTL vencido retorna None."""
        from app.services.llm_service import ToolResultCache
        # TTL=-1: expira inmediatamente (cualquier monotonic() > stored_at - 1)
        cache = ToolResultCache(ttl_seconds=-1)
        cache.set("get_weather", "10 grados", lat=-38.23, lon=-72.68)
        assert cache.get("get_weather", lat=-38.23, lon=-72.68) is None

    def test_clear_tool_result_cache_vacia_singleton(self) -> None:
        """Clear del singleton elimina todas las entradas."""
        from app.services.llm_service import _tool_cache
        _tool_cache.set("get_price", "850 pesos", producto="cebolla")
        assert _tool_cache.get("get_price", producto="cebolla") == "850 pesos"
        _tool_cache.clear()
        assert _tool_cache.get("get_price", producto="cebolla") is None

    @pytest.mark.asyncio
    async def test_execute_tool_cache_hit_skips_handler(self, monkeypatch) -> None:
        """Segundo llamado a _execute_tool con mismos params usa cache, no handler.

        Regression: si alguien rompe la integracion del cache en
        _execute_tool, el handler se llamaria dos veces.
        """
        from app.services.llm_service import _execute_tool, _tool_cache

        call_count = 0

        def fake_handler(**kwargs: object) -> str:
            nonlocal call_count
            call_count += 1
            return f"precio={kwargs.get('producto')}"

        # Reemplazar el handler en el dict de tools
        handlers = {"get_price": fake_handler}
        monkeypatch.setattr(
            "app.services.llm_service._get_tool_handlers",
            lambda: handlers,
        )

        # Limpiar cache antes del test
        _tool_cache.clear()

        # Primer llamado: debe ejecutar el handler (cache miss)
        r1 = await _execute_tool("get_price", {"producto": "papa"})
        assert call_count == 1
        assert "precio=papa" in r1

        # Segundo llamado mismos params: debe usar cache (NO llama handler)
        r2 = await _execute_tool("get_price", {"producto": "papa"})
        assert call_count == 1  # no incrementó
        assert r2 == r1

    @pytest.mark.asyncio
    async def test_execute_tool_error_no_se_cachea(self, monkeypatch) -> None:
        """Handler que lanza excepción: NO cachea el error, re-ejecuta handler.

        Si un error se cacheara, la segunda llamada retornaria el mensaje
        de error cacheado en vez de re-intentar la consulta real.
        """
        from app.services.llm_service import _execute_tool, _tool_cache

        call_count = 0

        def fake_handler_falla_primero(**kwargs: object) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("DB caida transitoria")
            return f"precio={kwargs.get('producto')}"

        handlers = {"get_price": fake_handler_falla_primero}
        monkeypatch.setattr(
            "app.services.llm_service._get_tool_handlers",
            lambda: handlers,
        )

        _tool_cache.clear()

        # Primer llamado: handler lanza excepción
        r1 = await _execute_tool("get_price", {"producto": "papa"})
        assert call_count == 1
        assert "error" in r1.lower()

        # Segundo llamado mismos params: DEBE re-ejecutar handler
        # (el error NO se cachea)
        r2 = await _execute_tool("get_price", {"producto": "papa"})
        assert call_count == 2  # se re-ejecutó
        assert "precio=papa" in r2
