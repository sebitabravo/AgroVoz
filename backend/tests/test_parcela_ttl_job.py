"""Regresiones del enforcement TTL de parcelas: job CLI y scheduler (C5).

La tabla fija ``expires_at`` por fila, pero sin alguien que ejecute la purga el
TTL queda declarado y nunca aplicado. Estas pruebas cubren las dos vías que lo
ejecutan: el job one-shot para cron y la tarea de fondo del lifespan.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.jobs.purge_parcelas import main as purge_job_main
from app.main import (
    _PARCELA_PURGE_INTERVAL_SECONDS,
    _cancel_background_task,
    _parcela_purge_scheduler,
    _start_parcela_purge_scheduler,
)
from app.services.parcela_service import ParcelaOperationError


class TestPurgeParcelasJob:
    """CLI one-shot invocable desde cron sin levantar la app."""

    def test_exit_cero_cuando_la_purga_confirma(self) -> None:
        """Una purga confirmada reporta éxito al orquestador de cron."""
        with patch(
            "app.jobs.purge_parcelas.purge_expired_parcelas",
            return_value=4,
        ) as purge:
            exit_code = purge_job_main([])

        assert exit_code == 0
        purge.assert_called_once_with()

    def test_exit_uno_si_operacion_no_se_confirma(self) -> None:
        """Un error tipado produce estado fallido para cron/Dokploy."""
        with patch(
            "app.jobs.purge_parcelas.purge_expired_parcelas",
            side_effect=ParcelaOperationError("fallo"),
        ):
            exit_code = purge_job_main([])

        assert exit_code == 1


class TestParcelaPurgeScheduler:
    """Verifica ejecución diaria, resiliencia y cierre del lifespan."""

    @pytest.mark.asyncio
    async def test_ejecuta_en_thread_y_espera_24_horas(self) -> None:
        """La operación síncrona nunca bloquea directamente el event loop."""
        to_thread = AsyncMock(return_value=2)
        sleep = AsyncMock(side_effect=asyncio.CancelledError)

        with (
            patch("app.main.asyncio.to_thread", to_thread),
            patch("app.main.asyncio.sleep", sleep),
            pytest.raises(asyncio.CancelledError),
        ):
            await _parcela_purge_scheduler()

        assert to_thread.await_count == 1
        sleep.assert_awaited_once_with(_PARCELA_PURGE_INTERVAL_SECONDS)

    @pytest.mark.asyncio
    async def test_continua_despues_de_error_operativo(self) -> None:
        """Una purga fallida no mata las ejecuciones diarias siguientes."""
        to_thread = AsyncMock(side_effect=[ParcelaOperationError("fallo"), 0])
        sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])

        with (
            patch("app.main.asyncio.to_thread", to_thread),
            patch("app.main.asyncio.sleep", sleep),
            pytest.raises(asyncio.CancelledError),
        ):
            await _parcela_purge_scheduler()

        assert to_thread.await_count == 2
        assert sleep.await_count == 2

    @pytest.mark.asyncio
    async def test_gate_apagado_igual_crea_la_tarea(self) -> None:
        """Apagar escrituras nuevas no suspende la retención de lo ya guardado."""
        started = asyncio.Event()

        async def controlled_scheduler() -> None:
            """Permanece activo hasta que el shutdown lo cancele."""
            started.set()
            await asyncio.Event().wait()

        with (
            patch.object(settings, "parcela_tracking_enabled", False),
            patch("app.main._parcela_purge_scheduler", controlled_scheduler),
        ):
            task = _start_parcela_purge_scheduler()
            await started.wait()
            await _cancel_background_task(task)

        assert task.cancelled()
