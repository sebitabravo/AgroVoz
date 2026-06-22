"""Tests para app.jobs.sync_odepa: entrypoint CLI para cron.

Verifica exit codes: 0 cuando sync OK, 1 ante OdepaSyncError o excepción
inesperada. sync_odepa() está mockeado (la lógica de servicio se cubre en
test_odepa_service.py).
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.jobs.sync_odepa import _ejecutar, main
from app.services.odepa_service import OdepaSyncError, SyncResult


class TestEjecutar:
    """_ejecutar() devuelve exit code según resultado de sync_odepa."""

    async def test_ok_devuelve_cero(self) -> None:
        with patch(
            "app.jobs.sync_odepa.sync_odepa",
            new=AsyncMock(return_value=SyncResult(insertados=3, actualizados=1)),
        ):
            rc = await _ejecutar()
        assert rc == 0

    async def test_odepa_sync_error_devuelve_uno(self) -> None:
        with patch(
            "app.jobs.sync_odepa.sync_odepa",
            new=AsyncMock(side_effect=OdepaSyncError("HTTP 503")),
        ):
            rc = await _ejecutar()
        assert rc == 1

    async def test_excepcion_inesperada_devuelve_uno(self) -> None:
        """Excepción de BD/sistema (OSError) da exit 1 y loguea trace completo."""
        with (
            patch(
                "app.jobs.sync_odepa.sync_odepa",
                new=AsyncMock(side_effect=OSError("boom")),
            ),
            patch("app.jobs.sync_odepa.logger.exception") as mock_log,
        ):
            rc = await _ejecutar()
        assert rc == 1
        mock_log.assert_called_once()
        assert "Sync ODEPA falló con error inesperado" in mock_log.call_args[0][0]


class TestMain:
    """main() integra asyncio.run + sys.exit con el exit code de _ejecutar."""

    def test_main_exit_code_cero(self) -> None:
        with (
            patch("app.jobs.sync_odepa._ejecutar", new=AsyncMock(return_value=0)),
            pytest.raises(SystemExit) as exc,
        ):
            main()
        assert exc.value.code == 0

    def test_main_exit_code_uno(self) -> None:
        with (
            patch("app.jobs.sync_odepa._ejecutar", new=AsyncMock(return_value=1)),
            pytest.raises(SystemExit) as exc,
        ):
            main()
        assert exc.value.code == 1
