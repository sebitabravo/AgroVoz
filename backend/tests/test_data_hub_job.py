"""Pruebas del job one-shot de actualización del Data Hub."""

from unittest.mock import Mock

from app.jobs import sync_data_hub as job
from app.services.data_hub_remote import RemoteDataHubSyncResult
from app.services.data_hub_service import DataHubSyncResult


def _local_result() -> DataHubSyncResult:
    """Entrega un resultado local mínimo para aislar el job."""
    return DataHubSyncResult(
        sources_synced=10,
        facts_synced=121,
        stale_sources=(),
        not_connected_sources=("campoclick_directorio_productores",),
    )


def test_job_retorna_ok_si_todas_las_fuentes_remotas_responden(monkeypatch) -> None:
    db = Mock()
    monkeypatch.setattr(job, "SessionLocal", lambda: db)
    monkeypatch.setattr(job, "sync_data_hub", lambda _db: _local_result())
    monkeypatch.setattr(
        job,
        "sync_remote_data_hub",
        lambda _db: RemoteDataHubSyncResult(sources_synced=3, source_errors=()),
    )

    assert job.main() == 0
    db.close.assert_called_once_with()


def test_job_retorna_falla_si_hay_error_remoto_saneado(monkeypatch) -> None:
    db = Mock()
    monkeypatch.setattr(job, "SessionLocal", lambda: db)
    monkeypatch.setattr(job, "sync_data_hub", lambda _db: _local_result())
    monkeypatch.setattr(
        job,
        "sync_remote_data_hub",
        lambda _db: RemoteDataHubSyncResult(
            sources_synced=2,
            source_errors=("ciren_ide_minagri:http_status",),
        ),
    )

    assert job.main() == 1
    db.close.assert_called_once_with()
