"""Tests deterministas del preflight de Open-WA.

No se conecta a WhatsApp ni a Open-WA: valida el contrato de configuración y
que el reporte nunca incluya secretos o identificadores de la sesión.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from scripts import openwa_e2e_preflight as preflight
from scripts.openwa_e2e_preflight import (
    PreflightConfig,
    PreflightConfigurationError,
    PreflightReport,
    blocked_report,
    count_ready_sessions,
    load_config,
    main,
    report_as_dict,
    run_preflight,
)


def test_load_config_requiere_api_key() -> None:
    """Sin API key el preflight queda bloqueado antes de tocar la red."""
    with pytest.raises(PreflightConfigurationError, match="OPENWA_API_KEY"):
        load_config({})


def test_load_config_rechaza_timeout_no_positivo() -> None:
    """Un timeout inválido no debe producir una request sin límite."""
    with pytest.raises(PreflightConfigurationError, match="positivo"):
        load_config({"OPENWA_API_KEY": "test-key", "OPENWA_E2E_TIMEOUT": "0"})


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ([{"id": "s1", "status": "ready"}], 1),
        ([{"id": "s1", "status": "ACTIVE"}, {"id": "s2", "status": "qr"}], 1),
        ([{"id": "s1", "status": "qr"}], 0),
        ({"sessions": []}, -1),
    ],
)
def test_ready_session_count_no_expone_ids(
    payload: object,
    expected: int,
) -> None:
    """Solo cuenta estados listos y no depende del ID de sesión."""
    assert count_ready_sessions(payload) == expected


def test_report_as_dict_omite_api_key_y_identificadores() -> None:
    """El JSON de evidencia no debe filtrar secretos ni IDs de Open-WA."""
    report = PreflightReport(
        status="passed",
        generated_at="2026-08-03T12:00:00+00:00",
        checks=(),
    )
    rendered = json.dumps(report_as_dict(report), ensure_ascii=False)

    assert "test-key" not in rendered
    assert "session-secret-id" not in rendered
    assert "api_key" not in rendered
    assert "note" in rendered


def test_main_sin_secreto_es_bloqueado_y_escribe_reporte(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """La ejecución local sin credencial produce evidencia explícita de bloqueo."""
    monkeypatch.delenv("OPENWA_API_KEY", raising=False)
    evidence_path = tmp_path / "preflight.json"

    exit_code = main(["--evidence", str(evidence_path)])

    assert exit_code == 2
    report = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["checks"][0]["error_code"] == "missing_or_invalid_configuration"


def test_blocked_report_no_contiene_mensaje_de_error_crudo() -> None:
    """Los detalles del entorno no se copian al artefacto compartible."""
    report = blocked_report("missing_or_invalid_configuration")

    assert report.checks[0].detail is None
    assert report.checks[0].error_code == "missing_or_invalid_configuration"


def test_run_preflight_pasa_con_backend_y_sesion_lista(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El preflight pasa solo cuando ambos servicios responden correctamente."""
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/health":
            return httpx.Response(200, json={"status": "ok"})
        assert request.url.path == "/api/sessions"
        assert request.headers["X-API-Key"] == "test-key"
        return httpx.Response(200, json=[{"id": "session-id", "status": "active"}])

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        preflight.httpx,
        "Client",
        lambda timeout: real_client(transport=transport, timeout=timeout),
    )
    config = PreflightConfig(
        backend_url="http://backend",
        openwa_url="http://openwa",
        api_key="test-key",
        timeout_seconds=1,
    )

    report = run_preflight(config)

    assert report.status == "passed"
    assert [check.check_id for check in report.checks] == [
        "backend_liveness",
        "openwa_session",
    ]


def test_run_preflight_bloquea_sesion_sin_qr_autenticado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una respuesta válida sin sesión lista mantiene el gate bloqueado."""
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json=[{"id": "session-id", "status": "qr"}])

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        preflight.httpx,
        "Client",
        lambda timeout: real_client(transport=transport, timeout=timeout),
    )
    config = PreflightConfig(
        backend_url="http://backend",
        openwa_url="http://openwa",
        api_key="test-key",
        timeout_seconds=1,
    )

    report = run_preflight(config)

    assert report.status == "blocked"
    assert report.checks[1].error_code == "session_not_ready"
