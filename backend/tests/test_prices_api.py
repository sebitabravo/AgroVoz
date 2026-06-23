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
        engine.dispose()


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

    def test_ilike_mercado_parcial(self, db: Session) -> None:
        _insertar_precio(db, mercado="Lo Valledor Sector Mayorista")
        result = query_latest_price(db, "papa", "Lo Valledor")
        assert result is not None

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
        assert "$1.200" in texto
        assert "Lo Valledor" in texto
        assert "20/06/2026" in texto

    def test_precio_con_decimales(self, db: Session) -> None:
        registro = _insertar_precio(db, precio_kg=Decimal("1150.50"))
        texto = format_price_text(registro)
        assert "$1.150,50" in texto

    def test_precio_entero_sin_decimales(self, db: Session) -> None:
        registro = _insertar_precio(db, precio_kg=Decimal("800"))
        texto = format_price_text(registro)
        assert "$800" in texto
        assert "$800.00" not in texto
        assert "$800,00" not in texto

    def test_precio_miles_chileno_con_punto(self, db: Session) -> None:
        """Formato chileno usa punto para miles: $1.200, no $1,200."""
        registro = _insertar_precio(db, precio_kg=Decimal("1500"))
        texto = format_price_text(registro)
        assert "$1.500" in texto
        assert "$1,500" not in texto

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
            "Tomate está a $850 el kilo en Vega Central, precio del 19/06/2026."
        )


# ── get_price_for_llm ──────────────────────────────────────────────


class TestGetPriceForLlm:
    """Función principal para Tool Calling del LLM."""

    def test_devuelve_texto_con_precio_cuando_existen_datos(self, db: Session) -> None:
        _insertar_precio(db)
        texto = get_price_for_llm(db, "papa", "Lo Valledor")
        assert "Papa" in texto
        assert "$1.200" in texto

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
        texto = get_price_for_llm(db, "papa", "")
        assert "No entendí" in texto


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
        assert "$1.200" in data["texto"]

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
