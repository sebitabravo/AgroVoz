"""Tests para app.jobs.sync_odepa: entrypoint CLI para cron.

Verifica exit codes: 0 cuando sync OK, 1 ante OdepaSyncError o excepción
inesperada. sync_odepa() está mockeado (la lógica de servicio se cubre en
test_odepa_service.py).
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.jobs.sync_odepa import (
    _ejecutar,
    _leer_fallos_consecutivos,
    _registrar_resultado_sync,
    _touch_sync_timestamp,
    get_sync_stale_hours,
    main,
)
from app.services.odepa_service import OdepaSyncError, SyncResult


@pytest.fixture(autouse=True)
def _archivos_aislados(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirige los archivos de timestamp/contador a tmp_path — sin tocar backend/data/ real."""
    monkeypatch.setattr("app.jobs.sync_odepa._TS_FILE", tmp_path / ".odepa_last_sync")
    monkeypatch.setattr("app.jobs.sync_odepa._FAIL_COUNT_FILE", tmp_path / ".odepa_fail_count")


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
        """Excepción de sistema da exit 1 sin exponer mensaje ni traceback."""
        secret_message = "boom-ruta-privada"
        with (
            patch(
                "app.jobs.sync_odepa.sync_odepa",
                new=AsyncMock(side_effect=OSError(secret_message)),
            ),
            patch("app.jobs.sync_odepa.logger.error") as mock_log,
        ):
            rc = await _ejecutar()
        assert rc == 1
        mock_log.assert_called_once()
        assert "Sync ODEPA falló con error inesperado" in mock_log.call_args[0][0]
        assert mock_log.call_args[0][1] == "OSError"
        assert secret_message not in str(mock_log.call_args)

    async def test_ok_reinicia_contador_de_fallos(self) -> None:
        """Un sync exitoso despues de fallos resetea el contador a 0 (#176)."""
        with patch(
            "app.jobs.sync_odepa.sync_odepa",
            new=AsyncMock(side_effect=OdepaSyncError("falla")),
        ):
            await _ejecutar()
            await _ejecutar()
        assert _leer_fallos_consecutivos() == 2

        with patch(
            "app.jobs.sync_odepa.sync_odepa",
            new=AsyncMock(return_value=SyncResult(insertados=1, actualizados=0)),
        ):
            await _ejecutar()
        assert _leer_fallos_consecutivos() == 0


class TestRegistrarResultadoSync:
    """_registrar_resultado_sync: contador de fallos consecutivos + alerta al equipo (#176)."""

    def test_primer_fallo_cuenta_uno(self) -> None:
        assert _registrar_resultado_sync(exito=False) == 1

    def test_fallos_consecutivos_incrementan(self) -> None:
        _registrar_resultado_sync(exito=False)
        _registrar_resultado_sync(exito=False)
        assert _registrar_resultado_sync(exito=False) == 3

    def test_exito_resetea_contador(self) -> None:
        _registrar_resultado_sync(exito=False)
        _registrar_resultado_sync(exito=False)
        assert _registrar_resultado_sync(exito=True) == 0

    def test_tercer_fallo_dispara_alerta_equipo(self) -> None:
        """Definición de hecho de #176: simular 3 fallos dispara la alerta."""
        with patch("app.jobs.sync_odepa.logger.error") as mock_error:
            _registrar_resultado_sync(exito=False)
            _registrar_resultado_sync(exito=False)
            mock_error.assert_not_called()
            _registrar_resultado_sync(exito=False)

        mock_error.assert_called_once()
        assert "ALERTA EQUIPO" in mock_error.call_args[0][0]

    def test_menos_de_tres_fallos_no_alerta(self) -> None:
        with patch("app.jobs.sync_odepa.logger.error") as mock_error:
            _registrar_resultado_sync(exito=False)
            _registrar_resultado_sync(exito=False)
        mock_error.assert_not_called()


class TestGetSyncStaleHours:
    """get_sync_stale_hours: horas desde el último sync exitoso."""

    def test_nunca_sincronizo_retorna_none(self) -> None:
        assert get_sync_stale_hours() is None

    def test_sync_reciente_retorna_horas_bajas(self) -> None:
        _touch_sync_timestamp()
        stale = get_sync_stale_hours()
        assert stale is not None
        assert stale < 1.0

    def test_archivo_corrupto_retorna_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ts_file = tmp_path / ".odepa_last_sync"
        ts_file.write_text("esto no es una fecha iso")
        monkeypatch.setattr("app.jobs.sync_odepa._TS_FILE", ts_file)
        assert get_sync_stale_hours() is None


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
