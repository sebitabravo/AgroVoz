"""Tests del fast-path determinista que evita el LLM.

El LLM sobre CPU limitada gasta la mayor parte de la latencia leyendo el prompt
de tools (~2779 tokens). Para la consulta directa y clasificada con certeza, las
tools deterministas ya arman la frase final, asi que el pipeline la responde sin
invocar al modelo. Estos tests fijan el gate: cuando aplica y cuando NO, para que
un cambio futuro no lo abra de mas y degrade la calidad de las respuestas.
"""

import builtins

import pytest

from app.schemas.variables import ExtractedVariables
from app.services.llm_keywords import _force_corpus_search
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

    async def test_sin_keywords_de_corpus_no_importa_rag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Una consulta de clima no debe siquiera tocar rag_service."""
        real_import = builtins.__import__

        def import_espia(name: str, *args: object, **kwargs: object) -> object:
            if "rag_service" in name:
                raise AssertionError("no debe importarse rag_service sin keywords")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", import_espia)
        assert await _force_corpus_search("¿como esta el clima hoy?") is None

    async def test_dependencia_faltante_degrada_sin_romper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Con keywords de corpus pero sin la dependencia: None, no excepcion."""
        real_import = builtins.__import__

        def import_roto(name: str, *args: object, **kwargs: object) -> object:
            if "rag_service" in name:
                raise ImportError("No module named 'sklearn'")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", import_roto)
        assert await _force_corpus_search("¿que dice el boletin de ODEPA?") is None


class TestGateFastPath:
    """Cuando el pipeline puede saltarse el LLM."""

    def test_precio_con_producto_y_consulta_corta_usa_fast_path(self) -> None:
        assert AgroVozPipeline._puede_usar_fast_path(
            "¿a cuánto está la papa?", _vars("precio", "papa"), None
        )

    def test_clima_usa_fast_path_sin_requerir_producto(self) -> None:
        assert AgroVozPipeline._puede_usar_fast_path(
            "¿va a llover mañana?", _vars("clima"), None
        )

    def test_precio_sin_producto_no_usa_fast_path(self) -> None:
        """Sin producto identificado el atajo no puede resolver la consulta."""
        assert not AgroVozPipeline._puede_usar_fast_path(
            "¿a cuánto está?", _vars("precio"), None
        )

    def test_tipo_ambos_no_usa_fast_path(self) -> None:
        """Precio y clima juntos necesitan al LLM para combinar las respuestas."""
        assert not AgroVozPipeline._puede_usar_fast_path(
            "precio de la papa y el clima", _vars("ambos", "papa"), None
        )

    def test_tipo_desconocido_no_usa_fast_path(self) -> None:
        assert not AgroVozPipeline._puede_usar_fast_path(
            "una consulta rara", _vars("desconocido"), None
        )

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
        assert not AgroVozPipeline._puede_usar_fast_path(
            "¿por qué subió la papa?", _vars("precio", "papa"), None
        )

    def test_consulta_larga_no_usa_fast_path(self) -> None:
        """Una consulta larga suele traer contexto que el atajo ignoraria."""
        larga = "hola buenas tardes " * 12
        assert len(larga) > AgroVozPipeline._FAST_PATH_MAX_CHARS
        assert not AgroVozPipeline._puede_usar_fast_path(
            larga, _vars("precio", "papa"), None
        )


class TestMercadoEspecifico:
    """Si el productor nombra un mercado, el atajo no puede resolverlo.

    Regresion real detectada en prueba E2E: se pregunto por Vega Central y el
    atajo respondio con Lo Valledor, porque resuelve el mercado por el telefono
    del productor y no lee el nombre de la consulta.
    """

    def test_vega_central_no_usa_fast_path(self) -> None:
        assert not AgroVozPipeline._puede_usar_fast_path(
            "precio de la papa en la Vega Central", _vars("precio", "papa"), None
        )

    def test_lo_valledor_no_usa_fast_path(self) -> None:
        assert not AgroVozPipeline._puede_usar_fast_path(
            "cuanto vale la papa en Lo Valledor", _vars("precio", "papa"), None
        )

    def test_mercado_generico_si_usa_fast_path(self) -> None:
        """"en el mercado" a secas no nombra un mercado: el atajo aplica."""
        assert AgroVozPipeline._puede_usar_fast_path(
            "¿a cómo está la papa en el mercado?", _vars("precio", "papa"), None
        )

    def test_feria_generica_si_usa_fast_path(self) -> None:
        assert AgroVozPipeline._puede_usar_fast_path(
            "precio de la papa en la feria", _vars("precio", "papa"), None
        )


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

    def test_desconocido_incluye_las_nueve_tools(self) -> None:
        """Sin certeza no se recorta: el modelo necesita todas las opciones."""
        seccion = _TOOLS_SECTION_POR_TIPO["desconocido"]
        for tool in (
            "get_price", "get_price_history", "calculate_sale_value",
            "calculate_margin", "get_price_spread", "get_weather",
            "get_clima_historico", "search_corpus", "register_expense",
        ):
            assert f'"name": "{tool}"' in seccion

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
        personalizado = _build_messages(
            "hola", [], cultivos=["papa"], system_tip="tip", consulta_tipo="precio"
        )

        for mensajes in (simple, personalizado):
            assert str(mensajes[0]["content"]).startswith(prefijo)

    def test_mismo_tipo_produce_el_mismo_prefijo(self) -> None:
        a = _build_messages("consulta uno", [], consulta_tipo="clima")
        b = _build_messages("consulta dos", [], consulta_tipo="clima")
        assert a[0]["content"] == b[0]["content"]

    def test_tipo_invalido_cae_a_todas_las_tools(self) -> None:
        """Un tipo inesperado no debe romper: se usan las 9 tools."""
        mensajes = _build_messages("hola", [], consulta_tipo="inventado")
        assert "get_weather" in str(mensajes[0]["content"])
        assert "get_price" in str(mensajes[0]["content"])
