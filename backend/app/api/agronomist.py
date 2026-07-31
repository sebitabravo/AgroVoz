"""Router de la console de agrónomos PRODESAL: link firmado, sin login (C4).

Endpoint de solo lectura para el resumen de un grupo. La identidad del grupo
se resuelve desde el token en la URL, igual que el panel del agricultor (C3):
sin sesión, sin cuenta.
"""

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.services.agronomist_console_service import get_group_summary, verify_agronomist_token

router = APIRouter(prefix="/agronomo", tags=["agronomo"])


def _require_agronomist_console_enabled() -> None:
    """Bloquea el endpoint si la console está deshabilitada."""
    if not settings.agronomist_console_enabled:
        raise HTTPException(
            status_code=503,
            detail="La console de agrónomos todavía no está disponible.",
        )


_console_enabled_dep = Depends(_require_agronomist_console_enabled)


@router.get("/{token}")
def get_group(
    token: str = Path(..., description="Token firmado entregado por el equipo."),
    _enabled: None = _console_enabled_dep,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, object]:
    """Devuelve el resumen del grupo si el token es válido y no venció."""
    group_label = verify_agronomist_token(token)
    if group_label is None:
        raise HTTPException(
            status_code=401,
            detail="Link inválido o vencido. Pide uno nuevo al equipo.",
        )

    resumenes = get_group_summary(db, group_label)
    if resumenes is None:
        raise HTTPException(
            status_code=404,
            detail="No hay contactos registrados para este grupo.",
        )

    return {
        "group_label": group_label,
        "productores": [
            {
                "comuna": resumen.comuna,
                "localidad": resumen.localidad,
                "cultivos": resumen.cultivos,
                "parcelas": resumen.parcelas,
            }
            for resumen in resumenes
        ],
    }
