"""Tests para Issue #16: Tool consulta precios ODEPA.

Cubre:
- query_latest_price: búsqueda, edge cases, normalización
- format_price_text: formato chileno, decimales
- get_price_for_llm: texto natural, fallback sin datos
- list_products / list_mercados: listados, filtros
- GET /api/v1/prices/{producto}: endpoint REST con y sin ?mercado=
"""

import datetime
from collections.abc import Generator
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.odepa_price import OdepaPrice
from app.services.odepa_service import (
    format_price_text,
    get_price_for_llm,
    get_price_history_for_llm,
    list_mercados,
    list_products,
    query_latest_by_product,
    query_latest_price,
)

# ── Helpers ────────────────────────────────────────────────────────


def _insertar_precio(
    db: Session,
    producto: str = "papa",
    mercado: str = "Lo Valledor",
    precio_kg: Decimal = Decimal("1200"),
    unidad: str = "kg",
    fecha: datetime.date | None = None,
    fuente: str = "test",
) -> OdepaPrice:
    """Inserta un registro OdepaPrice de prueba."""
    if fecha is None:
        fecha = datetime.date(2026, 6, 20)
    registro = OdepaPrice(
        producto=producto,
        mercado=mercado,
        precio_kg=precio_kg,
        unidad=unidad,
        fecha=fecha,
        fuente=fuente,
    )
    db.add(registro)
    db.commit()
    return registro


def _session_test_db(tmp_path: Path) -> Generator[Session, None, None]:
    """Session apuntando a la misma DB temporal que usa el client fixture."""
    db_path = tmp_path / "test_agrovoz.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        # engine.dispose() omitido intencionalmente: el engine del
        # fixture client es dueño del ciclo de vida del archivo SQLite.
        # Un dispose() acá compite con el engine del client fixture.


# ── query_latest_price ──────────────────────────────────────────────


class TestQueryLatestPrice:
    """Búsqueda de precio más reciente por producto+mercado."""

    def test_retorna_registro_cuando_existen_datos(self, db: Session) -> None:
        _insertar_precio(db, producto="papa", mercado="Lo Valledor", precio_kg=Decimal("1200"))
        result = query_latest_price(db, "papa", "Lo Valledor")
        assert result is not None
        assert result.producto == "papa"
        assert result.mercado == "Lo Valledor"
        assert result.precio_kg == Decimal("1200")

    def test_retorna_none_si_no_hay_datos(self, db: Session) -> None:
        result = query_latest_price(db, "papa", "Lo Valledor")
        assert result is None

    def test_retorna_registro_mas_reciente(self, db: Session) -> None:
        _insertar_precio(db, fecha=datetime.date(2026, 6, 18))
        _insertar_precio(db, fecha=datetime.date(2026, 6, 21))
        result = query_latest_price(db, "papa", "Lo Valledor")
        assert result is not None
        assert result.fecha == datetime.date(2026, 6, 21)

    def test_case_insensitive_producto(self, db: Session) -> None:
        _insertar_precio(db, producto="papa")
        result = query_latest_price(db, "PAPA", "Lo Valledor")
        assert result is not None
        assert result.producto == "papa"

    def test_case_insensitive_mercado(self, db: Session) -> None:
        _insertar_precio(db, mercado="Lo Valledor")
        result = query_latest_price(db, "papa", "lo valledor")
        assert result is not None
        assert result.mercado == "Lo Valledor"

    def test_mercado_match_exacto_no_parcial(self, db: Session) -> None:
        """Match exacto: buscar 'Lo Valledor' NO matchea 'Lo Valledor Sector Mayorista'."""
        _insertar_precio(db, mercado="Lo Valledor Sector Mayorista")
        result = query_latest_price(db, "papa", "Lo Valledor")
        assert result is None

    def test_lanza_value_error_si_producto_vacio(self, db: Session) -> None:
        with pytest.raises(ValueError, match="producto no puede estar vacío"):
            query_latest_price(db, "", "Lo Valledor")

    def test_lanza_value_error_si_producto_solo_espacios(self, db: Session) -> None:
        with pytest.raises(ValueError, match="producto no puede estar vacío"):
            query_latest_price(db, "   ", "Lo Valledor")

    def test_lanza_value_error_si_mercado_vacio(self, db: Session) -> None:
        with pytest.raises(ValueError, match="mercado no puede estar vacío"):
            query_latest_price(db, "papa", "")

    def test_lanza_value_error_si_mercado_solo_espacios(self, db: Session) -> None:
        with pytest.raises(ValueError, match="mercado no puede estar vacío"):
            query_latest_price(db, "papa", "   ")

    def test_mercado_con_acentos(self, db: Session) -> None:
        _insertar_precio(db, mercado="Concepción")
        result = query_latest_price(db, "papa", "Concepción")
        assert result is not None
        assert result.mercado == "Concepción"

    def test_producto_con_tilde(self, db: Session) -> None:
        _insertar_precio(db, producto="melón")
        result = query_latest_price(db, "melón", "Lo Valledor")
        assert result is not None
        assert result.producto == "melón"

    def test_mercado_con_porcentaje_no_expande_wildcard(self, db: Session) -> None:
        """El carácter % en el input no debe actuar como comodín LIKE."""
        _insertar_precio(db, mercado="Lo Valledor")
        result = query_latest_price(db, "papa", "%")
        assert result is None

    def test_mercado_con_guion_bajo_no_expande_wildcard(self, db: Session) -> None:
        """El carácter _ en el input no debe actuar como comodín LIKE."""
        _insertar_precio(db, mercado="Lo Valledor")
        result = query_latest_price(db, "papa", "_")
        assert result is None

    def test_mercado_con_backslash_real(self, db: Session) -> None:
        """Un backslash literal en el input debe buscarse como carácter normal."""
        _insertar_precio(db, mercado="Mercado\\Sur")
        result = query_latest_price(db, "papa", "Mercado\\Sur")
        assert result is not None
        assert result.mercado == "Mercado\\Sur"


# ── query_latest_by_product ───────────────────────────────────────────


class TestQueryLatestByProduct:
    """Precio más reciente por mercado para un producto — una sola query."""

    def test_retorna_un_registro_por_mercado(self, db: Session) -> None:
        _insertar_precio(db, producto="papa", mercado="Lo Valledor", fecha=datetime.date(2026, 6, 20))
        _insertar_precio(db, producto="papa", mercado="Vega Central", fecha=datetime.date(2026, 6, 21))
        result = query_latest_by_product(db, "papa")
        assert len(result) == 2
        assert "Lo Valledor" in result
        assert "Vega Central" in result

    def test_retorna_precio_mas_reciente_por_mercado(self, db: Session) -> None:
        _insertar_precio(db, producto="papa", mercado="Lo Valledor",
                         precio_kg=Decimal("1000"), fecha=datetime.date(2026, 6, 18))
        _insertar_precio(db, producto="papa", mercado="Lo Valledor",
                         precio_kg=Decimal("1200"), fecha=datetime.date(2026, 6, 21))
        result = query_latest_by_product(db, "papa")
        assert result["Lo Valledor"].precio_kg == Decimal("1200")
        assert result["Lo Valledor"].fecha == datetime.date(2026, 6, 21)

    def test_retorna_dict_vacio_si_no_hay_datos(self, db: Session) -> None:
        result = query_latest_by_product(db, "zanahoria")
        assert result == {}

    def test_case_insensitive(self, db: Session) -> None:
        _insertar_precio(db, producto="papa", mercado="Lo Valledor")
        result = query_latest_by_product(db, "PAPA")
        assert len(result) == 1

    def test_lanza_value_error_si_producto_vacio(self, db: Session) -> None:
        with pytest.raises(ValueError, match="producto no puede estar vacío"):
            query_latest_by_product(db, "")

    def test_lanza_value_error_si_producto_solo_espacios(self, db: Session) -> None:
        with pytest.raises(ValueError, match="producto no puede estar vacío"):
            query_latest_by_product(db, "   ")


# ── format_price_text ──────────────────────────────────────────────


class TestFormatPriceText:
    """Formateo de precio como texto natural chileno."""

    def test_formato_basico(self, db: Session) -> None:
        registro = _insertar_precio(db, producto="papa", precio_kg=Decimal("1200"))
        texto = format_price_text(registro)
        assert "Papa" in texto
        assert "1.200 pesos" in texto
        assert "Lo Valledor" in texto
        assert "20/06/2026" in texto
        assert "$" not in texto

    def test_precio_con_decimales(self, db: Session) -> None:
        registro = _insertar_precio(db, precio_kg=Decimal("1150.50"))
        texto = format_price_text(registro)
        assert "1.150 coma 50 pesos" in texto
        assert "$" not in texto

    def test_precio_entero_sin_decimales(self, db: Session) -> None:
        registro = _insertar_precio(db, precio_kg=Decimal("800"))
        texto = format_price_text(registro)
        assert "800 pesos" in texto
        assert "$" not in texto
        assert "800.00" not in texto
        assert "800,00" not in texto

    def test_precio_miles_chileno_con_punto(self, db: Session) -> None:
        """Formato chileno usa punto para miles."""
        registro = _insertar_precio(db, precio_kg=Decimal("1500"))
        texto = format_price_text(registro)
        assert "1.500 pesos" in texto
        assert "1,500" not in texto
        assert "$" not in texto

    def test_texto_contiene_frase_completa(self, db: Session) -> None:
        registro = _insertar_precio(
            db,
            producto="tomate",
            mercado="Vega Central",
            precio_kg=Decimal("850"),
            fecha=datetime.date(2026, 6, 19),
        )
        texto = format_price_text(registro)
        assert texto == (
            "Tomate está a 850 pesos el kilo en Vega Central, precio del 19/06/2026."
        )


class TestFormatPriceTextUnidades:
    """Regresión Issue #81: el texto debe respetar la unidad de venta ODEPA.

    Bug original: format_price_text decía siempre "el kilo" aunque la unidad
    fuera "$/saco 25 kilos" — el agricultor escuchaba un precio 25x el real.
    """

    def test_saco_25_kilos_dice_unidad_real_y_equivalencia(self, db: Session) -> None:
        registro = _insertar_precio(
            db,
            precio_kg=Decimal("8833.33"),
            unidad="$/saco 25 kilos",
            fecha=datetime.date(2026, 7, 3),
        )
        texto = format_price_text(registro)
        assert texto == (
            "Papa está a 8.833 coma 33 pesos por saco de 25 kilos en Lo Valledor, "
            "unos 353 pesos el kilo, precio del 03/07/2026."
        )

    def test_bandeja_con_sufijo_granel_es_convertible(self, db: Session) -> None:
        registro = _insertar_precio(
            db, precio_kg=Decimal("9000"), unidad="$/bandeja 18 kilos granel"
        )
        texto = format_price_text(registro)
        assert "por bandeja de 18 kilos granel" in texto
        assert "unos 500 pesos el kilo" in texto

    def test_bins_con_parentesis_es_convertible(self, db: Session) -> None:
        registro = _insertar_precio(
            db, precio_kg=Decimal("200000"), unidad="$/bins (400 kilos)"
        )
        texto = format_price_text(registro)
        assert "200.000 pesos por bins de 400 kilos" in texto
        assert "unos 500 pesos el kilo" in texto

    def test_docena_de_atados_no_inventa_conversion(self, db: Session) -> None:
        registro = _insertar_precio(
            db, producto="cilantro", precio_kg=Decimal("1200"),
            unidad="$/docena de atados",
        )
        texto = format_price_text(registro)
        assert "por docena de atados" in texto
        assert "el kilo" not in texto
        assert "unos" not in texto

    def test_caja_por_unidades_no_inventa_conversion(self, db: Session) -> None:
        registro = _insertar_precio(
            db, producto="lechuga", precio_kg=Decimal("15000"),
            unidad="$/caja 50 unidades",
        )
        texto = format_price_text(registro)
        assert "por caja de 50 unidades" in texto
        assert "el kilo" not in texto

    def test_atado_con_rango_no_inventa_conversion(self, db: Session) -> None:
        registro = _insertar_precio(
            db, producto="acelga", precio_kg=Decimal("800"),
            unidad="$/atado 0,5 a 1 kilo",
        )
        texto = format_price_text(registro)
        assert "por atado de 0,5 a 1 kilo" in texto
        assert "unos" not in texto

    def test_unidad_generica_dice_por_unidad(self, db: Session) -> None:
        registro = _insertar_precio(
            db, producto="piña", precio_kg=Decimal("1500"), unidad="$/unidad"
        )
        texto = format_price_text(registro)
        assert "1.500 pesos por unidad" in texto
        assert "el kilo" not in texto

    def test_dolar_kilo_con_parentesis_usa_formato_kilo(self, db: Session) -> None:
        registro = _insertar_precio(
            db, precio_kg=Decimal("450"), unidad="$/kilo (en caja de 17 kilos)"
        )
        texto = format_price_text(registro)
        assert "450 pesos el kilo en" in texto
        assert "por " not in texto

    def test_envase_1_kilo_sin_equivalencia_redundante(self, db: Session) -> None:
        registro = _insertar_precio(
            db, producto="jengibre", precio_kg=Decimal("4000"), unidad="$/envase 1 kilo"
        )
        texto = format_price_text(registro)
        assert "4.000 pesos por envase de 1 kilo" in texto
        assert "unos" not in texto


# ── get_price_for_llm ──────────────────────────────────────────────


class TestGetPriceForLlm:
    """Función principal para Tool Calling del LLM."""

    def test_devuelve_texto_con_precio_cuando_existen_datos(self, db: Session) -> None:
        _insertar_precio(db)
        texto = get_price_for_llm(db, "papa", "Lo Valledor")
        assert "Papa" in texto
        assert "1.200 pesos" in texto
        assert "$" not in texto

    def test_devuelve_mensaje_sin_datos_producto_no_existe(self, db: Session) -> None:
        texto = get_price_for_llm(db, "zanahoria", "Lo Valledor")
        assert "No tengo datos de precio" in texto
        assert "zanahoria" in texto

    def test_devuelve_mensaje_sin_datos_mercado_no_existe(self, db: Session) -> None:
        _insertar_precio(db)
        texto = get_price_for_llm(db, "papa", "Mercado Inexistente")
        assert "No tengo datos de precio" in texto

    def test_devuelve_fallback_si_producto_vacio(self, db: Session) -> None:
        texto = get_price_for_llm(db, "", "Lo Valledor")
        assert "No entendí" in texto

    def test_devuelve_fallback_si_mercado_vacio(self, db: Session) -> None:
        """Cuando mercado esta vacio, busca en todos los mercados.

        Si no hay datos para el producto en ningun mercado, informa.
        """
        texto = get_price_for_llm(db, "papa", "")
        # Sin datos insertados, query_latest_by_product no encuentra nada.
        assert "No tengo datos de precio" in texto
        assert "papa" in texto


class TestGetPriceForLlmSeleccionMercado:
    """Regresión Issue #83: selección de mercado cuando no se especifica uno.

    Bug original: prioridad por clave exacta "Lo Valledor" solo matcheaba
    seeds de demo; con DB real caía al fallback alfabético (Arica).
    """

    def test_prioriza_mercado_valledor_real_de_odepa(self, db: Session) -> None:
        _insertar_precio(
            db, mercado="Agrícola del Norte S.A. de Arica",
            precio_kg=Decimal("12833.33"), unidad="$/saco 25 kilos",
            fecha=datetime.date(2026, 7, 2),
        )
        _insertar_precio(
            db, mercado="Mercado Mayorista Lo Valledor de Santiago",
            precio_kg=Decimal("8833.33"), unidad="$/saco 25 kilos",
            fecha=datetime.date(2026, 7, 3),
        )
        texto = get_price_for_llm(db, "papa", "")
        assert "Lo Valledor" in texto
        assert "Arica" not in texto

    def test_entre_varios_valledor_gana_el_dato_mas_reciente(
        self, db: Session
    ) -> None:
        # Seed de demo con nombre corto y fecha vieja (fuente=test)
        _insertar_precio(
            db, mercado="Lo Valledor", precio_kg=Decimal("1200"),
            fecha=datetime.date(2026, 6, 20), fuente="test",
        )
        _insertar_precio(
            db, mercado="Mercado Mayorista Lo Valledor de Santiago",
            precio_kg=Decimal("8833.33"), unidad="$/saco 25 kilos",
            fecha=datetime.date(2026, 7, 3),
        )
        texto = get_price_for_llm(db, "papa", "")
        assert "8.833" in texto
        assert "1.200" not in texto

    def test_sin_valledor_gana_el_mas_reciente_no_el_alfabetico(
        self, db: Session
    ) -> None:
        _insertar_precio(
            db, mercado="Agrícola del Norte S.A. de Arica",
            precio_kg=Decimal("500"), fecha=datetime.date(2026, 6, 25),
        )
        _insertar_precio(
            db, mercado="Vega Modelo de Temuco",
            precio_kg=Decimal("700"), fecha=datetime.date(2026, 7, 3),
        )
        texto = get_price_for_llm(db, "papa", "")
        assert "Temuco" in texto
        assert "Arica" not in texto


class TestGetPriceHistoryForLlm:
    """Tool get_price_history (Issue #85): comparación con precio histórico."""

    def test_compara_precio_actual_con_hace_7_dias(self, db: Session) -> None:
        _insertar_precio(
            db, mercado="Mercado Mayorista Lo Valledor de Santiago",
            precio_kg=Decimal("10000"), unidad="$/saco 25 kilos",
            fecha=datetime.date(2026, 6, 26),
        )
        _insertar_precio(
            db, mercado="Mercado Mayorista Lo Valledor de Santiago",
            precio_kg=Decimal("8833.33"), unidad="$/saco 25 kilos",
            fecha=datetime.date(2026, 7, 3),
        )
        texto = get_price_history_for_llm(db, "papa", dias=7)
        assert "8.833 coma 33 pesos por saco de 25 kilos" in texto
        assert "Hace 7 días estaba a 10.000 pesos" in texto
        assert "ha bajado un 11 coma 7 por ciento" in texto

    def test_precio_al_alza_dice_subido(self, db: Session) -> None:
        _insertar_precio(
            db, precio_kg=Decimal("1000"), fecha=datetime.date(2026, 6, 26)
        )
        _insertar_precio(
            db, precio_kg=Decimal("1200"), fecha=datetime.date(2026, 7, 3)
        )
        texto = get_price_history_for_llm(db, "papa", dias=7)
        assert "ha subido un 20 por ciento" in texto

    def test_sin_historico_responde_honesto_con_precio_actual(
        self, db: Session
    ) -> None:
        _insertar_precio(
            db, precio_kg=Decimal("1200"), fecha=datetime.date(2026, 7, 3)
        )
        texto = get_price_history_for_llm(db, "papa", dias=7)
        assert "1.200 pesos" in texto
        assert "No tengo datos comparables de hace 7 días" in texto

    def test_no_compara_unidades_distintas(self, db: Session) -> None:
        # Hace 7 días se vendía por malla; hoy por saco. Comparar sería falso.
        _insertar_precio(
            db, precio_kg=Decimal("10766.66"), unidad="$/malla 25 kilos",
            fecha=datetime.date(2026, 6, 26),
        )
        _insertar_precio(
            db, precio_kg=Decimal("8833.33"), unidad="$/saco 25 kilos",
            fecha=datetime.date(2026, 7, 3),
        )
        texto = get_price_history_for_llm(db, "papa", dias=7)
        assert "No tengo datos comparables" in texto
        assert "10.766" not in texto

    def test_usa_punto_historico_mas_cercano_al_limite(self, db: Session) -> None:
        # Hay datos de hace 14 y de hace 8 días: debe comparar contra el de 8
        # (el más reciente que cumple la distancia mínima pedida).
        _insertar_precio(
            db, precio_kg=Decimal("900"), fecha=datetime.date(2026, 6, 19)
        )
        _insertar_precio(
            db, precio_kg=Decimal("1000"), fecha=datetime.date(2026, 6, 25)
        )
        _insertar_precio(
            db, precio_kg=Decimal("1200"), fecha=datetime.date(2026, 7, 3)
        )
        texto = get_price_history_for_llm(db, "papa", dias=7)
        assert "Hace 8 días estaba a 1.000 pesos" in texto

    def test_producto_sin_datos(self, db: Session) -> None:
        texto = get_price_history_for_llm(db, "zanahoria")
        assert "No tengo datos de precio" in texto

    def test_producto_vacio(self, db: Session) -> None:
        texto = get_price_history_for_llm(db, "")
        assert "No entendí el producto" in texto

    def test_dias_invalido_usa_default(self, db: Session) -> None:
        _insertar_precio(
            db, precio_kg=Decimal("1000"), fecha=datetime.date(2026, 6, 26)
        )
        _insertar_precio(
            db, precio_kg=Decimal("1200"), fecha=datetime.date(2026, 7, 3)
        )
        texto = get_price_history_for_llm(db, "papa", dias="no-numero")  # type: ignore[arg-type]
        assert "Hace 7 días" in texto

    def test_variacion_minima_dice_se_mantiene(self, db: Session) -> None:
        _insertar_precio(
            db, precio_kg=Decimal("1000.00"), fecha=datetime.date(2026, 6, 26)
        )
        _insertar_precio(
            db, precio_kg=Decimal("1000.20"), fecha=datetime.date(2026, 7, 3)
        )
        texto = get_price_history_for_llm(db, "papa", dias=7)
        assert "se mantiene igual" in texto


# ── list_products ──────────────────────────────────────────────────


class TestListProducts:
    """Listado de productos disponibles."""

    def test_lista_productos_ordenados(self, db: Session) -> None:
        _insertar_precio(db, producto="tomate")
        _insertar_precio(db, producto="papa")
        _insertar_precio(db, producto="cebolla")
        productos = list_products(db)
        assert productos == ["cebolla", "papa", "tomate"]

    def test_lista_vacia_sin_datos(self, db: Session) -> None:
        productos = list_products(db)
        assert productos == []

    def test_sin_duplicados(self, db: Session) -> None:
        _insertar_precio(db, producto="papa", fecha=datetime.date(2026, 6, 20))
        _insertar_precio(db, producto="papa", fecha=datetime.date(2026, 6, 21))
        productos = list_products(db)
        assert productos == ["papa"]


# ── list_mercados ──────────────────────────────────────────────────


class TestListMercados:
    """Listado de mercados disponibles."""

    def test_lista_mercados_ordenados(self, db: Session) -> None:
        _insertar_precio(db, mercado="Vega Central")
        _insertar_precio(db, mercado="Lo Valledor")
        _insertar_precio(db, mercado="Agro Centro")
        mercados = list_mercados(db)
        assert mercados == ["Agro Centro", "Lo Valledor", "Vega Central"]

    def test_filtra_por_producto(self, db: Session) -> None:
        _insertar_precio(db, producto="papa", mercado="Lo Valledor")
        _insertar_precio(db, producto="tomate", mercado="Vega Central")
        mercados = list_mercados(db, producto="papa")
        assert mercados == ["Lo Valledor"]

    def test_lista_vacia_sin_datos(self, db: Session) -> None:
        mercados = list_mercados(db)
        assert mercados == []

    def test_sin_duplicados(self, db: Session) -> None:
        _insertar_precio(db, mercado="Lo Valledor", fecha=datetime.date(2026, 6, 20))
        _insertar_precio(db, mercado="Lo Valledor", fecha=datetime.date(2026, 6, 21))
        mercados = list_mercados(db)
        assert mercados == ["Lo Valledor"]


# ── API Endpoint ───────────────────────────────────────────────────


class TestPricesApiEndpoint:
    """Tests de integración para GET /api/v1/prices/{producto}."""

    async def test_get_producto_con_mercado_devuelve_precio(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """Caso feliz: producto + mercado específicos."""
        with next(_session_test_db(tmp_path)) as db:
            _insertar_precio(db, producto="papa", mercado="Lo Valledor", precio_kg=Decimal("1200"))

        response = await client.get("/api/v1/prices/papa?mercado=Lo+Valledor")
        assert response.status_code == 200
        data = response.json()
        assert data["producto"] == "papa"
        assert data["mercado"] == "Lo Valledor"
        assert data["precio_kg"] == 1200.0
        assert data["unidad"] == "kg"
        assert "Papa" in data["texto"]
        assert "1.200 pesos" in data["texto"]
        assert "$" not in data["texto"]

    async def test_get_producto_sin_mercado_devuelve_todos(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _insertar_precio(
                db, producto="papa", mercado="Lo Valledor",
                precio_kg=Decimal("1200"), fecha=datetime.date(2026, 6, 20),
            )
            _insertar_precio(
                db, producto="papa", mercado="Vega Central",
                precio_kg=Decimal("1150.50"), fecha=datetime.date(2026, 6, 21),
            )

        response = await client.get("/api/v1/prices/papa")
        assert response.status_code == 200
        data = response.json()
        assert data["producto"] == "papa"
        assert data["total_mercados"] == 2
        assert len(data["precios"]) == 2
        mercados = [p["mercado"] for p in data["precios"]]
        assert "Lo Valledor" in mercados
        assert "Vega Central" in mercados

    async def test_get_producto_inexistente_devuelve_404(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/api/v1/prices/zanahoria")
        assert response.status_code == 404
        data = response.json()
        assert "zanahoria" in data["detail"]

    async def test_get_producto_vacio_devuelve_400(
        self, client: AsyncClient
    ) -> None:
        # FastAPI parsea %20%20 como "  " (espacios)
        response = await client.get("/api/v1/prices/%20%20")
        assert response.status_code == 400
        data = response.json()
        assert "producto" in data["detail"].lower()

    async def test_get_mercado_solo_espacios_devuelve_400(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """Mercado con solo espacios debe devolver 400, no 500."""
        with next(_session_test_db(tmp_path)) as db:
            _insertar_precio(db, producto="papa", mercado="Lo Valledor")

        response = await client.get("/api/v1/prices/papa?mercado=%20%20%20")
        assert response.status_code == 400
        data = response.json()
        assert "mercado" in data["detail"].lower()

    async def test_get_mercado_inexistente_devuelve_404(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _insertar_precio(db, producto="papa", mercado="Lo Valledor")

        response = await client.get("/api/v1/prices/papa?mercado=Mercado+Falso")
        assert response.status_code == 404
        data = response.json()
        assert "Mercado Falso" in data["detail"]

    async def test_response_tiene_texto_para_tts(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _insertar_precio(db, producto="papa", mercado="Lo Valledor")

        response = await client.get("/api/v1/prices/papa?mercado=Lo+Valledor")
        assert response.status_code == 200
        data = response.json()
        assert "texto" in data
        assert len(data["texto"]) > 0
        # El texto debe ser legible para Piper TTS
        assert "está a" in data["texto"]
        assert "el kilo" in data["texto"]


# ── Endpoints de descubrimiento ──────────────────────────────────────


class TestProductsEndpoint:
    """Tests para GET /api/v1/products."""

    async def test_lista_productos_vacia_sin_datos(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/api/v1/products")
        assert response.status_code == 200
        assert response.json() == []

    async def test_lista_productos_con_datos(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _insertar_precio(db, producto="papa", mercado="Lo Valledor")
            _insertar_precio(db, producto="tomate", mercado="Vega Central")

        response = await client.get("/api/v1/products")
        assert response.status_code == 200
        data = response.json()
        assert "papa" in data
        assert "tomate" in data
        assert data == sorted(data)  # ordenado A-Z

    async def test_productos_sin_duplicados(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _insertar_precio(
                db, producto="papa", mercado="Lo Valledor",
                fecha=datetime.date(2026, 6, 20),
            )
            _insertar_precio(
                db, producto="papa", mercado="Vega Central",
                fecha=datetime.date(2026, 6, 20),
            )
            _insertar_precio(
                db, producto="papa", mercado="Lo Valledor",
                fecha=datetime.date(2026, 6, 21),
            )

        response = await client.get("/api/v1/products")
        data = response.json()
        assert data == ["papa"]


class TestMercadosEndpoint:
    """Tests para GET /api/v1/mercados."""

    async def test_lista_mercados_vacia_sin_datos(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/api/v1/mercados")
        assert response.status_code == 200
        assert response.json() == []

    async def test_lista_mercados_con_datos(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _insertar_precio(db, producto="papa", mercado="Lo Valledor")
            _insertar_precio(db, producto="tomate", mercado="Vega Central")

        response = await client.get("/api/v1/mercados")
        assert response.status_code == 200
        data = response.json()
        assert "Lo Valledor" in data
        assert "Vega Central" in data
        assert data == sorted(data)

    async def test_filtra_mercados_por_producto(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _insertar_precio(db, producto="papa", mercado="Lo Valledor")
            _insertar_precio(db, producto="tomate", mercado="Vega Central")

        response = await client.get("/api/v1/mercados?producto=papa")
        data = response.json()
        assert data == ["Lo Valledor"]

    async def test_producto_inexistente_devuelve_lista_vacia(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _insertar_precio(db, producto="papa", mercado="Lo Valledor")

        response = await client.get("/api/v1/mercados?producto=zanahoria")
        assert response.status_code == 200
        assert response.json() == []

    async def test_mercados_sin_duplicados(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _insertar_precio(
                db, producto="papa", mercado="Lo Valledor",
                fecha=datetime.date(2026, 6, 20),
            )
            _insertar_precio(
                db, producto="papa", mercado="Lo Valledor",
                fecha=datetime.date(2026, 6, 21),
            )

        response = await client.get("/api/v1/mercados")
        data = response.json()
        assert data == ["Lo Valledor"]
