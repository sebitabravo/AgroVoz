"""Guardas estáticas para evitar regresiones de privacidad en logs."""

from __future__ import annotations

import ast
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[1] / "app"
_FORBIDDEN_LABELS = (
    "phone_hash=",
    "chat_id_hash=",
    "target_hash=",
    "contact_id_hash=",
    "query=",
    "texto=",
    "transcription=",
    "transcripcion=",
    "IP=",
)


def _logger_calls() -> list[tuple[Path, ast.Call]]:
    """Retorna llamadas ``logger.*`` encontradas en el código de aplicación."""
    calls: list[tuple[Path, ast.Call]] = []
    for path in _APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id == "logger"
            ):
                calls.append((path, node))
    return calls


def test_app_no_usa_logger_exception() -> None:
    """Los tracebacks pueden copiar consultas, rutas y parámetros SQL."""
    offenders = [
        f"{path.relative_to(_APP_DIR)}:{call.lineno}"
        for path, call in _logger_calls()
        if isinstance(call.func, ast.Attribute) and call.func.attr == "exception"
    ]
    assert offenders == []


def test_logs_no_declaran_campos_correlacionables() -> None:
    """Los mensajes de log no deben volver a incorporar sujeto o contenido libre."""
    offenders: list[str] = []
    for path, call in _logger_calls():
        if not call.args:
            continue
        message = call.args[0]
        if not isinstance(message, ast.Constant) or not isinstance(message.value, str):
            continue
        if any(label in message.value for label in _FORBIDDEN_LABELS):
            offenders.append(f"{path.relative_to(_APP_DIR)}:{call.lineno}")

        for keyword in call.keywords:
            if keyword.arg == "exc_info":
                offenders.append(f"{path.relative_to(_APP_DIR)}:{call.lineno}")

    assert offenders == []
