"""Router web de la console de agrónomos: shell HTML (C4).

Separado de app/api/agronomist.py (la API JSON, montada bajo /api/v1) por el
mismo motivo que app/panel_web.py: el shell vive en su propio path, aunque
acá no hace falta Service Worker — es una herramienta de escritorio para el
equipo/agrónomo, no un flujo offline-first como el panel del agricultor (C3).
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.config import settings

router = APIRouter(prefix="/agronomo", include_in_schema=False)

_AGRONOMO_STATIC_DIR = Path(__file__).resolve().parent / "static" / "agronomo"
_INDEX_HTML_PATH = _AGRONOMO_STATIC_DIR / "index.html"


def _require_agronomist_console_enabled() -> None:
    """Bloquea el shell web si la console está deshabilitada."""
    if not settings.agronomist_console_enabled:
        raise HTTPException(
            status_code=503,
            detail="La console de agrónomos todavía no está disponible.",
        )


@router.get("/{token}")
async def agronomist_shell(token: str) -> FileResponse:
    """Sirve el shell HTML de la console para cualquier token.

    El token no se valida acá: es el mismo shell para cualquier link, y la
    lectura real de datos ocurre en el navegador contra
    GET /api/v1/agronomo/{token}, que sí verifica firma y vigencia.
    """
    del token  # Solo forma parte de la URL; la validación real es en la API.
    _require_agronomist_console_enabled()
    return FileResponse(_INDEX_HTML_PATH, media_type="text/html")
