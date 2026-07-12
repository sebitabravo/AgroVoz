"""Tests del endpoint de export CSV de precios ODEPA (issue #90).

Cubre:
- Auth: sin cookie de sesión -> 303 redirect a login (patrón dashboard).
- Sin filtros: retorna todos los registros.
- Filtros: por producto, por mercado, por rango de fechas (desde/hasta).
- Formato: Content-Type text/csv, Content-Disposition con filename,
  BOM UTF-8, headers correctos, precio_kg como número crudo.
- Edge cases: DB vacía (solo headers), fecha inválida -> 400.

El endpoint vive en /admin/prices/export (cookie auth vía middleware),
no en /api/v1/admin/ (header X-Admin-Key): un <a> del dashboard no puede
mandar headers custom, y la cookie de sesión tiene path="/admin".
"""

import csv
import datetime
import io
from collections.abc import Generator
from decimal import Decimal
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.admin.auth import COOKIE_NAME, create_session_cookie
from app.models.odepa_price import OdepaPrice

# BOM UTF-8 como bytes: Excel en español lo necesita para detectar encoding.
_BOM_UTF8 = b"\xef\xbb\xbf"

# Headers del CSV en orden.
_CSV_HEADERS = ["fecha", "producto", "mercado", "precio_kg", "unidad"]


def _autenticar(client: AsyncClient) -> None:
    """Setea cookie de sesión admin válida en el client (patrón dashboard)."""
    client.cookies.set(COOKIE_NAME, create_session_cookie())


def _session_test_db(tmp_path: Path) -> Generator[Session, None, None]:
    """Engine SQLite temporal al mismo archivo que el fixture `client`.

    Ambos apuntan a tmp_path/test_agrovoz.db: el test inserta y commitea,
    el client lee via dependency_overrides de get_db. SQLite file-based
    ve los commits entre engines distintos.
    """
    db_path = tmp_path / "test_agrovoz.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _precio(
    db: Session,
    producto: str = "papa",
    mercado: str = "Lo Valledor",
    precio_kg: str = "1200",
    unidad: str = "kg",
    fecha: datetime.date | None = None,
) -> OdepaPrice:
    """Inserta un registro OdepaPrice y commitea."""
    reg = OdepaPrice(
        producto=producto,
        mercado=mercado,
        precio_kg=Decimal(precio_kg),
        unidad=unidad,
        fecha=fecha or datetime.date(2026, 7, 1),
        fuente="test",
    )
    db.add(reg)
    db.commit()
    return reg


def _parse_csv_rows(content: bytes) -> list[list[str]]:
    """Decodifica el CSV (utf-8-sig quita el BOM) y devuelve las filas."""
    text = content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


# ── Auth ───────────────────────────────────────────────────────────


class TestCsvExportAuth:
    async def test_sin_cookie_redirige_a_login(self, client: AsyncClient) -> None:
        """Sin cookie de sesión, el middleware redirige (303) al login.

        El endpoint usa cookie auth (patrón dashboard), no header
        X-Admin-Key. 303 redirect (no 401) es consistente con todas
        las rutas /admin/*: el navegador debe ir al login, no ver un JSON.
        """
        resp = await client.get("/admin/prices/export", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/admin/login"

    async def test_con_cookie_valida_retorna_200(self, client: AsyncClient) -> None:
        _autenticar(client)
        resp = await client.get("/admin/prices/export", follow_redirects=False)
        assert resp.status_code == 200


# ── Contenido y filtros ────────────────────────────────────────────


class TestCsvExportFiltros:
    async def test_sin_filtros_retorna_todos(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _precio(db, producto="papa", mercado="Lo Valledor", fecha=datetime.date(2026, 7, 1))
            _precio(db, producto="tomate", mercado="Vega Central", fecha=datetime.date(2026, 7, 2))
            _precio(db, producto="papa", mercado="Vega Central", fecha=datetime.date(2026, 7, 3))

        _autenticar(client)
        resp = await client.get("/admin/prices/export", follow_redirects=False)
        assert resp.status_code == 200
        rows = _parse_csv_rows(resp.content)
        # rows[0] = headers, rows[1:] = datos.
        assert rows[0] == _CSV_HEADERS
        assert len(rows) == 4  # 1 header + 3 datos

    async def test_filtro_por_producto(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _precio(db, producto="papa", mercado="Lo Valledor", fecha=datetime.date(2026, 7, 1))
            _precio(db, producto="tomate", mercado="Vega Central", fecha=datetime.date(2026, 7, 2))

        _autenticar(client)
        resp = await client.get(
            "/admin/prices/export?producto=papa", follow_redirects=False
        )
        assert resp.status_code == 200
        rows = _parse_csv_rows(resp.content)
        datos = rows[1:]
        assert len(datos) == 1
        assert datos[0][1] == "papa"  # columna producto

    async def test_filtro_por_mercado(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _precio(db, producto="papa", mercado="Lo Valledor", fecha=datetime.date(2026, 7, 1))
            _precio(db, producto="papa", mercado="Vega Central", fecha=datetime.date(2026, 7, 2))

        _autenticar(client)
        resp = await client.get(
            "/admin/prices/export?mercado=Lo Valledor", follow_redirects=False
        )
        assert resp.status_code == 200
        rows = _parse_csv_rows(resp.content)
        datos = rows[1:]
        assert len(datos) == 1
        assert datos[0][2] == "Lo Valledor"  # columna mercado

    async def test_filtro_rango_fechas(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _precio(db, producto="papa", fecha=datetime.date(2026, 7, 1))
            _precio(db, producto="papa", fecha=datetime.date(2026, 7, 5))
            _precio(db, producto="papa", fecha=datetime.date(2026, 7, 10))

        _autenticar(client)
        resp = await client.get(
            "/admin/prices/export?desde=2026-07-03&hasta=2026-07-07",
            follow_redirects=False,
        )
        assert resp.status_code == 200
        rows = _parse_csv_rows(resp.content)
        datos = rows[1:]
        assert len(datos) == 1
        assert datos[0][0] == "2026-07-05"  # columna fecha

    async def test_filtro_solo_desde(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _precio(db, producto="papa", fecha=datetime.date(2026, 7, 1))
            _precio(db, producto="papa", fecha=datetime.date(2026, 7, 10))

        _autenticar(client)
        resp = await client.get(
            "/admin/prices/export?desde=2026-07-05", follow_redirects=False
        )
        assert resp.status_code == 200
        rows = _parse_csv_rows(resp.content)
        datos = rows[1:]
        assert len(datos) == 1
        assert datos[0][0] == "2026-07-10"


# ── Formato del CSV ─────────────────────────────────────────────────


class TestCsvExportFormato:
    async def test_content_type_es_text_csv(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _precio(db)

        _autenticar(client)
        resp = await client.get("/admin/prices/export", follow_redirects=False)
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]

    async def test_content_disposition_tiene_filename(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _precio(db)

        _autenticar(client)
        resp = await client.get("/admin/prices/export", follow_redirects=False)
        cd = resp.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert 'filename="odepa_prices_' in cd
        assert cd.endswith('.csv"')

    async def test_csv_tiene_bom_utf8(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _precio(db)

        _autenticar(client)
        resp = await client.get("/admin/prices/export", follow_redirects=False)
        # Los primeros 3 bytes deben ser el BOM UTF-8 (EF BB BF).
        assert resp.content[:3] == _BOM_UTF8

    async def test_headers_correctos(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _precio(db)

        _autenticar(client)
        resp = await client.get("/admin/prices/export", follow_redirects=False)
        rows = _parse_csv_rows(resp.content)
        assert rows[0] == _CSV_HEADERS

    async def test_precio_kg_es_numero_crudo(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """precio_kg sin formato chileno: '1200.50', no '1.200,50'."""
        with next(_session_test_db(tmp_path)) as db:
            _precio(db, producto="papa", precio_kg="1200.50", fecha=datetime.date(2026, 7, 1))

        _autenticar(client)
        resp = await client.get(
            "/admin/prices/export?producto=papa", follow_redirects=False
        )
        rows = _parse_csv_rows(resp.content)
        datos = rows[1:]
        assert datos[0][3] == "1200.50"  # columna precio_kg

    async def test_fila_contiene_valores_correctos(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _precio(
                db,
                producto="papa",
                mercado="Lo Valledor",
                precio_kg="950",
                unidad="kg",
                fecha=datetime.date(2026, 7, 11),
            )

        _autenticar(client)
        resp = await client.get("/admin/prices/export", follow_redirects=False)
        rows = _parse_csv_rows(resp.content)
        datos = rows[1]
        assert datos[0] == "2026-07-11"
        assert datos[1] == "papa"
        assert datos[2] == "Lo Valledor"
        # Numeric(10,2) siempre conserva 2 decimales: 950 -> "950.00".
        assert datos[3] == "950.00"
        assert datos[4] == "kg"


# ── Edge cases ─────────────────────────────────────────────────────


class TestCsvExportEdgeCases:
    async def test_db_vacio_retorna_solo_headers(self, client: AsyncClient) -> None:
        # El fixture `client` ya crea la DB con tablas (sin datos).
        _autenticar(client)
        resp = await client.get("/admin/prices/export", follow_redirects=False)
        assert resp.status_code == 200
        rows = _parse_csv_rows(resp.content)
        assert len(rows) == 1  # solo headers, cero datos
        assert rows[0] == _CSV_HEADERS

    async def test_fecha_invalida_retorna_400(self, client: AsyncClient) -> None:
        _autenticar(client)
        resp = await client.get(
            "/admin/prices/export?desde=no-es-fecha", follow_redirects=False
        )
        assert resp.status_code == 400

    async def test_hasta_invalida_retorna_400(self, client: AsyncClient) -> None:
        _autenticar(client)
        resp = await client.get(
            "/admin/prices/export?hasta=2026/07/01", follow_redirects=False
        )
        assert resp.status_code == 400

    async def test_producto_inexistente_retorna_csv_vacio(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with next(_session_test_db(tmp_path)) as db:
            _precio(db, producto="papa")

        _autenticar(client)
        resp = await client.get(
            "/admin/prices/export?producto=inexistente", follow_redirects=False
        )
        assert resp.status_code == 200
        rows = _parse_csv_rows(resp.content)
        assert len(rows) == 1  # solo headers


# ── Seguridad (formula injection) ───────────────────────────────────


class TestCsvExportSecurity:
    async def test_formula_injection_sanitized(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """CSV formula injection: producto que empieza con '=SUMA(...)' es escapado.

        Si un producto/mercado empieza con '=', '+', '-', '@' o tab, Excel lo
        interpreta como fórmula. El export debe prefijarlo con apóstrofo (')
        para obligar tratamiento como texto.

        Regresión para: issue #90, PR #108 — sanitización de CSV values.
        """
        with next(_session_test_db(tmp_path)) as db:
            # Inserta un producto "malicioso" con fórmula
            _precio(db, producto="=SUMA(A1:A10)", mercado="Lo Valledor")

        _autenticar(client)
        resp = await client.get("/admin/prices/export", follow_redirects=False)
        assert resp.status_code == 200
        rows = _parse_csv_rows(resp.content)
        datos = rows[1]
        # El valor del producto debe estar escapado: "'=SUMA(A1:A10)"
        assert datos[1] == "'=SUMA(A1:A10)"

    async def test_formula_injection_mercado_sanitized(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """Mercado que empieza con '+' es escapado."""
        with next(_session_test_db(tmp_path)) as db:
            _precio(db, producto="papa", mercado="+1234567")

        _autenticar(client)
        resp = await client.get("/admin/prices/export", follow_redirects=False)
        assert resp.status_code == 200
        rows = _parse_csv_rows(resp.content)
        datos = rows[1]
        assert datos[2] == "'+1234567"

    async def test_formula_injection_minus_sign_sanitized(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """Mercado que empieza con '-' es escapado."""
        with next(_session_test_db(tmp_path)) as db:
            _precio(db, producto="papa", mercado="-1000")

        _autenticar(client)
        resp = await client.get("/admin/prices/export", follow_redirects=False)
        assert resp.status_code == 200
        rows = _parse_csv_rows(resp.content)
        datos = rows[1]
        assert datos[2] == "'-1000"

    async def test_formula_injection_at_sign_sanitized(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """Producto que empieza con '@' es escapado."""
        with next(_session_test_db(tmp_path)) as db:
            _precio(db, producto="@malicious", mercado="Lo Valledor")

        _autenticar(client)
        resp = await client.get("/admin/prices/export", follow_redirects=False)
        assert resp.status_code == 200
        rows = _parse_csv_rows(resp.content)
        datos = rows[1]
        assert datos[1] == "'@malicious"

    async def test_no_sanitize_normal_values(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        """Valores que NO empiezan con caracteres especiales no son modificados."""
        with next(_session_test_db(tmp_path)) as db:
            _precio(db, producto="papa", mercado="Lo Valledor")

        _autenticar(client)
        resp = await client.get("/admin/prices/export", follow_redirects=False)
        assert resp.status_code == 200
        rows = _parse_csv_rows(resp.content)
        datos = rows[1]
        # Sin sanitización para valores normales
        assert datos[1] == "papa"
        assert datos[2] == "Lo Valledor"
