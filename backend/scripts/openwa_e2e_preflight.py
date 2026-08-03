#!/usr/bin/env python3
"""Preflight seguro para la validación E2E de Open-WA.

El script comprueba únicamente que el backend y una sesión autenticada de
Open-WA estén disponibles. No envía mensajes ni guarda credenciales: los
casos de conversación se ejecutan manualmente con el checklist de
``docs/validacion-operativa.md`` para evitar confundir un smoke técnico con
una prueba real de WhatsApp.

Uso:
    cd backend
    OPENWA_API_KEY='...' uv run python scripts/openwa_e2e_preflight.py

Para guardar un resumen saneado fuera de Git:
    uv run python scripts/openwa_e2e_preflight.py \
        --evidence data/openwa_e2e_preflight.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal, cast

import httpx

DEFAULT_BACKEND_URL: Final[str] = "http://localhost:8000"
DEFAULT_OPENWA_URL: Final[str] = "http://localhost:2785"
DEFAULT_TIMEOUT_SECONDS: Final[float] = 5.0
READY_SESSION_STATUSES: Final[frozenset[str]] = frozenset({"ready", "active"})

CheckStatus = Literal["passed", "failed", "blocked"]


class PreflightConfigurationError(ValueError):
    """Indica que faltan datos locales para iniciar el preflight."""


@dataclass(frozen=True)
class PreflightConfig:
    """Configuración no sensible del preflight.

    La API key vive únicamente en memoria durante la request. Nunca se copia
    a esta estructura de salida ni se serializa al reporte.
    """

    backend_url: str
    openwa_url: str
    api_key: str
    timeout_seconds: float


@dataclass(frozen=True)
class CheckResult:
    """Resultado mínimo y saneado de una comprobación."""

    check_id: str
    status: CheckStatus
    elapsed_ms: float | None = None
    error_code: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class PreflightReport:
    """Reporte serializable sin URLs, teléfonos, mensajes ni secretos."""

    status: CheckStatus
    generated_at: str
    checks: tuple[CheckResult, ...]


def _normalise_url(value: str) -> str:
    """Quita barras finales para construir rutas HTTP de forma consistente."""
    return value.strip().rstrip("/")


def load_config(environ: Mapping[str, str] | None = None) -> PreflightConfig:
    """Carga variables de entorno y bloquea una ejecución incompleta."""
    values = os.environ if environ is None else environ
    api_key = values.get("OPENWA_API_KEY", "").strip()
    if not api_key:
        raise PreflightConfigurationError(
            "OPENWA_API_KEY no está configurada; no se puede validar una sesión real."
        )

    timeout_raw = values.get("OPENWA_E2E_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS)).strip()
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError as exc:
        raise PreflightConfigurationError("OPENWA_E2E_TIMEOUT debe ser un número positivo.") from exc
    if timeout_seconds <= 0:
        raise PreflightConfigurationError("OPENWA_E2E_TIMEOUT debe ser un número positivo.")

    return PreflightConfig(
        backend_url=_normalise_url(values.get("AGROVOZ_E2E_BASE_URL", DEFAULT_BACKEND_URL)),
        openwa_url=_normalise_url(values.get("OPENWA_API_URL", DEFAULT_OPENWA_URL)),
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )


def _elapsed_ms(started_at: float) -> float:
    """Convierte un contador monotónico a milisegundos redondeados."""
    return round((time.perf_counter() - started_at) * 1000, 1)


def _http_error_code(error: httpx.HTTPError) -> str:
    """Clasifica errores de red sin copiar su mensaje potencialmente sensible."""
    if isinstance(error, httpx.TimeoutException):
        return "timeout"
    if isinstance(error, httpx.ConnectError):
        return "connection_error"
    return "http_error"


def _check_backend(client: httpx.Client, config: PreflightConfig) -> CheckResult:
    """Comprueba liveness del backend sin exponer su body en el reporte."""
    started_at = time.perf_counter()
    try:
        response = client.get(f"{config.backend_url}/api/v1/health?probe=liveness")
    except httpx.HTTPError as error:
        return CheckResult(
            check_id="backend_liveness",
            status="failed",
            elapsed_ms=_elapsed_ms(started_at),
            error_code=_http_error_code(error),
        )

    if response.status_code != 200:
        return CheckResult(
            check_id="backend_liveness",
            status="failed",
            elapsed_ms=_elapsed_ms(started_at),
            error_code=f"http_{response.status_code}",
        )

    try:
        payload = cast(object, response.json())
    except (TypeError, ValueError):
        return CheckResult(
            check_id="backend_liveness",
            status="failed",
            elapsed_ms=_elapsed_ms(started_at),
            error_code="invalid_json",
        )
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return CheckResult(
            check_id="backend_liveness",
            status="failed",
            elapsed_ms=_elapsed_ms(started_at),
            error_code="health_not_ok",
        )
    return CheckResult(
        check_id="backend_liveness",
        status="passed",
        elapsed_ms=_elapsed_ms(started_at),
        detail="status_ok",
    )


def count_ready_sessions(payload: object) -> int:
    """Cuenta sesiones listas sin retener sus IDs ni otros campos."""
    if not isinstance(payload, list):
        return -1
    ready_count = 0
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        if isinstance(status, str) and status.lower() in READY_SESSION_STATUSES:
            ready_count += 1
    return ready_count


def _check_openwa(client: httpx.Client, config: PreflightConfig) -> CheckResult:
    """Comprueba API key y sesión lista de Open-WA."""
    started_at = time.perf_counter()
    try:
        response = client.get(
            f"{config.openwa_url}/api/sessions",
            headers={"X-API-Key": config.api_key},
        )
    except httpx.HTTPError as error:
        return CheckResult(
            check_id="openwa_session",
            status="blocked",
            elapsed_ms=_elapsed_ms(started_at),
            error_code=_http_error_code(error),
        )

    if response.status_code in (401, 403):
        return CheckResult(
            check_id="openwa_session",
            status="failed",
            elapsed_ms=_elapsed_ms(started_at),
            error_code="api_key_rejected",
        )
    if response.status_code < 200 or response.status_code >= 300:
        return CheckResult(
            check_id="openwa_session",
            status="blocked",
            elapsed_ms=_elapsed_ms(started_at),
            error_code=f"http_{response.status_code}",
        )

    try:
        payload = cast(object, response.json())
    except (TypeError, ValueError):
        return CheckResult(
            check_id="openwa_session",
            status="failed",
            elapsed_ms=_elapsed_ms(started_at),
            error_code="invalid_json",
        )

    ready_count = count_ready_sessions(payload)
    if ready_count < 0:
        return CheckResult(
            check_id="openwa_session",
            status="failed",
            elapsed_ms=_elapsed_ms(started_at),
            error_code="invalid_sessions_payload",
        )
    if ready_count == 0:
        return CheckResult(
            check_id="openwa_session",
            status="blocked",
            elapsed_ms=_elapsed_ms(started_at),
            error_code="session_not_ready",
            detail="escanea_el_qr_y_reintenta",
        )
    return CheckResult(
        check_id="openwa_session",
        status="passed",
        elapsed_ms=_elapsed_ms(started_at),
        detail=f"ready_sessions={ready_count}",
    )


def _overall_status(checks: Sequence[CheckResult]) -> CheckStatus:
    """Consolida checks sin convertir un bloqueo de infraestructura en éxito."""
    if any(check.status == "failed" for check in checks):
        return "failed"
    if any(check.status == "blocked" for check in checks):
        return "blocked"
    return "passed"


def run_preflight(config: PreflightConfig) -> PreflightReport:
    """Ejecuta las comprobaciones de backend y Open-WA."""
    with httpx.Client(timeout=config.timeout_seconds) as client:
        checks = (
            _check_backend(client, config),
            _check_openwa(client, config),
        )
    return PreflightReport(
        status=_overall_status(checks),
        generated_at=datetime.now(UTC).isoformat(),
        checks=checks,
    )


def blocked_report(error_code: str) -> PreflightReport:
    """Construye un reporte bloqueado para una configuración ausente."""
    return PreflightReport(
        status="blocked",
        generated_at=datetime.now(UTC).isoformat(),
        checks=(
            CheckResult(
                check_id="configuration",
                status="blocked",
                error_code=error_code,
            ),
        ),
    )


def report_as_dict(report: PreflightReport) -> dict[str, object]:
    """Serializa solo campos saneados y estables para adjuntar evidencia."""
    return {
        "status": report.status,
        "generated_at": report.generated_at,
        "checks": [
            {
                "check_id": check.check_id,
                "status": check.status,
                "elapsed_ms": check.elapsed_ms,
                "error_code": check.error_code,
                "detail": check.detail,
            }
            for check in report.checks
        ],
        "note": "El preflight no ejecuta conversaciones ni certifica el gate E2E.",
    }


def _write_evidence(path: Path, report: PreflightReport) -> None:
    """Escribe JSON saneado en la ruta elegida por el operador."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report_as_dict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parsea opciones sin aceptar secretos por línea de comandos."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence",
        type=Path,
        help="Ruta opcional para guardar un reporte JSON saneado (backend/data recomendado).",
    )
    return parser.parse_args(argv)


def _print_report(report: PreflightReport) -> None:
    """Muestra el resumen JSON sin incluir configuración sensible."""
    print(json.dumps(report_as_dict(report), ensure_ascii=False, indent=2))


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada del preflight.

    Código 0 significa que la infraestructura está lista; 1 indica un error
    verificable y 2 que el gate quedó bloqueado por configuración o sesión.
    Ningún código certifica por sí solo los casos de conversación.
    """
    args = _parse_args(argv)
    try:
        config = load_config()
    except PreflightConfigurationError:
        report = blocked_report("missing_or_invalid_configuration")
        _print_report(report)
        if args.evidence is not None:
            _write_evidence(args.evidence, report)
        return 2

    report = run_preflight(config)
    _print_report(report)
    if args.evidence is not None:
        _write_evidence(args.evidence, report)
    if report.status == "passed":
        return 0
    if report.status == "blocked":
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
