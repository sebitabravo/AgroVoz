"""Tests para app.services.llm_service: Tool Calling, whitelist, mock mode.

Cobertura: constantes (SYSTEM_PROMPT, FALLBACK_TEXT, WHITELIST_TOOLS),
parseo de respuestas (_parse_tool_calls, _parse_content), construccion
de mensajes (_build_messages), mock_answer con keywords, answer() sin modelo,
funciones utilitarias (is_model_available, reset_model, get_model_error),
y _execute_tool con whitelist enforcement.

Sin modelo real: todos los tests corren en CI sin llama-cpp-python ni GGUF.
"""

import asyncio
import logging
import os
import threading
from unittest.mock import patch

import httpx
import pytest

from app.core.config import settings
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
    LlmBusyError,
    _build_messages,
    _execute_tool,
    _filter_handler_args,
    _mock_answer,
    _parse_content,
    _parse_text_tool_calls,
    _parse_tool_calls,
    _strip_tool_tags,
    answer,
    answer_via_openrouter,
    get_model_error,
    is_model_available,
    reset_model,
)
from app.services.llm_worker import LlmWorkerCrashedError

# ── Constantes ──────────────────────────────────────────────────


class TestConstantes:
    """Verifica que las constantes del modulo no se modifiquen accidentalmente."""

    def test_system_prompt_contiene_reglas_estrictas(self) -> None:
        """El system prompt estructurado conserva las reglas del issue #18 + #91."""
        assert "HERRAMIENTAS DISPONIBLES" in SYSTEM_PROMPT
        assert "get_price_history" in SYSTEM_PROMPT
        assert "calculate_sale_value" in SYSTEM_PROMPT
        assert "calculate_margin" in SYSTEM_PROMPT
        assert "search_corpus" in SYSTEM_PROMPT
        assert "register_expense" in SYSTEM_PROMPT
        assert "NUNCA recomendaciones" in SYSTEM_PROMPT
        assert "Crédito: deriva a INDAP, sin asesorar" in SYSTEM_PROMPT
        assert "NUNCA pidas datos personales" in SYSTEM_PROMPT
        assert "NUNCA inventes precios" in SYSTEM_PROMPT
        assert "Español chileno" in SYSTEM_PROMPT
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
        assert "según ODEPA" in SYSTEM_PROMPT
        assert "según OpenMeteo" in SYSTEM_PROMPT

    def test_fallback_text_no_vacio(self) -> None:
        """El texto de fallback es un mensaje informativo no vacio."""
        assert len(FALLBACK_TEXT) > 20
        assert "ODEPA" in FALLBACK_TEXT
        assert "clima" in FALLBACK_TEXT

    def test_no_response_text_no_vacio(self) -> None:
        """El texto de no-respuesta pide reformular."""
        assert len(NO_RESPONSE_TEXT) > 10
        assert "reformular" in NO_RESPONSE_TEXT.lower()

    def test_whitelist_diez_tools(self) -> None:
        """Whitelist: precio, spread, historico, venta, margen, clima actual,
        pronostico, clima historico, corpus y gastos (10 tools)."""
        assert (
            frozenset(
                {
                    "get_price",
                    "get_price_spread",
                    "get_price_history",
                    "calculate_sale_value",
                    "calculate_margin",
                    "get_weather",
                    "get_pronostico",
                    "get_clima_historico",
                    "search_corpus",
                    "register_expense",
                }
            )
            == WHITELIST_TOOLS
        )

    def test_tools_definition_formato_openai(self) -> None:
        """Las tool definitions siguen el formato OpenAI function-calling."""
        # 5 base + calculate_margin (#155) + search_corpus (#156)
        # + register_expense (#170) + get_price_spread (#171) + get_pronostico
        assert len(TOOLS) == 10
        for tool in TOOLS:
            assert tool["type"] == "function"
            fn = tool["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn
            assert fn["name"] in WHITELIST_TOOLS  # type: ignore[index]

    def test_tool_apagada_por_gate_no_se_ofrece(self) -> None:
        """Fail-closed también significa no anunciar la tool (#170)."""
        from app.services.llm_service import _offered_tools, _tool_names

        with patch.object(settings, "expense_tracking_enabled", False):
            assert "register_expense" not in _tool_names(_offered_tools())
        with patch.object(settings, "expense_tracking_enabled", True):
            assert "register_expense" in _tool_names(_offered_tools())

    def test_seccion_de_tools_omite_la_tool_apagada(self) -> None:
        """El prefijo del prompt no gasta chars en una tool deshabilitada."""
        from app.services.llm_service import _render_tools_section

        with patch.object(settings, "expense_tracking_enabled", False):
            apagada = _render_tools_section()
        with patch.object(settings, "expense_tracking_enabled", True):
            encendida = _render_tools_section()

        assert '"name": "register_expense"' not in apagada
        assert '"name": "register_expense"' in encendida
        assert len(apagada) < len(encendida)

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

    def test_n_ctx_alcanza_para_prompt_con_siete_tools(self) -> None:
        """n_ctx debe cubrir el prompt real: system+tools (~2771 tokens

        medidos con el tokenizer real de Qwen2.5) + tool_response de RAG
        (peor caso, ~360 tokens) + margen para query/respuesta.

        n_ctx=1024 y 2048 NO alcanzaban ni para el primer prompt (crash
        ValueError instantaneo de llama-cpp-python). n_ctx=3072 alcanzaba
        para la 1a llamada pero no para la 2a vuelta del loop con
        tool_response de search_corpus inyectado. 4096 es el minimo medido
        que no revienta con las 7 tools actuales.

        RIESGO SIN VALIDAR EN VPS (gate #100, overrideado): medido en
        Apple M3 con Metal (mejor caso, no representativo del VPS CX43 sin
        GPU) da ~46s solo LLM (36s + 10s) vs target <15s E2E total. Si
        alguien sube mas este valor, medir de nuevo en el VPS antes de
        asumir que la latencia sigue siendo aceptable.
        """
        assert _N_CTX >= 4096, (
            f"_N_CTX={_N_CTX} no alcanza para el prompt con 7 tools "
            "(~2771 tokens) + tool_response de RAG (~3171 tokens en la "
            "2a vuelta). Medir tokens reales con el tokenizer antes de bajarlo."
        )

    def test_n_threads_sigue_a_los_cores_sin_sobresuscribir(self) -> None:
        """n_threads = un hilo por core real, nunca mas.

        El contrato anterior forzaba un piso de 4, pero en el peor caso
        soportado (1 vCPU) eso lanzaba 4 hilos sobre 1 core: se pelean el
        mismo core y la inferencia va mas lenta que con 1 solo. Ahora sigue
        a cpu_count(). cpu_count() puede ser None en entornos restringidos
        (Docker sin --cpuset-cpus), cayendo a un fallback de 4.
        """
        esperado = min(os.cpu_count() or 4, 8)
        assert esperado == _N_THREADS, f"_N_THREADS={_N_THREADS} deberia ser min(cpu_count, 8)={esperado}."

    def test_n_threads_al_menos_1(self) -> None:
        """Nunca cero hilos: llama.cpp necesita al menos uno."""
        assert _N_THREADS >= 1

    def test_n_threads_no_excede_8(self) -> None:
        """Tope de 8: mas hilos que los 8 vCPU del CX43 solo agrega
        contention de scheduler sin beneficio para un 3B en CPU."""
        assert _N_THREADS <= 8, f"_N_THREADS={_N_THREADS} muy alto. Oversubscription de hilos degrada inferencia."

    def test_system_prompt_char_count_razonable(self) -> None:
        """El system prompt estructurado debe ser compacto (Issue #190).

        Reorganizado en 6 secciones desde el bloque plano anterior.
        La sección HERRAMIENTAS solo tiene intent detection, no definiciones
        (las definiciones están en _TOOLS_SECTION para no duplicar).
        Limite: 2500 chars.
        """
        assert len(SYSTEM_PROMPT) <= 2500, (
            f"SYSTEM_PROMPT={len(SYSTEM_PROMPT)} chars excede el limite "
            "de 2500. Comprime o justifica con datos de latencia."
        )

    def test_total_prompt_chars_under_limit(self) -> None:
        """El prompt total (system + tools) no debe exceder un limite.

        Guard barato (no requiere cargar el modelo) contra regresiones que
        inflan el prompt sin querer. Ratio medido ~3.26 chars/token con el
        tokenizer de Qwen2.5 (ver test_n_ctx_alcanza_para_prompt_con_siete_tools).

        Con 10 tools (se sumo get_pronostico) son ~10359 chars ≈ ~3178 tokens.
        Sumado al peor caso de tool_response (~360 tokens de search_corpus) da
        ~3538, y deja ~558 tokens de margen dentro de n_ctx=4096 para la query
        y la respuesta — que esta capada en max_tokens=128. Entra con holgura.

        Ademas este es el PEOR caso: la seccion completa solo se manda cuando
        el intent es 'desconocido' o 'ambos'. Con el subset por intent, una
        consulta de clima manda 4 tools (~1677 tokens), la mitad.

        Limite en 10400 (margen de ~40 chars sobre el valor actual) para seguir
        detectando crecimiento no intencional. Si se agrega otra tool, apretar
        descripciones antes que subir este numero: cada token del prefijo se
        paga en prompt eval, que es el cuello en 1 vCPU.
        """
        total_chars = len(SYSTEM_PROMPT) + len(_TOOLS_SECTION)
        assert total_chars <= 10400, (
            f"Prompt total={total_chars} chars demasiado grande para n_ctx={_N_CTX}. Reduce o aumenta n_ctx."
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
            "<tool_call>\n"
            '{"name": "get_price", "arguments": {"producto": "papa", "mercado": "Lo Valledor"}}\n'
            "</tool_call>"
        )
        result = _parse_text_tool_calls(content)
        assert len(result) == 1
        assert result[0]["function"]["name"] == "get_price"
        assert "papa" in result[0]["function"]["arguments"]

    def test_tool_call_con_texto_adyacente(self) -> None:
        """Ignora texto alrededor del tool_call."""
        content = (
            "Voy a consultar el precio para ti.\n"
            "<tool_call>\n"
            '{"name": "get_price", "arguments": {"producto": "tomate", "mercado": "La Vega"}}\n'
            "</tool_call>\n"
            "Un momento por favor."
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

    def test_json_invalido_dentro_de_tool_call(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """JSON inválido se descarta sin registrar su fragmento."""
        secret = "rut-secreto-11.111.111-1"
        content = f'<tool_call>\n{{"name": "get_price", "arguments": {{"nota": "{secret}"}},}}\n</tool_call>'
        caplog.set_level(logging.WARNING, logger="app.services.llm_service")

        result = _parse_text_tool_calls(content)

        assert result == []
        assert secret not in caplog.text
        assert "JSONDecodeError" in caplog.text

    def test_multiple_tool_calls(self) -> None:
        """Soporta multiples tool calls en un mismo texto."""
        content = (
            "<tool_call>\n"
            '{"name": "get_price", "arguments": {"producto": "papa", "mercado": "Lo Valledor"}}\n'
            "</tool_call>\n"
            "<tool_call>\n"
            '{"name": "get_weather", "arguments": {"lat": -38.23, "lon": -72.68}}\n'
            "</tool_call>"
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
        text = "<tool_response>42</tool_response> La respuesta es 42"
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
        text = "<|im_start|>assistant\nHola<|im_end|>"
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
        assert "calculate_margin" in _TOOLS_SECTION

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
        assert "simulado" in result
        assert "humedad" in result

    def test_keyword_temperatura(self) -> None:
        """'temperatura' dispara respuesta de clima."""
        result = _mock_answer("¿Qué temperatura hace hoy?")
        assert "simulado" in result

    def test_keyword_lluvia(self) -> None:
        """'lluvia' dispara respuesta de clima."""
        result = _mock_answer("¿Hay lluvia para mañana?")
        assert "lluvia" in result.lower()

    def test_keyword_precio(self) -> None:
        """Detecta intencion de precio por keyword 'precio'."""
        result = _mock_answer("¿Cuál es el precio de la papa?")
        assert "papa" in result.lower() or "Papa" in result
        assert "pesos" in result
        assert "simulado" in result
        assert "kilo" in result

    def test_keyword_cuanto_cuesta(self) -> None:
        """'cuánto cuesta' dispara respuesta de precio."""
        result = _mock_answer("¿Cuánto cuesta la cebolla?")
        assert "pesos" in result

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
        assert "simulado" in result


# ── answer() sin modelo (mock path) ─────────────────────────────


class TestAnswerMockPath:
    """answer() usa _mock_answer cuando no hay modelo LLM cargado."""

    async def test_answer_sin_modelo_clima(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sin modelo, answer() delega en _mock_answer para clima."""
        monkeypatch.setattr("app.services.llm_service._get_model", lambda: None)
        result = await answer("¿Cómo está el clima?")
        assert "simulado" in result

    async def test_answer_sin_modelo_precio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sin modelo, answer() delega en _mock_answer para precio."""
        monkeypatch.setattr("app.services.llm_service._get_model", lambda: None)
        result = await answer("¿Cuál es el precio de la papa?")
        assert "simulado" in result

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
        assert "simulado" in result


class TestAnswerGuardasLlm:
    """Regresiones de guardas anti-cuelgue en camino LLM local."""

    async def test_carga_worker_no_bloquea_event_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """El arranque del proceso hijo se delega fuera del event loop."""
        worker_started = threading.Event()
        release_worker = threading.Event()
        heartbeat_completed = False

        def _slow_get_model() -> None:
            worker_started.set()
            release_worker.wait(timeout=1.0)
            return None

        async def _heartbeat() -> None:
            nonlocal heartbeat_completed
            while not worker_started.is_set():
                await asyncio.sleep(0)
            heartbeat_completed = True
            release_worker.set()

        monkeypatch.setattr(
            "app.services.llm_service._get_model",
            _slow_get_model,
        )

        result, _ = await asyncio.gather(
            answer("consulta de integración"),
            _heartbeat(),
        )

        assert heartbeat_completed is True
        assert result == FALLBACK_TEXT

    async def test_answer_delega_inferencia_al_worker(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """El happy path conserva el contrato textual de answer()."""
        captured_messages: list[list[dict[str, object]]] = []

        class _HappyWorker:
            def complete(
                self,
                messages: list[dict[str, object]],
                max_tokens: int,
                timeout_seconds: float,
            ) -> dict[str, object]:
                captured_messages.append(messages)
                return {"choices": [{"message": {"content": "Respuesta segura desde worker."}}]}

        monkeypatch.setattr(
            "app.services.llm_service._get_model",
            lambda: _HappyWorker(),
        )

        result = await answer("consulta de integración")

        assert result == "Respuesta segura desde worker."
        assert captured_messages
        assert captured_messages[0][-1]["content"] == "consulta de integración"

    async def test_timeout_llm_cae_a_fallback_determinista(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Si el LLM timeout, answer intenta _force_keyword_tool antes de disculpa."""
        query_secret = "precio papa para rut-secreto-timeout"
        exception_secret = "prompt-secreto-en-timeout"

        async def _raise_timeout(*args: object, **kwargs: object) -> object:
            raise TimeoutError(exception_secret)

        async def _forced(*args: object, **kwargs: object) -> str | None:
            return "Papa está a 900 pesos el kilo según ODEPA."

        monkeypatch.setattr("app.services.llm_service._get_model", lambda: object())
        monkeypatch.setattr("app.services.llm_service._run_llm_completion", _raise_timeout)
        monkeypatch.setattr("app.services.llm_service._force_keyword_tool", _forced)
        caplog.set_level(logging.WARNING, logger="app.services.llm_service")

        result = await answer(query_secret)

        assert result == "Papa está a 900 pesos el kilo según ODEPA."
        assert query_secret not in caplog.text
        assert exception_secret not in caplog.text
        assert "timeout_seconds" in caplog.text

    async def test_llm_ocupado_responde_sin_colgar(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Si la guarda detecta LLM ocupado, retorna mensaje rápido sin esperar."""

        async def _raise_busy(*args: object, **kwargs: object) -> object:
            raise LlmBusyError("llm_busy")

        async def _forced_none(*args: object, **kwargs: object) -> str | None:
            return None

        monkeypatch.setattr("app.services.llm_service._get_model", lambda: object())
        monkeypatch.setattr("app.services.llm_service._run_llm_completion", _raise_busy)
        monkeypatch.setattr("app.services.llm_service._force_keyword_tool", _forced_none)

        result = await answer("consulta sin producto ni clima")
        assert "procesando otra consulta" in result.lower()

    async def test_error_runtime_llm_prioriza_fallback_determinista(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Si el runtime del LLM falla, answer retorna datos forzados si existen."""
        query_secret = "clima para parcela-secreta-runtime"
        exception_secret = "contenido-privado-en-excepcion"

        async def _raise_runtime(*args: object, **kwargs: object) -> object:
            raise RuntimeError(exception_secret)

        async def _forced(*args: object, **kwargs: object) -> str | None:
            return "Mañana en Temuco: máxima de 14 grados, según OpenMeteo."

        monkeypatch.setattr("app.services.llm_service._get_model", lambda: object())
        monkeypatch.setattr("app.services.llm_service._run_llm_completion", _raise_runtime)
        monkeypatch.setattr("app.services.llm_service._force_keyword_tool", _forced)
        caplog.set_level(logging.WARNING, logger="app.services.llm_service")

        result = await answer(query_secret)

        assert "según OpenMeteo" in result
        assert query_secret not in caplog.text
        assert exception_secret not in caplog.text
        assert "RuntimeError" in caplog.text

    async def test_crash_del_worker_conserva_fallback_determinista(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Un hijo muerto se traduce al fallback sin escapar hacia FastAPI."""
        query_secret = "precio para sujeto-secreto-worker"

        class _CrashedWorker:
            def complete(
                self,
                messages: list[dict[str, object]],
                max_tokens: int,
                timeout_seconds: float,
            ) -> dict[str, object]:
                raise LlmWorkerCrashedError("worker_crashed")

        async def _forced(*args: object, **kwargs: object) -> str | None:
            return "Papa está a 900 pesos el kilo según ODEPA."

        monkeypatch.setattr(
            "app.services.llm_service._get_model",
            lambda: _CrashedWorker(),
        )
        monkeypatch.setattr("app.services.llm_service._force_keyword_tool", _forced)
        caplog.set_level(logging.WARNING, logger="app.services.llm_service")

        try:
            result = await answer(query_secret)
        finally:
            reset_model()

        assert result == "Papa está a 900 pesos el kilo según ODEPA."
        assert query_secret not in caplog.text
        assert "worker_crashed" in caplog.text

    async def test_argumentos_json_invalidos_no_se_loguean(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """El loop conserva solo tool y clase de error, nunca el JSON libre."""
        query_secret = "consulta-secreta-json"
        arguments_secret = "telefono-secreto-56912345678"
        completions = iter(
            [
                {"choices": [{"message": {"content": "primera-vuelta"}}]},
                {"choices": [{"message": {"content": "respuesta final segura"}}]},
            ]
        )

        async def _completion(*args: object, **kwargs: object) -> object:
            return next(completions)

        def _tool_calls(content: str) -> list[dict[str, object]]:
            if content != "primera-vuelta":
                return []
            return [
                {
                    "function": {
                        "name": "get_price",
                        "arguments": (f'{{"producto": "papa", "nota": "{arguments_secret}"'),
                    }
                }
            ]

        async def _tool_result(*args: object, **kwargs: object) -> str:
            return "resultado seguro"

        monkeypatch.setattr("app.services.llm_service._get_model", lambda: object())
        monkeypatch.setattr(
            "app.services.llm_service._run_llm_completion",
            _completion,
        )
        monkeypatch.setattr(
            "app.services.llm_service._parse_text_tool_calls",
            _tool_calls,
        )
        monkeypatch.setattr("app.services.llm_service._execute_tool", _tool_result)
        caplog.set_level(logging.WARNING, logger="app.services.llm_service")

        result = await answer(query_secret)

        assert result == "respuesta final segura"
        assert query_secret not in caplog.text
        assert arguments_secret not in caplog.text
        assert "get_price" in caplog.text
        assert "JSONDecodeError" in caplog.text

    async def test_loop_agotado_no_loguea_query_ni_tool_call(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Al agotar iteraciones solo registra el contador configurado."""
        query_secret = "consulta-secreta-loop"
        arguments_secret = "direccion-secreta-parcela-42"
        tool_result_secret = "resultado-tool-secreto"
        completion_count = 0

        async def _completion(*args: object, **kwargs: object) -> object:
            nonlocal completion_count
            completion_count += 1
            content = "tool-call-generado" if completion_count <= MAX_TOOL_ITERATIONS else "respuesta final segura"
            return {"choices": [{"message": {"content": content}}]}

        def _tool_calls(content: str) -> list[dict[str, object]]:
            if content != "tool-call-generado":
                return []
            return [
                {
                    "function": {
                        "name": "get_price",
                        "arguments": (f'{{"producto": "papa", "nota": "{arguments_secret}"}}'),
                    }
                }
            ]

        async def _tool_result(*args: object, **kwargs: object) -> str:
            return tool_result_secret

        monkeypatch.setattr("app.services.llm_service._get_model", lambda: object())
        monkeypatch.setattr(
            "app.services.llm_service._run_llm_completion",
            _completion,
        )
        monkeypatch.setattr(
            "app.services.llm_service._parse_text_tool_calls",
            _tool_calls,
        )
        monkeypatch.setattr("app.services.llm_service._execute_tool", _tool_result)
        caplog.set_level(logging.WARNING, logger="app.services.llm_service")

        result = await answer(query_secret)

        assert result == "respuesta final segura"
        assert query_secret not in caplog.text
        assert arguments_secret not in caplog.text
        assert tool_result_secret not in caplog.text
        assert f"agoto {MAX_TOOL_ITERATIONS} iteraciones" in caplog.text


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

    async def test_tool_no_whitelisteada(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Tool fuera del whitelist retorna fallback sin registrar su nombre."""
        tool_name_secret = "get_advisory_rut_secreto"
        argument_secret = "dato-libre-secreto"
        caplog.set_level(logging.WARNING, logger="app.services.llm_service")

        result = await _execute_tool(
            tool_name_secret,
            {"topic": argument_secret},
        )

        assert result == FALLBACK_TEXT
        assert tool_name_secret not in caplog.text
        assert argument_secret not in caplog.text

    async def test_error_de_tool_loguea_solo_nombre_validado_y_clase(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Argumentos y mensaje de excepción no aparecen en telemetría."""
        argument_secret = "coordenada-secreta-tool"
        exception_secret = "respuesta-privada-en-error-tool"

        async def _failing_handler(**kwargs: object) -> str:
            raise RuntimeError(exception_secret)

        monkeypatch.setattr(
            "app.services.llm_service._get_tool_handlers",
            lambda: {"get_weather": _failing_handler},
        )
        caplog.set_level(logging.INFO, logger="app.services.llm_service")

        result = await _execute_tool(
            "get_weather",
            {"ubicacion": argument_secret},
        )

        assert "error" in result.lower()
        assert argument_secret not in caplog.text
        assert exception_secret not in caplog.text
        assert "get_weather" in caplog.text
        assert "RuntimeError" in caplog.text

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

    async def test_tool_calculate_margin_en_whitelist_no_es_fallback(self) -> None:
        """calculate_margin esta en whitelist, NO retorna FALLBACK_TEXT.

        Similar a test_tool_get_price_en_whitelist_se_ejecuta:
        verifica que la tool no sea rechazada por whitelist.
        """
        result = await _execute_tool(
            "calculate_margin",
            {"producto": "papa", "cantidad": "1", "unidad": "saco", "precio_total": "60000"},
        )
        assert result != FALLBACK_TEXT

    async def test_calculate_margin_sin_cantidad_retorna_validacion(self) -> None:
        """calculate_margin sin cantidad retorna mensaje de validacion, no fallback."""
        result = await _execute_tool(
            "calculate_margin",
            {"producto": "papa", "unidad": "saco", "precio_total": "60000"},
        )
        assert result != FALLBACK_TEXT
        assert "cantidad" in result.lower() or "No entendi" in result

    async def test_calculate_margin_sin_unidad_retorna_validacion(self) -> None:
        """calculate_margin sin unidad retorna mensaje de validacion."""
        result = await _execute_tool(
            "calculate_margin",
            {"producto": "papa", "cantidad": "1", "precio_total": "60000"},
        )
        assert result != FALLBACK_TEXT
        assert "unidad" in result.lower() or "No entendi" in result

    async def test_calculate_margin_sin_precio_total_retorna_validacion(self) -> None:
        """calculate_margin sin precio_total retorna mensaje de validacion."""
        result = await _execute_tool(
            "calculate_margin",
            {"producto": "papa", "cantidad": "1", "unidad": "saco"},
        )
        assert result != FALLBACK_TEXT
        assert "monto" in result.lower() or "No entendi" in result


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

        def _raise_db_error(session, producto, mercado="", phone_hash=None):
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

    def test_regex_no_matchea_sin_texto_despues(self) -> None:
        """'tengo 30 kilos' al final de frase no dispara venta."""
        assert _VENTA_KILOS_RE.search("tengo 30 kilos") is None

    def test_regex_acepta_sin_de_con_producto(self) -> None:
        """'cuanto vale 50 kilos papa' (sin 'de') tambien cuenta como venta."""
        match = _VENTA_KILOS_RE.search("cuanto vale 50 kilos papa")
        assert match is not None
        assert match.group(1) == "50"

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
        monkeypatch.setattr(odepa_service, "calculate_sale_value_for_llm", _capture_sale)

        result = await _force_keyword_tool("voy a vender 30 kilos de papa")
        assert result is not None
        assert "25.500 pesos" in result
        assert calls == [{"producto": "papa", "cantidad_kg": "30", "mercado": ""}]

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
        monkeypatch.setattr(odepa_service, "calculate_sale_value_for_llm", _capture_sale)

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


# ── Calculate Margin Tool ────────────────────────────────────────


class TestCalculateMarginToolDefinition:
    """Verifica que la tool calculate_margin este correctamente definida."""

    def test_margin_tool_en_whitelist(self) -> None:
        """calculate_margin debe estar en WHITELIST_TOOLS."""
        assert "calculate_margin" in WHITELIST_TOOLS

    def test_margin_tool_tiene_cuatro_parametros_requeridos(self) -> None:
        """La tool requiere producto, cantidad, unidad y precio_total."""
        margin_tool = [t for t in TOOLS if t["function"]["name"] == "calculate_margin"]
        assert len(margin_tool) == 1
        params = margin_tool[0]["function"]["parameters"]
        required = set(params.get("required", []))
        assert required == {"producto", "cantidad", "unidad", "precio_total"}

    def test_margin_tool_mercado_opcional(self) -> None:
        """Mercado es opcional en calculate_margin."""
        margin_tool = [t for t in TOOLS if t["function"]["name"] == "calculate_margin"]
        params = margin_tool[0]["function"]["parameters"]["properties"]
        assert "mercado" in params
        assert "mercado" not in set(margin_tool[0]["function"]["parameters"].get("required", []))


class TestCalculateMarginDb:
    """Tests de calculate_margin_for_llm con DB real.

    Usa la fixture db que crea una SQLite temporal con tablas, y
    parchea SessionLocal para que las funciones internas la usen.
    """

    def _insertar_precio(
        self,
        db,
        producto="papa",
        precio_kg=850,
        mercado="Mercado Mayorista Lo Valledor de Santiago",
        unidad="kg",
        fecha=None,
    ):
        """Helper: inserta un precio ODEPA de prueba."""
        import datetime

        from app.models.odepa_price import OdepaPrice

        if fecha is None:
            fecha = datetime.date(2026, 7, 15)
        reg = OdepaPrice(
            producto=producto,
            mercado=mercado,
            precio_kg=precio_kg,
            unidad=unidad,
            fecha=fecha,
            fuente="test",
        )
        db.add(reg)
        db.commit()
        return reg

    def test_precio_superior_a_referencia(self, db) -> None:
        """Venta de 1 saco (50 kg) a $60.000, referencia ODEPA $850/kg -> $42.500.
        Diferencia: +$17.500, +41,2%.
        """
        from app.services.odepa_service import calculate_margin_for_llm

        self._insertar_precio(db, producto="papa", precio_kg=850)

        result = calculate_margin_for_llm(
            session=db,
            producto="papa",
            cantidad="1",
            unidad="saco",
            precio_total="60000",
        )
        assert "Papa" in result
        assert "ODEPA" in result
        assert "17.500" in result  # $60.000 - $42.500 = +$17.500
        assert "sobre" in result  # vendio sobre referencia

    def test_precio_inferior_a_referencia(self, db) -> None:
        """Venta de 1 saco (50 kg) a $30.000, referencia ODEPA $850/kg -> $42.500.
        Diferencia: -$12.500, -29,4%.
        """
        from app.services.odepa_service import calculate_margin_for_llm

        self._insertar_precio(db, producto="papa", precio_kg=850)

        result = calculate_margin_for_llm(
            session=db,
            producto="papa",
            cantidad="1",
            unidad="saco",
            precio_total="30000",
        )
        assert "Papa" in result
        assert "ODEPA" in result
        assert "12.500" in result  # $42.500 - $30.000 = -$12.500
        assert "bajo" in result  # vendio bajo referencia

    def test_precio_exactamente_igual_a_referencia(self, db) -> None:
        """Venta de 1 saco (50 kg) al mismo precio que ODEPA: $42.500 -> 0% dif."""
        from app.services.odepa_service import calculate_margin_for_llm

        self._insertar_precio(db, producto="papa", precio_kg=850)

        result = calculate_margin_for_llm(
            session=db,
            producto="papa",
            cantidad="1",
            unidad="saco",
            precio_total="42500",
        )
        assert "Papa" in result
        assert "ODEPA" in result
        assert "exactamente" in result or "0" in result.split("por ciento")[0]

    def test_conversion_saco_a_kilos(self, db) -> None:
        """Saco = 50 kg: 3 sacos = 150 kg."""
        from app.services.odepa_service import calculate_margin_for_llm

        self._insertar_precio(db, producto="papa", precio_kg=1000)

        result = calculate_margin_for_llm(
            session=db,
            producto="papa",
            cantidad="3",
            unidad="saco",
            precio_total="500000",
        )
        assert "Papa" in result
        assert "ODEPA" in result
        # 150 kg * $1.000 = $150.000 de referencia
        assert "150.000" in result or "150000" in result

    def test_conversion_malla_a_kilos(self, db) -> None:
        """Malla = 25 kg: 4 mallas = 100 kg."""
        from app.services.odepa_service import calculate_margin_for_llm

        self._insertar_precio(db, producto="tomate", precio_kg=1200)

        result = calculate_margin_for_llm(
            session=db,
            producto="tomate",
            cantidad="4",
            unidad="malla",
            precio_total="200000",
        )
        assert "Tomate" in result or "tomate" in result
        assert "ODEPA" in result
        # 100 kg * $1.200 = $120.000 de referencia

    def test_conversion_caja_a_kilos(self, db) -> None:
        """Caja = 20 kg."""
        from app.services.odepa_service import calculate_margin_for_llm

        self._insertar_precio(db, producto="cebolla", precio_kg=500)

        result = calculate_margin_for_llm(
            session=db,
            producto="cebolla",
            cantidad="10",
            unidad="caja",
            precio_total="150000",
        )
        assert "Cebolla" in result or "cebolla" in result
        assert "ODEPA" in result
        # 10 cajas * 20 kg = 200 kg * $500 = $100.000

    def test_conversion_tonelada_a_kilos(self, db) -> None:
        """Tonelada = 1000 kg."""
        from app.services.odepa_service import calculate_margin_for_llm

        self._insertar_precio(db, producto="trigo", precio_kg=300)

        result = calculate_margin_for_llm(
            session=db,
            producto="trigo",
            cantidad="2",
            unidad="tonelada",
            precio_total="800000",
        )
        assert "Trigo" in result or "trigo" in result
        assert "ODEPA" in result
        # 2 toneladas = 2000 kg * $300 = $600.000 referencia

    def test_conversion_kilo_directo(self, db) -> None:
        """Kilo no necesita conversion: 100 kilos = 100 kg."""
        from app.services.odepa_service import calculate_margin_for_llm

        self._insertar_precio(db, producto="papa", precio_kg=850)

        result = calculate_margin_for_llm(
            session=db,
            producto="papa",
            cantidad="100",
            unidad="kilo",
            precio_total="100000",
        )
        assert "Papa" in result
        assert "ODEPA" in result
        # 100 kg * $850 = $85.000 referencia

    def test_producto_sin_datos_odepa(self, db) -> None:
        """Producto sin precio ODEPA retorna mensaje informativo, no error."""
        from app.services.odepa_service import calculate_margin_for_llm

        result = calculate_margin_for_llm(
            session=db,
            producto="kiwi",
            cantidad="10",
            unidad="kilo",
            precio_total="50000",
        )
        assert "No tengo datos" in result
        assert "kiwi" in result.lower()

    def test_cantidad_invalida(self, db) -> None:
        """Cantidad no numerica retorna mensaje de error."""
        from app.services.odepa_service import calculate_margin_for_llm

        result = calculate_margin_for_llm(
            session=db,
            producto="papa",
            cantidad="abc",
            unidad="kilo",
            precio_total="50000",
        )
        assert "No entendi" in result

    def test_cantidad_cero(self, db) -> None:
        """Cantidad cero retorna mensaje."""
        from app.services.odepa_service import calculate_margin_for_llm

        result = calculate_margin_for_llm(
            session=db,
            producto="papa",
            cantidad="0",
            unidad="kilo",
            precio_total="50000",
        )
        assert "mayor a cero" in result

    def test_precio_total_invalido(self, db) -> None:
        """Precio total no numerico retorna mensaje de error."""
        from app.services.odepa_service import calculate_margin_for_llm

        result = calculate_margin_for_llm(
            session=db,
            producto="papa",
            cantidad="10",
            unidad="kilo",
            precio_total="abc",
        )
        assert "No entendi" in result

    def test_precio_total_cero(self, db) -> None:
        """Precio total cero retorna mensaje."""
        from app.services.odepa_service import calculate_margin_for_llm

        result = calculate_margin_for_llm(
            session=db,
            producto="papa",
            cantidad="10",
            unidad="kilo",
            precio_total="0",
        )
        assert "mayor a cero" in result

    def test_unidad_invalida(self, db) -> None:
        """Unidad no reconocida retorna mensaje con lista de unidades validas."""
        from app.services.odepa_service import calculate_margin_for_llm

        self._insertar_precio(db, producto="papa", precio_kg=850)

        result = calculate_margin_for_llm(
            session=db,
            producto="papa",
            cantidad="10",
            unidad="atado",
            precio_total="50000",
        )
        assert "No conozco" in result
        assert "kilo" in result  # menciona unidades validas

    def test_unidad_vacia(self, db) -> None:
        """Unidad vacia retorna mensaje."""
        from app.services.odepa_service import calculate_margin_for_llm

        result = calculate_margin_for_llm(
            session=db,
            producto="papa",
            cantidad="10",
            unidad="",
            precio_total="50000",
        )
        assert "No conozco" in result

    def test_no_persiste_datos_financieros(self, db) -> None:
        """calculate_margin_for_llm NO escribe en la DB.

        Verifica que la tabla consultations no reciba nuevas filas
        tras ejecutar calculate_margin_for_llm. La funcion es
        read-only (solo SELECT en odepa_prices).
        """
        from sqlalchemy import func, select

        from app.models.consultation import Consultation
        from app.services.odepa_service import calculate_margin_for_llm

        self._insertar_precio(db, producto="papa", precio_kg=850)

        # Contar filas antes.
        antes = db.scalar(select(func.count()).select_from(Consultation))

        # Ejecutar la funcion.
        calculate_margin_for_llm(
            session=db,
            producto="papa",
            cantidad="50",
            unidad="saco",
            precio_total="60000",
        )

        # Contar filas despues — no debe haber cambiado.
        despues = db.scalar(select(func.count()).select_from(Consultation))
        assert despues == antes

    def test_unidad_kilos_plural(self, db) -> None:
        """'kilos' (plural) funciona igual que 'kilo'."""
        from app.services.odepa_service import calculate_margin_for_llm

        self._insertar_precio(db, producto="papa", precio_kg=850)

        result = calculate_margin_for_llm(
            session=db,
            producto="papa",
            cantidad="30",
            unidad="kilos",
            precio_total="30000",
        )
        assert "Papa" in result
        assert "ODEPA" in result

    def test_unidad_kg_abreviatura(self, db) -> None:
        """'kg' funciona como abreviatura de kilo."""
        from app.services.odepa_service import calculate_margin_for_llm

        self._insertar_precio(db, producto="papa", precio_kg=850)

        result = calculate_margin_for_llm(
            session=db,
            producto="papa",
            cantidad="30",
            unidad="kg",
            precio_total="30000",
        )
        assert "Papa" in result
        assert "ODEPA" in result

    def test_precio_decimal_coma(self, db) -> None:
        """Precio total con coma decimal se normaliza correctamente."""
        from app.services.odepa_service import calculate_margin_for_llm

        self._insertar_precio(db, producto="papa", precio_kg=850)

        result = calculate_margin_for_llm(
            session=db,
            producto="papa",
            cantidad="50",
            unidad="saco",
            precio_total="60,500",
        )
        # $60.500 -> $60.500, no error de parseo
        assert "No entendi" not in result
        assert "ODEPA" in result

    def test_precio_con_signo_peso(self, db) -> None:
        """Precio total con $ se normaliza."""
        from app.services.odepa_service import calculate_margin_for_llm

        self._insertar_precio(db, producto="papa", precio_kg=850)

        result = calculate_margin_for_llm(
            session=db,
            producto="papa",
            cantidad="50",
            unidad="saco",
            precio_total="$60000",
        )
        assert "No entendi" not in result
        assert "ODEPA" in result

    def test_mercado_especifico(self, db) -> None:
        """Mercado especifico filtra correctamente."""
        from app.services.odepa_service import calculate_margin_for_llm

        # Dos mercados, precio diferente.
        self._insertar_precio(db, producto="papa", precio_kg=850, mercado="Mercado Mayorista Lo Valledor de Santiago")
        self._insertar_precio(db, producto="papa", precio_kg=700, mercado="Vega Modelo de Temuco")

        result = calculate_margin_for_llm(
            session=db,
            producto="papa",
            cantidad="1",
            unidad="saco",
            precio_total="35000",
            mercado="Vega Modelo de Temuco",
        )
        # Debe usar precio de Temuco ($700/kg, no $850).
        assert "ODEPA" in result
        # 50 kg * $700 = $35.000 = exactamente lo que recibio
        assert "exactamente" in result

    def test_unidad_odepa_no_convertible(self, db) -> None:
        """Cuando ODEPA publica en unidad no convertible, avisa y no inventa."""
        from app.services.odepa_service import calculate_margin_for_llm

        # ODEPA con unidad no convertible (docena de atados).
        self._insertar_precio(db, producto="lechuga", precio_kg=1200, unidad="$/docena de atados")

        result = calculate_margin_for_llm(
            session=db,
            producto="lechuga",
            cantidad="10",
            unidad="kilo",
            precio_total="15000",
        )
        # No debe fallar, debe avisar que no puede comparar.
        assert "No puedo calcular" in result
        assert "ODEPA" in result


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


# ── answer_via_openrouter (fallback LLM remoto) ─────────────────


def _install_openrouter_mock(monkeypatch: pytest.MonkeyPatch, responses: list[dict[str, object]]) -> None:
    """Instala un cliente HTTP mockeado que retorna `responses` en orden,
    una por cada llamada a chat_completion_with_tools (simula el loop)."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        idx = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return httpx.Response(200, json=responses[idx])

    transport = httpx.MockTransport(handler)
    mock_client = httpx.AsyncClient(transport=transport)
    monkeypatch.setattr("app.services.openrouter_service._http_client", mock_client)


class TestAnswerViaOpenrouter:
    """Fallback de 2a capa: responde via OpenRouter cuando el LLM local falla."""

    async def test_sin_api_key_retorna_none_sin_llamar_red(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sin OPENROUTER_API_KEY, el fallback esta deshabilitado."""
        monkeypatch.setattr(settings, "openrouter_api_key", "")
        result = await answer_via_openrouter("a cuanto esta la papa")
        assert result is None

    async def test_respuesta_directa_sin_tool_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """El modelo responde texto sin necesitar tools — se retorna tal cual."""
        monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-test")
        _install_openrouter_mock(
            monkeypatch,
            [{"choices": [{"message": {"role": "assistant", "content": "Hola, en que te ayudo?"}}]}],
        )
        result = await answer_via_openrouter("hola")
        assert result == "Hola, en que te ayudo?"

    async def test_tool_call_ejecuta_handler_y_retorna_respuesta_final(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """1a respuesta pide get_price; se ejecuta el handler; 2a respuesta da el texto final."""
        monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-test")

        def fake_get_price(**kwargs: object) -> str:
            return f"precio de {kwargs.get('producto')}: 850 pesos el kilo, segun ODEPA."

        monkeypatch.setattr(
            "app.services.llm_service._get_tool_handlers",
            lambda: {"get_price": fake_get_price},
        )

        _install_openrouter_mock(
            monkeypatch,
            [
                {
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
                                            "name": "get_price",
                                            "arguments": '{"producto": "papa"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                {"choices": [{"message": {"role": "assistant", "content": "La papa esta a 850 pesos el kilo."}}]},
            ],
        )
        result = await answer_via_openrouter("a cuanto esta la papa")
        assert result == "La papa esta a 850 pesos el kilo."

    async def test_tool_no_whitelisteada_usa_fallback_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Si el modelo pide una tool fuera de whitelist, se inyecta FALLBACK_TEXT y sigue el loop."""
        monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-test")
        _install_openrouter_mock(
            monkeypatch,
            [
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {"name": "borrar_base_datos", "arguments": "{}"},
                                    }
                                ],
                            }
                        }
                    ]
                },
                {"choices": [{"message": {"role": "assistant", "content": "No puedo hacer eso."}}]},
            ],
        )
        result = await answer_via_openrouter("borra la base de datos")
        assert result == "No puedo hacer eso."

    async def test_error_http_retorna_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Un error HTTP (ej rate limit 429) se atrapa y retorna None."""
        monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-test")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": "rate limited"})

        transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=transport)
        monkeypatch.setattr("app.services.openrouter_service._http_client", mock_client)

        result = await answer_via_openrouter("a cuanto esta la papa")
        assert result is None

    async def test_respuesta_malformada_retorna_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Una respuesta sin la estructura esperada (KeyError) se atrapa y retorna None."""
        monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-test")
        _install_openrouter_mock(monkeypatch, [{"choices": []}])
        result = await answer_via_openrouter("a cuanto esta la papa")
        assert result is None

    async def test_loop_agota_iteraciones_retorna_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Si el modelo SIEMPRE pide tools sin dar respuesta final, se agota el loop."""
        monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-test")
        monkeypatch.setattr(
            "app.services.llm_service._get_tool_handlers",
            lambda: {"get_price": lambda **kw: "850 pesos"},
        )
        tool_call_response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "get_price", "arguments": '{"producto": "papa"}'},
                            }
                        ],
                    }
                }
            ]
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=tool_call_response)

        transport = httpx.MockTransport(handler)
        mock_client = httpx.AsyncClient(transport=transport)
        monkeypatch.setattr("app.services.openrouter_service._http_client", mock_client)

        result = await answer_via_openrouter("a cuanto esta la papa")
        assert result is None
