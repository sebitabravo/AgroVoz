"""Endpoints admin de preferencias de usuario (onboarding por voz, #86).

Permite al equipo registrar la comuna de un productor presencialmente
durante el piloto de Traiguén. El phone_hash ya está hasheado (HMAC-SHA256);
el equipo lo obtiene del dashboard de actividad.

Endpoints:
- PUT  /admin/users/{phone_hash}/comuna — upsert comuna para un phone_hash.
- GET  /admin/users/{phone_hash}        — obtiene prefs de un productor.

Todos requieren header X-Admin-Key.
"""

import json
import logging
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.admin.deps import require_admin_key
from app.core.config import settings
from app.core.database import get_db
from app.core.phone_hash import validate_phone_hash
from app.models.user_prefs import UserPrefs
from app.schemas.user_prefs import ComunaRequest, IdentityType, UserPrefsResponse
from app.services.consultation_history_service import (
    HistoryOperationError,
    delete_history,
)
from app.services.expense_service import (
    ExpenseOperationError,
    delete_expenses_for_subject,
)
from app.services.location_service import (
    LocationOperationError,
    clear_user_location,
)
from app.services.parcela_service import (
    ParcelaOperationError,
    delete_parcelas_for_subject,
)

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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="phone_hash debe ser 64 caracteres hexadecimales minúscula.",
        )


def _serializar_cultivos(cultivos: list[str] | None) -> str | None:
    """Serializa lista de cultivos a JSON string para SQLite TEXT.

    Args:
        cultivos: Lista de strings con cultivos de interés, o None.

    Returns:
        JSON string o None si la lista está vacía o es None.
    """
    if not cultivos:
        return None
    return json.dumps(cultivos, ensure_ascii=False)


def _apply_prefs_fields(
    prefs: UserPrefs,
    comuna: str,
    body: ComunaRequest,
    *,
    is_new: bool,
) -> None:
    """Aplica campos presentes sin filtrar datos sensibles a los logs.

    Punto único de asignación para el upsert y su retry por race condition.
    El cambio de consentimiento se loguea siempre (auditoría Ley 21.719).
    Cada campo opcional solo se modifica si viene explícito.
    """
    prefs.comuna = comuna
    if body.dataset_consent is not None:
        prefs.dataset_consent = body.dataset_consent
        logger.info(
            "dataset_consent actualizado — consent=%s",
            prefs.dataset_consent,
        )
    if body.history_consent is not None:
        prefs.history_consent = body.history_consent
        logger.info(
            "history_consent actualizado — consent=%s",
            prefs.history_consent,
        )
    if body.expense_consent is not None:
        prefs.expense_consent = body.expense_consent
        logger.info(
            "expense_consent actualizado — consent=%s",
            prefs.expense_consent,
        )
    if body.parcela_consent is not None:
        prefs.parcela_consent = body.parcela_consent
        logger.info(
            "parcela_consent actualizado — consent=%s",
            prefs.parcela_consent,
        )
    if body.location_consent is not None:
        prefs.location_consent = body.location_consent
        logger.info(
            "location_consent actualizado — consent=%s",
            prefs.location_consent,
        )
    if body.cultivos is not None:
        prefs.cultivos = _serializar_cultivos(body.cultivos)
        # No loguear el contenido de cultivos (dato personal); solo la cantidad.
        logger.info(
            "cultivos actualizados — count=%d",
            len(body.cultivos),
        )

    provided_fields = body.model_fields_set
    current_identity = "individual" if is_new else prefs.identity_type
    requested_identity = body.identity_type if "identity_type" in provided_fields else current_identity

    if requested_identity == "individual":
        if "group_label" in provided_fields and body.group_label is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="group_label solo se admite para prodesal_group.",
            )
        if is_new or "identity_type" in provided_fields:
            prefs.identity_type = "individual"
            prefs.group_label = None
    else:
        if "identity_type" in provided_fields:
            if body.group_label is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="prodesal_group exige group_label explícito.",
                )
            prefs.identity_type = "prodesal_group"
            prefs.group_label = body.group_label
        elif "group_label" in provided_fields:
            if body.group_label is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Una identidad prodesal_group no puede quedar sin group_label.",
                )
            prefs.group_label = body.group_label

    if "localidad" in provided_fields:
        prefs.localidad = body.localidad


def _delete_history_after_consent_revocation(
    phone_hash: str,
    history_consent: bool | None,
) -> None:
    """Borra historial después de confirmar una revocación explícita.

    El gate apagado y la omisión del campo no cambian el comportamiento
    existente. Un fallo no revierte el consentimiento: responde 503 para que
    el cliente reintente la misma solicitud hasta confirmar la limpieza.
    """
    if history_consent is not False or not settings.consultation_history_enabled:
        return

    try:
        delete_history(
            phone_hash,
            reason="consent_revoked",
            requested_via="admin_api",
        )
    except HistoryOperationError:
        logger.error("Consentimiento revocado; limpieza auditada de historial pendiente")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("Consentimiento revocado; limpieza de historial pendiente. Reintente la solicitud."),
        ) from None


def _delete_expenses_after_consent_revocation(
    phone_hash: str,
    expense_consent: bool | None,
) -> None:
    """Borra los gastos declarados después de una revocación explícita (#170).

    A diferencia del historial, la limpieza no depende del feature gate: si el
    gate se apagó después de haber registrado gastos, la revocación igual debe
    dejar la tabla sin datos del sujeto. Un fallo no revierte el consentimiento:
    responde 503 para que el cliente reintente la misma solicitud.
    """
    if expense_consent is not False:
        return

    try:
        delete_expenses_for_subject(phone_hash)
    except ExpenseOperationError:
        logger.error("Consentimiento revocado; limpieza de gastos pendiente")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("Consentimiento revocado; limpieza de gastos pendiente. Reintente la solicitud."),
        ) from None


def _delete_parcelas_after_consent_revocation(
    phone_hash: str,
    parcela_consent: bool | None,
) -> None:
    """Borra las parcelas registradas después de una revocación explícita (C5).

    A diferencia del historial, la limpieza no depende del feature gate: si el
    gate se apagó después de haber registrado parcelas, la revocación igual debe
    dejar la tabla sin datos del sujeto. Un fallo no revierte el consentimiento:
    responde 503 para que el cliente reintente la misma solicitud.
    """
    if parcela_consent is not False:
        return

    try:
        delete_parcelas_for_subject(phone_hash)
    except ParcelaOperationError:
        logger.error("Consentimiento revocado; limpieza de parcelas pendiente")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("Consentimiento revocado; limpieza de parcelas pendiente. Reintente la solicitud."),
        ) from None


def _delete_location_after_consent_revocation(
    phone_hash: str,
    location_consent: bool | None,
) -> None:
    """Limpia el pin GPS después de una revocación explícita."""
    if location_consent is not False:
        return

    try:
        clear_user_location(phone_hash)
    except LocationOperationError:
        logger.error("Consentimiento revocado; limpieza de ubicación pendiente")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("Consentimiento revocado; limpieza de ubicación pendiente. Reintente la solicitud."),
        ) from None


@router.put("/{phone_hash}/comuna", response_model=UserPrefsResponse)
def set_comuna(
    phone_hash: str = Path(
        ...,
        description="Hash HMAC-SHA256 del teléfono (64 chars hex minúscula).",
    ),
    body: ComunaRequest = ...,  # type: ignore[assignment]
    db: Session = Depends(get_db),  # noqa: B008
) -> UserPrefsResponse:
    """Registra o actualiza la comuna y cultivos de un productor (upsert).

    Si el phone_hash no existe en user_prefs, crea una fila nueva.
    Si ya existe, actualiza los campos. Idempotente: múltiples PUTs con
    los mismos valores no crean duplicados (unique constraint en phone_hash).

    Usado en el onboarding presencial del piloto: el equipo visita al
    productor, recibe su primer audio y registra desde el dashboard su
    comuna, cultivos e identidad operativa.
    """
    _validate_phone_hash_param(phone_hash)
    comuna = body.comuna.strip()
    if not comuna:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="comuna no puede estar vacía después de limpiar espacios.",
        )

    try:
        prefs = db.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
        if prefs is None:
            prefs = UserPrefs(phone_hash=phone_hash)
            db.add(prefs)
            logger.info("UserPrefs creada")
            is_new = True
        else:
            logger.info("UserPrefs actualizada")
            is_new = False
        _apply_prefs_fields(prefs, comuna, body, is_new=is_new)

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
        _apply_prefs_fields(prefs, comuna, body, is_new=False)
        db.commit()
        db.refresh(prefs)
    except SQLAlchemyError:
        db.rollback()
        # No incluir traceback: SQLAlchemy puede adjuntar parámetros sensibles.
        logger.error("Error de DB en set_comuna")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al guardar preferencias.",
        ) from None

    _delete_history_after_consent_revocation(
        phone_hash,
        body.history_consent,
    )
    _delete_expenses_after_consent_revocation(
        phone_hash,
        body.expense_consent,
    )
    _delete_parcelas_after_consent_revocation(
        phone_hash,
        body.parcela_consent,
    )
    _delete_location_after_consent_revocation(
        phone_hash,
        body.location_consent,
    )

    return UserPrefsResponse(
        phone_hash=prefs.phone_hash,
        comuna=prefs.comuna,
        dataset_consent=prefs.dataset_consent,
        history_consent=prefs.history_consent,
        expense_consent=prefs.expense_consent,
        parcela_consent=prefs.parcela_consent,
        location_consent=prefs.location_consent,
        identity_type=cast("IdentityType", prefs.identity_type),
        group_label=prefs.group_label,
        localidad=prefs.localidad,
        cultivos=cast("list[str] | None", prefs.cultivos),
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
        history_consent=prefs.history_consent,
        expense_consent=prefs.expense_consent,
        parcela_consent=prefs.parcela_consent,
        location_consent=prefs.location_consent,
        identity_type=cast("IdentityType", prefs.identity_type),
        group_label=prefs.group_label,
        localidad=prefs.localidad,
        cultivos=cast("list[str] | None", prefs.cultivos),
        created_at=prefs.created_at,
    )
