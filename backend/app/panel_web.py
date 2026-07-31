"""Router web del panel del agricultor: shell HTML y Service Worker (C3).

Separado de app/api/panel.py (la API JSON, montada bajo /api/v1) porque un
Service Worker solo puede controlar el path desde donde se sirve su script:
necesita scope /panel/, así que no puede vivir bajo /static/ ni /api/v1/.
Mismo motivo que app/admin/admin.py sirve su propio /admin/sw.js.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.config import settings

router = APIRouter(prefix="/panel", include_in_schema=False)

_PANEL_STATIC_DIR = Path(__file__).resolve().parent / "static" / "panel"
_SW_JS_PATH = _PANEL_STATIC_DIR / "sw.js"
_INDEX_HTML_PATH = _PANEL_STATIC_DIR / "index.html"


def _require_panel_enabled() -> None:
    """Bloquea el shell web si el panel está deshabilitado."""
    if not settings.farmer_panel_enabled:
        raise HTTPException(
            status_code=503,
            detail="El panel web todavía no está disponible.",
        )


@router.get("/sw.js")
async def panel_service_worker() -> FileResponse:
    """Service Worker del panel: scope /panel/ para cachear shell y resumen.

    no-cache: el navegador revalida el SW en cada visita, así los cambios de
    estrategia de cache llegan sin esperar expiración de TTL.
    """
    return FileResponse(
        _SW_JS_PATH,
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/{token}")
async def panel_shell(token: str) -> FileResponse:
    """Sirve el shell HTML del panel para cualquier token.

    El token no se valida acá: es el mismo shell para cualquier link, y la
    lectura real de datos ocurre en el navegador contra
    GET /api/v1/panel/{token}, que sí verifica firma y vigencia.
    """
    del token  # Solo forma parte de la URL; la validación real es en la API.
    _require_panel_enabled()
    return FileResponse(_INDEX_HTML_PATH, media_type="text/html")
