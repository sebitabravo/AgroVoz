"""Tests del fast-path determinista que evita el LLM.

El LLM sobre CPU limitada gasta la mayor parte de la latencia leyendo el prompt
de tools (~2779 tokens). Para la consulta directa y clasificada con certeza, las
tools deterministas ya arman la frase final, asi que el pipeline la responde sin
invocar al modelo. Estos tests fijan el gate: cuando aplica y cuando NO, para que
un cambio futuro no lo abra de mas y degrade la calidad de las respuestas.
"""

import builtins
import logging
from datetime import date

import pytest

from app.core.config import settings
from app.schemas.variables import ExtractedVariables
from app.services.agricultural_calendar_service import get_calendario_agricola
from app.services.llm_keywords import (
    _force_compound_keyword_tools,
    _force_corpus_search,
)
from app.services.llm_service import (
    _TOOLS_SECTION_POR_TIPO,
    SYSTEM_PROMPT,
    _build_messages,
)
from app.services.pipeline_service import AgroVozPipeline


def _vars(tipo: str, producto: str | None = None) -> ExtractedVariables:
    """Arma un ExtractedVariables minimo para los casos de gate."""
    return ExtractedVariables(producto=producto, consulta_tipo=tipo)  # type: ignore[arg-type]


class TestCorpusNoTumbaElPipeline:
    """El corpus RAG es opcional: si falta, se sigue respondiendo precio y clima.

    Regresion real: rag_service importa scikit-learn y el import estaba FUERA
    del branch de keywords, asi que corria en cada consulta. Con la imagen sin
    reconstruir (sin sklearn), un "¿como esta el clima?" moria con
    ModuleNotFoundError y el productor no recibia nada.
    """

    async def test_sin_keywords_de_corpus_no_importa_rag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Una consulta de clima no debe siquiera tocar rag_service."""
        real_import = builtins.__import__

        def import_espia(name: str, *args: object, **kwargs: object) -> object:
            if "rag_service" in name:
                raise AssertionError("no debe importarse rag_service sin keywords")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", import_espia)
        assert await _force_corpus_search("¿como esta el clima hoy?") is None

    async def test_dependencia_faltante_degrada_sin_romper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Con keywords de corpus pero sin la dependencia: None, no excepcion."""
        real_import = builtins.__import__

        def import_roto(name: str, *args: object, **kwargs: object) -> object:
            if "rag_service" in name:
                raise ImportError("No module named 'sklearn'")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", import_roto)
        assert await _force_corpus_search("¿que dice el boletin de ODEPA?") is None


class TestFastPathCreditoIndap:
    """Crédito se resuelve antes de fuzzy, precio, RAG y LLM."""

    @pytest.mark.parametrize(
        "query",
        [
            "Necesito un crédito de INDAP para comprar semillas",
            "¿Qué documento habla de crédito INDAP?",
        ],
    )
    async def test_frases_reales_no_caen_en_pera_haba_ni_llm(
        self,
        query: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regresión P0: para→pera y habla→haba entregaban precio ODEPA."""

        def fail_extract(_query: str) -> ExtractedVariables:
            raise AssertionError("crédito debe resolverse antes del clasificador")

        async def fail_llm(*args: object, **kwargs: object) -> str:
            raise AssertionError("crédito no debe invocar el LLM")

        def fail_greeting(_query: str) -> bool:
            raise AssertionError("crédito debe resolverse antes del fuzzy de saludo")

        monkeypatch.setattr(
            AgroVozPipeline,
            "_extract_variables",
            staticmethod(fail_extract),
        )
        monkeypatch.setattr(
            "app.services.llm_keywords._detect_greeting",
            fail_greeting,
        )
        monkeypatch.setattr("app.services.llm_service.answer", fail_llm)

        origin = ["desconocido"]
        response, intent = await AgroVozPipeline._generate_response(
            query,
            "phone-hash",
            origin,
        )

        assert "INDAP" in response
        assert "precio" not in response.lower()
        assert "pera" not in response.lower()
        assert "haba" not in response.lower()
        assert intent == "credito"
        assert origin == ["fast_path_credito"]

    async def test_precio_normal_conserva_su_fast_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """La cuarentena crediticia no intercepta el precio de la papa."""

        async def fake_keyword_tool(
            query_text: str,
            phone_hash: str | None = None,
        ) -> str:
            assert query_text == "precio de la papa"
            assert phone_hash == "phone-hash"
            return "La papa está a 500 pesos el kilo según ODEPA."

        async def fail_llm(*args: object, **kwargs: object) -> str:
            raise AssertionError("el precio simple tampoco necesita LLM")

        monkeypatch.setattr(
            "app.services.pipeline_service.AgroVozPipeline._load_user_cultivos",
            staticmethod(lambda _phone: None),
        )
        monkeypatch.setattr(
            "app.services.llm_keywords._force_keyword_tool",
            fake_keyword_tool,
        )
        monkeypatch.setattr("app.services.llm_service.answer", fail_llm)

        origin = ["desconocido"]
        response, intent = await AgroVozPipeline._generate_response(
            "precio de la papa",
            "phone-hash",
            origin,
        )

        assert "500 pesos" in response
        assert intent == "precio"
        assert origin == ["fast_path"]


class TestFastPathCalendario:
    """El calendario se resuelve igual para texto y audio tras Whisper."""

    @pytest.mark.asyncio
    async def test_texto_calendario_citado_no_invoca_llm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """La ventana proviene del servicio local y conserva el origen trazable."""
        monkeypatch.setattr(settings, "agronomic_rules_enabled", True)

        def fixed_calendar(producto: str, comuna: str) -> str:
            """Mantiene fija la fecha del snapshot en esta prueba de integración."""
            return get_calendario_agricola(producto, comuna, today=date(2026, 8, 3))

        monkeypatch.setattr(
            "app.services.agricultural_calendar_service.get_calendario_agricola",
            fixed_calendar,
        )
        monkeypatch.setattr(
            "app.services.llm_service.answer",
            lambda *_args, **_kwargs: pytest.fail("el calendario no debe invocar al LLM"),
        )

        origin = ["desconocido"]
        response, intent = await AgroVozPipeline._generate_response(
            "cuando siembro trigo en Traiguén",
            "phone-hash",
            origin,
        )

        assert intent == "agronomica"
        assert "15/04 a 30/05" in response
        assert "INIA" in response
        assert origin == ["calendario"]


class TestFastPathAgronomicoDegradacion:
    """Una keyword agronómica no debe bloquear precio o clima con el gate off."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("query", "expected_intent"),
        [
            ("a cuanto esta la cosecha de trigo", "precio"),
            ("como esta el clima para la cosecha", "clima"),
        ],
    )
    async def test_consulta_mixta_usa_fast_path_normal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        query: str,
        expected_intent: str,
    ) -> None:
        """El pipeline llega a la tool normal y no retorna el texto del gate."""
        monkeypatch.setattr(settings, "agronomic_rules_enabled", False)
        monkeypatch.setattr(
            AgroVozPipeline,
            "_load_user_cultivos",
            staticmethod(lambda _phone_hash: None),
        )

        async def normal_keyword_tool(
            _query: str,
            phone_hash: str | None = None,
        ) -> str:
            assert phone_hash == "phone-hash"
            return "Respuesta normal de datos."

        async def fail_llm(*_args: object, **_kwargs: object) -> str:
            raise AssertionError("la consulta simple debe usar el fast-path normal")

        monkeypatch.setattr("app.services.llm_keywords._force_keyword_tool", normal_keyword_tool)
        monkeypatch.setattr("app.services.llm_service.answer", fail_llm)

        origin = ["desconocido"]
        response, intent = await AgroVozPipeline._generate_response(query, "phone-hash", origin)

        assert response == "Respuesta normal de datos."
        assert intent == expected_intent
        assert origin == ["fast_path"]


class TestGateFastPath:
    """Cuando el pipeline puede saltarse el LLM."""

    def test_precio_con_producto_y_consulta_corta_usa_fast_path(self) -> None:
        assert AgroVozPipeline._puede_usar_fast_path("¿a cuánto está la papa?", _vars("precio", "papa"), None)

    def test_clima_usa_fast_path_sin_requerir_producto(self) -> None:
        assert AgroVozPipeline._puede_usar_fast_path("¿va a llover mañana?", _vars("clima"), None)

    def test_precio_sin_producto_no_usa_fast_path(self) -> None:
        """Sin producto identificado el atajo no puede resolver la consulta."""
        assert not AgroVozPipeline._puede_usar_fast_path("¿a cuánto está?", _vars("precio"), None)

    def test_tipo_ambos_no_usa_fast_path(self) -> None:
        """Precio y clima juntos necesitan al LLM para combinar las respuestas."""
        assert not AgroVozPipeline._puede_usar_fast_path("precio de la papa y el clima", _vars("ambos", "papa"), None)

    def test_tipo_desconocido_no_usa_fast_path(self) -> None:
        assert not AgroVozPipeline._puede_usar_fast_path("una consulta rara", _vars("desconocido"), None)

    def test_system_tip_de_margen_no_usa_fast_path(self) -> None:
        """calculate_margin necesita razonamiento del LLM, no keywords."""
        assert not AgroVozPipeline._puede_usar_fast_path(
            "vendí 100 kilos de papa", _vars("precio", "papa"), "usa calculate_margin"
        )

    def test_consulta_compuesta_no_usa_fast_path(self) -> None:
        """Marcas de pregunta compuesta desactivan el atajo."""
        assert not AgroVozPipeline._puede_usar_fast_path(
            "¿a cuánto está la papa y también el tomate?", _vars("precio", "papa"), None
        )

    def test_consulta_que_pide_explicacion_no_usa_fast_path(self) -> None:
        assert not AgroVozPipeline._puede_usar_fast_path("¿por qué subió la papa?", _vars("precio", "papa"), None)

    def test_consulta_larga_no_usa_fast_path(self) -> None:
        """Una consulta larga suele traer contexto que el atajo ignoraria."""
        larga = "hola buenas tardes " * 12
        assert len(larga) > AgroVozPipeline._FAST_PATH_MAX_CHARS
        assert not AgroVozPipeline._puede_usar_fast_path(larga, _vars("precio", "papa"), None)


class TestMercadoEspecifico:
    """Mercado nombrado usa fast-path: el atajo ya extrae el alias.

    Antes se bloqueaba porque ``_force_keyword_tool`` ignoraba el mercado de
    la consulta y respondia Lo Valledor. Ahora extrae "vega central" /
    "valledor" y ``get_price_for_llm`` lo resuelve por substring.
    """

    def test_vega_central_usa_fast_path(self) -> None:
        assert AgroVozPipeline._puede_usar_fast_path(
            "precio de la papa en la Vega Central", _vars("precio", "papa"), None
        )

    def test_lo_valledor_usa_fast_path(self) -> None:
        assert AgroVozPipeline._puede_usar_fast_path(
            "cuanto vale la papa en Lo Valledor", _vars("precio", "papa"), None
        )

    def test_mercado_generico_si_usa_fast_path(self) -> None:
        """ "en el mercado" a secas no nombra un mercado: el atajo aplica."""
        assert AgroVozPipeline._puede_usar_fast_path(
            "¿a cómo está la papa en el mercado?", _vars("precio", "papa"), None
        )

    def test_feria_generica_si_usa_fast_path(self) -> None:
        assert AgroVozPipeline._puede_usar_fast_path("precio de la papa en la feria", _vars("precio", "papa"), None)


class TestFastPathCompuesto:
    """Precio y clima se resuelven juntos sin depender del LLM."""

    async def test_respeta_mercado_comuna_y_no_invoca_llm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Vega Central y Temuco llegan a sus fuentes respectivas."""
        calls: dict[str, object] = {}

        class DummySession:
            def close(self) -> None:
                calls["session_closed"] = True

        def fake_price(
            session: object,
            *,
            producto: str,
            mercado: str,
            phone_hash: str | None,
        ) -> str:
            calls["price"] = (session, producto, mercado, phone_hash)
            return "Papa a 12.000 pesos el saco."

        async def fake_forecast(comuna: str, *, dias: int) -> str:
            calls["forecast"] = (comuna, dias)
            return "Mañana habrá lluvia."

        async def fail_llm(*args: object, **kwargs: object) -> str:
            raise AssertionError("el LLM no debe ejecutarse")

        monkeypatch.setattr("app.core.database.SessionLocal", DummySession)
        monkeypatch.setattr("app.services.odepa_service.get_price_for_llm", fake_price)
        monkeypatch.setattr("app.services.weather_service.get_pronostico", fake_forecast)
        monkeypatch.setattr(
            "app.services.pipeline_service.AgroVozPipeline._load_user_cultivos",
            staticmethod(lambda _phone: None),
        )
        monkeypatch.setattr("app.services.llm_service.answer", fail_llm)

        origen = ["desconocido"]
        response, intent = await AgroVozPipeline._generate_response(
            "precio de la papa en Vega Central y clima mañana en Temuco",
            "phone-hash",
            origen,
        )

        assert calls["price"][1:] == ("papa", "vega central", "phone-hash")  # type: ignore[index]
        assert calls["forecast"] == ("Temuco", 2)
        assert calls["session_closed"] is True
        assert "ODEPA — Precio:\nPapa a 12.000 pesos el saco." in response
        assert "OpenMeteo — Clima:\nMañana habrá lluvia." in response
        assert intent == "precio"
        assert origen == ["fast_path_compuesto"]

    async def test_si_falla_clima_conserva_precio_y_avisa(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.WARNING, logger="app.services.llm_keywords")

        class DummySession:
            def close(self) -> None:
                pass

        def fake_price(
            session: object,
            *,
            producto: str,
            mercado: str,
            phone_hash: str | None,
        ) -> str:
            return "Precio ODEPA disponible."

        async def broken_weather(*args: object, **kwargs: object) -> str:
            raise RuntimeError("secreto compuesto: precio de papa y clima en Traiguén phone-hash")

        monkeypatch.setattr("app.core.database.SessionLocal", DummySession)
        monkeypatch.setattr("app.services.odepa_service.get_price_for_llm", fake_price)
        monkeypatch.setattr("app.services.weather_service.get_weather", broken_weather)

        response = await _force_compound_keyword_tools(
            "precio de papa y clima en Traiguén",
            phone_hash="phone-hash",
        )

        assert "ODEPA — Precio:\nPrecio ODEPA disponible." in response
        assert "OpenMeteo — Clima:\nNo pude obtener el clima" in response
        assert "secreto compuesto" not in caplog.text
        assert "Traiguén" not in caplog.text
        assert "phone-hash" not in caplog.text

    async def test_si_falla_odepa_conserva_clima_y_avisa(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class DummySession:
            def close(self) -> None:
                pass

        def broken_price(*args: object, **kwargs: object) -> str:
            raise RuntimeError("ODEPA caído")

        async def fake_weather(*, lat: float, lon: float) -> str:
            return f"Clima disponible en {lat}, {lon}."

        monkeypatch.setattr("app.core.database.SessionLocal", DummySession)
        monkeypatch.setattr("app.services.odepa_service.get_price_for_llm", broken_price)
        monkeypatch.setattr("app.services.weather_service.get_weather", fake_weather)

        response = await _force_compound_keyword_tools("precio de papa y clima en Traiguén")

        assert "ODEPA — Precio:\nNo pude obtener el precio" in response
        assert "OpenMeteo — Clima:\nClima disponible" in response
        assert "ninguno de los dos" not in response

    async def test_producto_faltante_entrega_clima_y_pide_aclaracion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_forecast(comuna: str, *, dias: int) -> str:
            return f"Pronóstico para {comuna}, {dias} días."

        monkeypatch.setattr("app.services.weather_service.get_pronostico", fake_forecast)

        response = await _force_compound_keyword_tools("dame el precio y el clima mañana en Angol")

        assert "Necesito que me indiques qué producto" in response
        assert "OpenMeteo — Clima:\nPronóstico para Angol, 2 días." in response
        assert "ninguno de los dos" not in response

    async def test_ambas_fuentes_fallan_responde_con_honestidad(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class DummySession:
            def close(self) -> None:
                pass

        def broken_price(*args: object, **kwargs: object) -> str:
            raise RuntimeError("ODEPA caído")

        async def broken_weather(*args: object, **kwargs: object) -> str:
            raise RuntimeError("OpenMeteo caído")

        monkeypatch.setattr("app.core.database.SessionLocal", DummySession)
        monkeypatch.setattr("app.services.odepa_service.get_price_for_llm", broken_price)
        monkeypatch.setattr("app.services.weather_service.get_weather", broken_weather)

        response = await _force_compound_keyword_tools("precio de papa y clima en Traiguén")

        assert response.startswith("No pude completar ninguno de los dos datos solicitados.")
        assert "ODEPA — Precio:\nNo pude obtener el precio" in response
        assert "OpenMeteo — Clima:\nNo pude obtener el clima" in response


class TestSubsetDeTools:
    """El bloque de tools se recorta al dominio consultado."""

    def test_precio_no_incluye_tools_de_clima(self) -> None:
        seccion = _TOOLS_SECTION_POR_TIPO["precio"]
        assert '"name": "get_price"' in seccion
        assert '"name": "get_weather"' not in seccion
        assert '"name": "get_clima_historico"' not in seccion

    def test_clima_no_incluye_tools_de_precio(self) -> None:
        """Se compara sobre el campo name: las descripciones se referencian entre si.

        La descripcion de search_corpus menciona get_price en texto ("NO usar
        para precios actuales"), asi que buscar el string suelto da falso
        positivo. Lo que importa es que no exista la DEFINICION de la tool.
        """
        seccion = _TOOLS_SECTION_POR_TIPO["clima"]
        assert '"name": "get_weather"' in seccion
        assert '"name": "get_price"' not in seccion
        assert '"name": "calculate_margin"' not in seccion

    def test_desconocido_incluye_todas_las_tools_habilitadas(self) -> None:
        """Sin certeza no se recorta: el modelo necesita todas las opciones.

        ``register_expense``, las tools de parcela, la de reglas agronomicas
        y la del panel quedan fuera mientras sus feature gates estén apagados
        (#170, C5, C1+C2, C3); la cobertura de esos filtros vive en
        ``test_llm_service.py``.
        """
        seccion = _TOOLS_SECTION_POR_TIPO["desconocido"]
        for tool in (
            "get_price",
            "get_price_history",
            "calculate_sale_value",
            "calculate_margin",
            "get_price_spread",
            "get_weather",
            "get_clima_historico",
            "search_corpus",
            "get_programas_indap",
        ):
            assert f'"name": "{tool}"' in seccion
        assert '"name": "register_expense"' not in seccion
        assert '"name": "register_parcela"' not in seccion
        assert '"name": "get_parcelas"' not in seccion
        assert '"name": "get_regla_agronomica"' not in seccion
        assert '"name": "get_calendario_agricola"' not in seccion
        assert '"name": "get_link_resumen"' not in seccion

    def test_agronomica_ofrece_solo_tools_citadas(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Con el gate activo, la sección agronómica no ofrece tools de precio."""
        from app.services.llm_service import _TOOLS_AGRONOMICA, _render_tools_section

        monkeypatch.setattr(settings, "agronomic_rules_enabled", True)
        seccion = _render_tools_section(_TOOLS_AGRONOMICA)

        assert '"name": "get_calendario_agricola"' in seccion
        assert '"name": "get_regla_agronomica"' in seccion
        assert '"name": "get_price"' not in seccion

    def test_recorte_reduce_el_prompt(self) -> None:
        completo = len(_TOOLS_SECTION_POR_TIPO["desconocido"])
        assert len(_TOOLS_SECTION_POR_TIPO["clima"]) < completo
        assert len(_TOOLS_SECTION_POR_TIPO["precio"]) < completo


class TestPrefijoCacheable:
    """El prefijo del system prompt debe ser estable para que el cache KV sirva."""

    def test_lo_variable_va_despues_del_bloque_de_tools(self) -> None:
        """cultivos y system_tip no deben partir el prefijo comun."""
        prefijo = SYSTEM_PROMPT + _TOOLS_SECTION_POR_TIPO["precio"]

        simple = _build_messages("hola", [], consulta_tipo="precio")
        personalizado = _build_messages("hola", [], cultivos=["papa"], system_tip="tip", consulta_tipo="precio")

        for mensajes in (simple, personalizado):
            assert str(mensajes[0]["content"]).startswith(prefijo)

    def test_mismo_tipo_produce_el_mismo_prefijo(self) -> None:
        a = _build_messages("consulta uno", [], consulta_tipo="clima")
        b = _build_messages("consulta dos", [], consulta_tipo="clima")
        assert a[0]["content"] == b[0]["content"]

    def test_tipo_invalido_cae_a_todas_las_tools(self) -> None:
        """Un tipo inesperado no debe romper: se usan todas las tools."""
        mensajes = _build_messages("hola", [], consulta_tipo="inventado")
        assert "get_weather" in str(mensajes[0]["content"])
        assert "get_price" in str(mensajes[0]["content"])
