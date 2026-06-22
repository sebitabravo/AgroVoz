"""Tests para app.services.odepa_service: descarga, parseo y upsert ODEPA.

Cobertura: parser CSV (happy path, columnas flexibles, filas inválidas),
upsert (insert/update/idempotencia), download_csv (httpx mockeado, sin red)
y orquestador sync_odepa con session inyectada.
"""

import datetime
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.orm import Session

from app.models.odepa_price import OdepaPrice
from app.services.odepa_service import (
    OdepaCsvRecord,
    OdepaSyncError,
    SyncResult,
    _normalizar_precio,
    _parsear_precio,
    download_csv,
    parse_csv,
    sync_odepa,
    upsert_prices,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "odepa_sample.csv"

# CSV en string para tests que no leen de archivo.
_CSV_FIJO = (
    "producto,mercado,precio_promedio,unidad,fecha\n"
    "papa,Lo Valledor,800,kg,2026-06-20\n"
    "papa,Vega Central,750,kg,2026-06-20\n"
    "papa,Temuco,820,kg,2026-06-20\n"
    "tomate,Lo Valledor,1500,kg,2026-06-20\n"
    "papa,Lo Valledor,810,kg,2026-06-21\n"
)


def _csv_fake_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[type, httpx.AsyncClient]:
    """Construye un reemplazo de httpx.AsyncClient backed by MockTransport.

    El caller debe cerrar el cliente retornado al terminar el test.
    """
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient(transport=transport)

    class _CtxAsyncClient:
        """Stub de AsyncClient: __aenter__ devuelve el cliente con MockTransport."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> httpx.AsyncClient:
            return real_client

        async def __aexit__(self, *args: object) -> None:
            pass

    return _CtxAsyncClient, real_client


class TestNormalizarPrecio:
    """Normalización de formato numérico chileno a anglosajón."""

    def test_entero_sin_separadores(self) -> None:
        assert _normalizar_precio("1500") == "1500"

    def test_coma_decimal(self) -> None:
        """'800,50' (coma decimal) -> '800.50'."""
        assert _normalizar_precio("800,50") == "800.50"

    def test_punto_miles_simple(self) -> None:
        """'1.500' (punto miles) -> '1500'."""
        assert _normalizar_precio("1.500") == "1500"

    def test_punto_miles_multiple(self) -> None:
        """'1.500.000' (puntos miles) -> '1500000'."""
        assert _normalizar_precio("1.500.000") == "1500000"

    def test_punto_miles_y_coma_decimal(self) -> None:
        """'1.500,50' (formato chileno completo) -> '1500.50'."""
        assert _normalizar_precio("1.500,50") == "1500.50"

    def test_decimal_anglosajon_intacto(self) -> None:
        """'800.50' (2 decimales, no miles) se preserva."""
        assert _normalizar_precio("800.50") == "800.50"

    def test_simbolo_moneda_y_espacios(self) -> None:
        """'$ 1.500' -> '1500'."""
        assert _normalizar_precio("$ 1.500") == "1500"


class TestParsearPrecio:
    """_parsear_precio con formato chileno y casos inválidos."""

    def test_punto_miles_a_decimal(self) -> None:
        """'1.500' se interpreta como 1500, no como 1.5."""
        assert _parsear_precio("1.500") == Decimal("1500")

    def test_coma_decimal(self) -> None:
        assert _parsear_precio("800,50") == Decimal("800.50")

    def test_formato_chileno_completo(self) -> None:
        assert _parsear_precio("1.500,50") == Decimal("1500.50")

    def test_entero_plano(self) -> None:
        assert _parsear_precio("800") == Decimal("800")

    def test_vacio_lanza_value_error(self) -> None:
        with pytest.raises(ValueError, match="vacío"):
            _parsear_precio("")

    def test_no_numerico_lanza_value_error(self) -> None:
        with pytest.raises(ValueError, match="inválido"):
            _parsear_precio("ABC")


class TestParseCsv:
    """Parser CSV -> OdepaCsvRecord."""

    def test_parsea_todas_las_filas(self) -> None:
        """Sin filtro, parsea las 5 filas del fixture."""
        registros = parse_csv(_CSV_FIJO)
        assert len(registros) == 5

    def test_filtro_producto_papa(self) -> None:
        """Con filtro ['papa'], excluye tomate (4 filas)."""
        registros = parse_csv(_CSV_FIJO, productos_filter=["papa"])
        assert len(registros) == 4
        assert all(r.producto == "papa" for r in registros)

    def test_producto_normalizado_lowercase(self) -> None:
        """'PAPA' en el CSV se normaliza a 'papa' para match con filtro."""
        csv_mayus = "producto,mercado,precio,unidad,fecha\nPAPA,Lo Valledor,800,kg,2026-06-20\n"
        registros = parse_csv(csv_mayus, productos_filter=["papa"])
        assert len(registros) == 1
        assert registros[0].producto == "papa"

    def test_campos_parseados_correctos(self) -> None:
        """Precio, unidad y fecha se tipan correctamente."""
        registros = parse_csv(_CSV_FIJO, productos_filter=["papa"])
        primero = registros[0]
        assert primero.mercado == "Lo Valledor"
        assert primero.precio_kg == Decimal("800")
        assert primero.unidad == "kg"
        assert primero.fecha == datetime.date(2026, 6, 20)

    def test_nombres_columna_flexibles(self) -> None:
        """Headers con sinónimos ('Mercado Mayorista', 'Día') se resuelven."""
        csv_alt = (
            "Productos,Mercado Mayorista,Precio promedio,Medida,Día\n"
            "papa,Lo Valledor,800,kg,2026-06-20\n"
        )
        registros = parse_csv(csv_alt)
        assert len(registros) == 1
        assert registros[0].producto == "papa"
        assert registros[0].mercado == "Lo Valledor"

    def test_csv_vacio_lanza_error(self) -> None:
        with pytest.raises(OdepaSyncError, match="vacío"):
            parse_csv("")

    def test_csv_sin_columna_precio_lanza_error(self) -> None:
        """Falta columna de precio (schema roto) aborta el batch."""
        csv_sin_precio = "producto,mercado,unidad,fecha\npapa,Lo Valledor,kg,2026-06-20\n"
        with pytest.raises(OdepaSyncError, match="precio"):
            parse_csv(csv_sin_precio)

    def test_fila_precio_invalido_se_omite(self) -> None:
        """Fila con precio no numérico se omite; el resto del batch se carga."""
        csv_invalido = (
            "producto,mercado,precio,unidad,fecha\n"
            "papa,Lo Valledor,ABC,kg,2026-06-20\n"
            "papa,Temuco,800,kg,2026-06-20\n"
        )
        registros = parse_csv(csv_invalido)
        assert len(registros) == 1
        assert registros[0].mercado == "Temuco"

    def test_fila_fecha_invalida_se_omite(self) -> None:
        """Fila con fecha no parseable se omite sin abortar el batch."""
        csv_fecha_mala = (
            "producto,mercado,precio,unidad,fecha\n"
            "papa,Lo Valledor,800,kg,no-es-fecha\n"
            "papa,Temuco,800,kg,2026-06-20\n"
        )
        registros = parse_csv(csv_fecha_mala)
        assert len(registros) == 1
        assert registros[0].mercado == "Temuco"

    def test_unidad_default_kg_si_vacia(self) -> None:
        """Columna de unidad ausente o vacía -> default 'kg'."""
        csv_sin_unidad = "producto,mercado,precio,fecha\npapa,Lo Valledor,800,2026-06-20\n"
        registros = parse_csv(csv_sin_unidad)
        assert registros[0].unidad == "kg"

    def test_formato_fecha_dd_mm_aaaa(self) -> None:
        """Formato dd/mm/yyyy se parsea (ODEPA históricamente lo usa)."""
        csv_fecha = "producto,mercado,precio,unidad,fecha\npapa,Lo Valledor,800,kg,20/06/2026\n"
        registros = parse_csv(csv_fecha)
        assert registros[0].fecha == datetime.date(2026, 6, 20)

    def test_fixture_archivo_parsea(self) -> None:
        """El fixture odepa_sample.csv del repo parsea limpio."""
        contenido = _FIXTURE.read_text(encoding="utf-8")
        registros = parse_csv(contenido)
        assert len(registros) == 5


class TestUpsertPrices:
    """Upsert en odepa_prices: insert, update e idempotencia."""

    def test_insert_nuevos(self, db: Session) -> None:
        registros = [
            OdepaCsvRecord("papa", "Lo Valledor", Decimal("800"), "kg", datetime.date(2026, 6, 20)),
            OdepaCsvRecord("papa", "Temuco", Decimal("820"), "kg", datetime.date(2026, 6, 20)),
        ]
        insertados, actualizados = upsert_prices(db, registros)

        assert insertados == 2
        assert actualizados == 0
        assert db.query(OdepaPrice).count() == 2

    def test_update_precio_existente(self, db: Session) -> None:
        """Re-sync con precio corregido actualiza la fila existente."""
        upsert_prices(db, [OdepaCsvRecord("papa", "Lo Valledor", Decimal("800"), "kg", datetime.date(2026, 6, 20))])
        insertados, actualizados = upsert_prices(
            db, [OdepaCsvRecord("papa", "Lo Valledor", Decimal("850"), "kg", datetime.date(2026, 6, 20))]
        )

        assert insertados == 0
        assert actualizados == 1
        assert db.query(OdepaPrice).count() == 1
        precio = db.query(OdepaPrice).one()
        assert precio.precio_kg == Decimal("850")

    def test_idempotente_re_ejecucion(self, db: Session) -> None:
        """Re-ejecutar el mismo batch no duplica filas."""
        registros = [OdepaCsvRecord("papa", "Lo Valledor", Decimal("800"), "kg", datetime.date(2026, 6, 20))]
        upsert_prices(db, registros)

        insertados, actualizados = upsert_prices(db, registros)

        assert insertados == 0
        assert actualizados == 1
        assert db.query(OdepaPrice).count() == 1

    def test_lista_vacia_devuelve_ceros(self, db: Session) -> None:
        insertados, actualizados = upsert_prices(db, [])
        assert (insertados, actualizados) == (0, 0)

    def test_duplicados_en_batch_db_vacia_cuenta_un_insert(self, db: Session) -> None:
        """Dos registros con misma tupla en un batch: 1 insert real, no 2.

        El conteo debe reflejar lo que ON CONFLICT colapsa, no el largo
        del batch. Gana la última aparición (precio 810).
        """
        registros = [
            OdepaCsvRecord("papa", "Lo Valledor", Decimal("800"), "kg", datetime.date(2026, 6, 20)),
            OdepaCsvRecord("papa", "Lo Valledor", Decimal("810"), "kg", datetime.date(2026, 6, 20)),
        ]
        insertados, actualizados = upsert_prices(db, registros)

        assert (insertados, actualizados) == (1, 0)
        assert db.query(OdepaPrice).count() == 1
        assert db.query(OdepaPrice).one().precio_kg == Decimal("810")

    def test_duplicados_en_batch_tupla_existente_cuenta_un_update(self, db: Session) -> None:
        """Batch con dup intra-batch sobre tupla ya en DB: 1 update real."""
        upsert_prices(db, [OdepaCsvRecord("papa", "Lo Valledor", Decimal("800"), "kg", datetime.date(2026, 6, 20))])

        registros = [
            OdepaCsvRecord("papa", "Lo Valledor", Decimal("850"), "kg", datetime.date(2026, 6, 20)),
            OdepaCsvRecord("papa", "Lo Valledor", Decimal("870"), "kg", datetime.date(2026, 6, 20)),
        ]
        insertados, actualizados = upsert_prices(db, registros)

        assert (insertados, actualizados) == (0, 1)
        assert db.query(OdepaPrice).count() == 1
        assert db.query(OdepaPrice).one().precio_kg == Decimal("870")


class TestDownloadCsv:
    """download_csv con httpx mockeado. Sin red real."""

    async def test_descarga_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, text="producto,mercado,precio,unidad,fecha\npapa,X,1,kg,2026-06-20\n"
            )

        fake_cls, real_client = _csv_fake_client(handler)
        monkeypatch.setattr("app.services.odepa_service.httpx.AsyncClient", fake_cls)
        try:
            contenido = await download_csv("https://fake.odepa.cl/csv")
        finally:
            await real_client.aclose()

        assert "papa" in contenido

    async def test_http_error_lanza_odepa_sync_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="")

        fake_cls, real_client = _csv_fake_client(handler)
        monkeypatch.setattr("app.services.odepa_service.httpx.AsyncClient", fake_cls)
        try:
            with pytest.raises(OdepaSyncError, match="503"):
                await download_csv("https://fake.odepa.cl/csv")
        finally:
            await real_client.aclose()

    async def test_request_error_lanza_odepa_sync_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("conexión rechazada")

        fake_cls, real_client = _csv_fake_client(handler)
        monkeypatch.setattr("app.services.odepa_service.httpx.AsyncClient", fake_cls)
        try:
            with pytest.raises(OdepaSyncError, match="Error de red"):
                await download_csv("https://fake.odepa.cl/csv")
        finally:
            await real_client.aclose()

    async def test_respuesta_vacia_lanza_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="")

        fake_cls, real_client = _csv_fake_client(handler)
        monkeypatch.setattr("app.services.odepa_service.httpx.AsyncClient", fake_cls)
        try:
            with pytest.raises(OdepaSyncError, match="vacío"):
                await download_csv("https://fake.odepa.cl/csv")
        finally:
            await real_client.aclose()


class TestSyncOdepa:
    """Orquestador sync_odepa con download_csv mockeado."""

    async def test_sync_end_to_end_con_session_inyectada(self, db: Session) -> None:
        with patch(
            "app.services.odepa_service.download_csv",
            new=AsyncMock(return_value=_CSV_FIJO),
        ):
            resultado = await sync_odepa(session=db)

        assert isinstance(resultado, SyncResult)
        # sync_odepa filtra por settings.odepa_productos_list (default ['papa']).
        # El CSV_FIJO tiene 4 papas + 1 tomate; el tomate queda fuera del filtro.
        assert resultado.insertados == 4
        assert resultado.actualizados == 0
        papas = db.query(OdepaPrice).filter_by(producto="papa").count()
        assert papas == 4
        tomates = db.query(OdepaPrice).filter_by(producto="tomate").count()
        assert tomates == 0

    async def test_sync_error_de_red_propaga(self, db: Session) -> None:
        with patch(
            "app.services.odepa_service.download_csv",
            new=AsyncMock(side_effect=OdepaSyncError("red caída")),
        ), pytest.raises(OdepaSyncError, match="red caída"):
            await sync_odepa(session=db)
