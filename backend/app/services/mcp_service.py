"""Operaciones seguras para la interfaz administrativa interna tipo MCP.

Las proyecciones de lectura son allowlist: los textos libres y el identificador
seudonimizado del agricultor no se seleccionan desde SQLite. La única escritura
expuesta reutiliza la sincronización ODEPA y se serializa con un lock local.
"""

import asyncio
import datetime
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from typing import cast

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.consultation import Consultation
from app.schemas.mcp import JsonValue
from app.services import delivery_service, metrics_service, odepa_service

logger = logging.getLogger(__name__)

_DEFAULT_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 50
_SAFE_DELIVERY_STATUSES = frozenset({"pending", "delivered", "failed"})
# Se deriva de delivery_service en vez de repetir la lista: mantener dos copias
# hacía que un código válido al escribir ("openwa_send_failed", "openwa_rejected",
# "openwa_request_error") se leyera como "delivery_error_unknown" desde MCP.
_SAFE_DELIVERY_ERROR_CODES = delivery_service.ALLOWED_DELIVERY_ERROR_CODES
_CONVERSATION_COLUMNS = (
    Consultation.id,
    Consultation.intent,
    Consultation.producto,
    Consultation.created_at,
    Consultation.delivered_at,
    Consultation.delivery_status,
    Consultation.delivery_error_code,
    Consultation.latency_ms,
    Consultation.whisper_ms,
    Consultation.llm_ms,
    Consultation.tts_ms,
    Consultation.requires_review,
)
_sync_odepa_lock = asyncio.Lock()

type McpResult = dict[str, JsonValue]


class McpServiceError(Exception):
    """Error seguro y serializable del servicio MCP."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class McpInputError(McpServiceError):
    """Argumentos de una tool fuera del contrato."""


class McpNotFoundError(McpServiceError):
    """Recurso MCP solicitado inexistente."""


class McpDatabaseError(McpServiceError):
    """Fallo de almacenamiento sin detalles internos."""


class McpConflictError(McpServiceError):
    """Operación administrativa incompatible con otra en curso."""


class McpUpstreamError(McpServiceError):
    """Fallo externo saneado sin contenido del proveedor."""


def _with_session[T](operation: Callable[[Session], T]) -> T:
    """Ejecuta una operación con sesión propia y cierre garantizado."""
    try:
        db = SessionLocal()
    except SQLAlchemyError:
        logger.error("MCP lectura falló al abrir SQLite")
        raise McpDatabaseError(
            "MCP_DATABASE_ERROR",
            "No fue posible consultar los datos administrativos.",
        ) from None

    try:
        return operation(db)
    except SQLAlchemyError:
        # No registrar excepción, paths ni SQL: la interfaz es administrativa
        # pero sus logs siguen sujetos a minimización de datos.
        logger.error("MCP lectura falló durante una consulta SQLite")
        raise McpDatabaseError(
            "MCP_DATABASE_ERROR",
            "No fue posible consultar los datos administrativos.",
        ) from None
    finally:
        db.close()


def _validate_arguments(
    arguments: Mapping[str, object],
    allowed: frozenset[str],
) -> None:
    """Rechaza argumentos desconocidos sin reflejarlos en el error."""
    if not set(arguments).issubset(allowed):
        raise McpInputError(
            "MCP_INVALID_ARGUMENTS",
            "Los argumentos no cumplen el contrato de la tool.",
        )


def _parse_limit(arguments: Mapping[str, object]) -> int:
    """Obtiene un límite entero entre 1 y 50."""
    value = arguments.get("limit", _DEFAULT_PAGE_SIZE)
    if type(value) is not int or not 1 <= value <= _MAX_PAGE_SIZE:
        raise McpInputError(
            "MCP_INVALID_LIMIT",
            "limit debe ser un entero entre 1 y 50.",
        )
    return value


def _parse_cursor(arguments: Mapping[str, object]) -> int | None:
    """Obtiene un cursor de ID positivo y exclusivo."""
    value = arguments.get("cursor")
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise McpInputError(
            "MCP_INVALID_CURSOR",
            "cursor debe ser un ID entero positivo.",
        )
    return value


def _parse_required_id(arguments: Mapping[str, object]) -> int:
    """Obtiene el ID obligatorio de una conversación."""
    if "id" not in arguments:
        raise McpInputError(
            "MCP_ID_REQUIRED",
            "La tool requiere un ID de conversación.",
        )
    value = arguments["id"]
    if type(value) is not int or value < 1:
        raise McpInputError(
            "MCP_INVALID_ID",
            "id debe ser un entero positivo.",
        )
    return value


def _timestamp(value: object) -> str | None:
    """Serializa un timestamp conocido sin aceptar objetos arbitrarios."""
    if isinstance(value, datetime.datetime):
        return value.isoformat(timespec="seconds")
    return None


def _safe_delivery_status(value: object) -> str:
    """Cierra el dominio público del estado de entrega."""
    if isinstance(value, str) and value in _SAFE_DELIVERY_STATUSES:
        return value
    return "pending"


def _safe_delivery_error_code(value: object) -> str | None:
    """Expone solo códigos conocidos; nunca texto libre de excepciones."""
    if value is None:
        return None
    if isinstance(value, str) and value in _SAFE_DELIVERY_ERROR_CODES:
        return value
    return "delivery_error_unknown"


def _project_conversation(row: Sequence[object]) -> McpResult:
    """Construye la proyección pública desde columnas explícitas."""
    (
        conversation_id,
        intent,
        producto,
        created_at,
        delivered_at,
        delivery_status,
        delivery_error_code,
        latency_ms,
        whisper_ms,
        llm_ms,
        tts_ms,
        requires_review,
    ) = row
    return {
        "id": int(cast(int, conversation_id)),
        "intent": str(intent),
        "producto": str(producto) if producto is not None else None,
        "created_at": _timestamp(created_at),
        "delivered_at": _timestamp(delivered_at),
        "delivery_status": _safe_delivery_status(delivery_status),
        "delivery_error_code": _safe_delivery_error_code(delivery_error_code),
        "latency_ms": int(cast(int, latency_ms)),
        "whisper_ms": int(cast(int, whisper_ms)),
        "llm_ms": int(cast(int, llm_ms)),
        "tts_ms": int(cast(int, tts_ms)),
        "requires_review": bool(requires_review),
    }


def _page_result(rows: Sequence[Sequence[object]], limit: int) -> McpResult:
    """Serializa una página y calcula el cursor siguiente por ID."""
    has_more = len(rows) > limit
    visible_rows = rows[:limit]
    items = [_project_conversation(row) for row in visible_rows]
    next_cursor: int | None = None
    if has_more and visible_rows:
        next_cursor = int(cast(int, visible_rows[-1][0]))
    return {
        "items": cast(JsonValue, items),
        "next_cursor": next_cursor,
    }


def get_metrics(arguments: Mapping[str, object]) -> McpResult:
    """Retorna KPIs reutilizando el agregador oficial del dashboard."""
    _validate_arguments(arguments, frozenset())

    def operation(db: Session) -> McpResult:
        metrics = metrics_service.get_dashboard_kpis(db)
        return cast(McpResult, asdict(metrics))

    return _with_session(operation)


def list_conversations(arguments: Mapping[str, object]) -> McpResult:
    """Lista consultas recientes con paginación por ID descendente."""
    _validate_arguments(arguments, frozenset({"limit", "cursor"}))
    limit = _parse_limit(arguments)
    cursor = _parse_cursor(arguments)

    def operation(db: Session) -> McpResult:
        stmt = (
            select(*_CONVERSATION_COLUMNS)
            .where(Consultation.is_test.is_(False))
            .order_by(Consultation.id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            stmt = stmt.where(Consultation.id < cursor)
        rows = [tuple(row) for row in db.execute(stmt).all()]
        return _page_result(rows, limit)

    return _with_session(operation)


def get_conversation(arguments: Mapping[str, object]) -> McpResult:
    """Obtiene una consulta por ID sin cargar sus textos ni phone_hash."""
    _validate_arguments(arguments, frozenset({"id"}))
    conversation_id = _parse_required_id(arguments)

    def operation(db: Session) -> McpResult:
        stmt = select(*_CONVERSATION_COLUMNS).where(
            Consultation.id == conversation_id,
            Consultation.is_test.is_(False),
        )
        row = db.execute(stmt).first()
        if row is None:
            raise McpNotFoundError(
                "MCP_CONVERSATION_NOT_FOUND",
                "La conversación solicitada no existe.",
            )
        return _project_conversation(tuple(row))

    return _with_session(operation)


def get_error_log(arguments: Mapping[str, object]) -> McpResult:
    """Lista fallos recientes de entrega como registros estructurados."""
    _validate_arguments(arguments, frozenset({"limit", "cursor"}))
    limit = _parse_limit(arguments)
    cursor = _parse_cursor(arguments)

    def operation(db: Session) -> McpResult:
        stmt = (
            select(*_CONVERSATION_COLUMNS)
            .where(
                Consultation.is_test.is_(False),
                Consultation.delivery_status == "failed",
            )
            .order_by(Consultation.id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            stmt = stmt.where(Consultation.id < cursor)
        rows = [tuple(row) for row in db.execute(stmt).all()]
        return _page_result(rows, limit)

    return _with_session(operation)


def execute_read_tool(
    tool_name: str,
    arguments: Mapping[str, object],
) -> McpResult:
    """Despacha una única tool de lectura a su operación correspondiente."""
    handlers: dict[str, Callable[[Mapping[str, object]], McpResult]] = {
        "get_metrics": get_metrics,
        "list_conversations": list_conversations,
        "get_conversation": get_conversation,
        "get_error_log": get_error_log,
    }
    handler = handlers.get(tool_name)
    if handler is None:
        raise McpInputError(
            "MCP_READ_TOOL_UNKNOWN",
            "La tool de lectura no está disponible.",
        )
    return handler(arguments)


async def _sync_odepa(arguments: Mapping[str, object]) -> McpResult:
    """Ejecuta la sincronización ODEPA con exclusión mutua por proceso."""
    _validate_arguments(arguments, frozenset())
    if _sync_odepa_lock.locked():
        raise McpConflictError(
            "MCP_ODEPA_SYNC_IN_PROGRESS",
            "Ya existe una sincronización ODEPA en curso.",
        )

    async with _sync_odepa_lock:
        try:
            sync_result = await odepa_service.sync_odepa()
        except (odepa_service.OdepaSyncError, SQLAlchemyError):
            logger.error("MCP sync ODEPA finalizó con error saneado")
            raise McpUpstreamError(
                "MCP_ODEPA_SYNC_ERROR",
                "No fue posible completar la sincronización ODEPA.",
            ) from None

    return {
        "status": "completed",
        "inserted": sync_result.insertados,
        "updated": sync_result.actualizados,
        "total": sync_result.total,
    }


async def execute_tool(
    tool_name: str,
    arguments: Mapping[str, object],
) -> McpResult:
    """Despacha una tool MCP sin bloquear el event loop."""
    if tool_name == "sync_odepa":
        return await _sync_odepa(arguments)
    return await asyncio.to_thread(execute_read_tool, tool_name, arguments)
