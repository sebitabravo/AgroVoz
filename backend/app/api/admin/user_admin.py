"""Endpoints admin de preferencias de usuario (onboarding por voz, #86).

Permite al equipo registrar la comuna de un productor presencialmente
durante el piloto de Traiguén. El phone_hash ya está hasheado (HMAC-SHA256);
el equipo lo obtiene del dashboard de actividad o del log del primer contacto.

Endpoints:
- PUT  /admin/users/{phone_hash}/comuna — upsert comuna para un phone_hash.
- GET  /admin/users/{phone_hash}        — obtiene prefs de un productor.

Todos requieren header X-Admin-Key.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.admin.deps import require_admin_key
from app.core.database import get_db
from app.core.phone_hash import validate_phone_hash
from app.models.user_prefs import UserPrefs
from app.schemas.user_prefs import ComunaRequest, UserPrefsResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/users",
    tags=["admin-users"],
    dependencies=[Depends(require_admin_key)],
)


def _validate_phone_hash_param(phone_hash: str) -> None:
    """Valida que phone_hash sea un hash HMAC-SHA256 válido.

    Args:
        phone_hash: Hash a validar.

    Raises:
        HTTPException: Si el formato es inválido (422).
    """
    if not validate_phone_hash(phone_hash):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="phone_hash debe ser 64 caracteres hexadecimales minúscula.",
        )


@router.put("/{phone_hash}/comuna", response_model=UserPrefsResponse)
def set_comuna(
    phone_hash: str = Path(
        ...,
        description="Hash HMAC-SHA256 del teléfono (64 chars hex minúscula).",
    ),
    body: ComunaRequest = ...,  # type: ignore[assignment]
    db: Session = Depends(get_db),  # noqa: B008
) -> UserPrefsResponse:
    """Registra o actualiza la comuna de un productor (upsert).

    Si el phone_hash no existe en user_prefs, crea una fila nueva.
    Si ya existe, actualiza la comuna. Idempotente: múltiples PUTs con
    la misma comuna no crean duplicados (unique constraint en phone_hash).

    Usado en el onboarding presencial del piloto: el equipo visita al
    productor, recibe su primer audio, anota el phone_hash del log y
    registra su comuna acá.
    """
    _validate_phone_hash_param(phone_hash)
    comuna = body.comuna.strip()
    if not comuna:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="comuna no puede estar vacía después de limpiar espacios.",
        )

    try:
        prefs = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
        if prefs is None:
            prefs = UserPrefs(phone_hash=phone_hash, comuna=comuna)
            db.add(prefs)
            logger.info(
                "UserPrefs creada — phone_hash=%s comuna=%s",
                phone_hash[:8],
                comuna,
            )
        else:
            prefs.comuna = comuna
            logger.info(
                "UserPrefs actualizada — phone_hash=%s comuna=%s",
                phone_hash[:8],
                comuna,
            )

        # Consentimiento para dataset de voz rural (#96). Opt-in explicito:
        # solo se actualiza si el body lo envia explicitamente.
        if body.dataset_consent is not None:
            prefs.dataset_consent = body.dataset_consent
            logger.info(
                "dataset_consent actualizado — phone_hash=%s consent=%s",
                phone_hash[:8],
                prefs.dataset_consent,
            )

        db.commit()
        db.refresh(prefs)
    except IntegrityError:
        db.rollback()
        # Race condition: otro request insertó el mismo phone_hash entre
        # nuestro SELECT y INSERT. Reintentar el SELECT para devolver la fila.
        prefs = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
        if prefs is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Conflicto al crear user_prefs.",
            ) from None
        prefs.comuna = comuna
        if body.dataset_consent is not None:
            prefs.dataset_consent = body.dataset_consent
        db.commit()
        db.refresh(prefs)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Error de DB en set_comuna — phone_hash=%s", phone_hash[:8])
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al guardar preferencias.",
        ) from None

    return UserPrefsResponse(
        phone_hash=prefs.phone_hash,
        comuna=prefs.comuna,
        dataset_consent=prefs.dataset_consent,
        created_at=prefs.created_at,
    )


@router.get("/{phone_hash}", response_model=UserPrefsResponse)
def get_user_prefs(
    phone_hash: str = Path(
        ...,
        description="Hash HMAC-SHA256 del teléfono (64 chars hex minúscula).",
    ),
    db: Session = Depends(get_db),  # noqa: B008
) -> UserPrefsResponse:
    """Obtiene las preferencias de un productor por phone_hash.

    Retorna 404 si el phone_hash no tiene prefs registradas.
    """
    _validate_phone_hash_param(phone_hash)
    prefs = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
    if prefs is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No hay preferencias registradas para este phone_hash.",
        )

    return UserPrefsResponse(
        phone_hash=prefs.phone_hash,
        comuna=prefs.comuna,
        dataset_consent=prefs.dataset_consent,
        created_at=prefs.created_at,
    )
