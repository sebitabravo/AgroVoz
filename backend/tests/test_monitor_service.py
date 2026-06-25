"""Tests para monitor_service (T5.4).

Cubre:
- get_system_stats: lectura de CPU/RAM/disco con psutil mockeado (determinista).
- get_uptime / reset_uptime_for_tests: cálculo de uptime sin depender del
  momento de import del módulo.
- get_monitor_snapshot: ensamblado del snapshot con check_services y
  get_system_stats monkeypatcheado (evita I/O real de Open-WA y DB).
- ServiceCheck / SystemStats / MonitorSnapshot: dataclasses frozen.
- Acciones operativas: clear_weather_cache, clear_audio_temp_files,
  get_audio_temp_count, reload_llm, check_openwa_now.
"""

import datetime
import pathlib
from types import SimpleNamespace

import psutil
import pytest

from app.services import monitor_service
from app.services.monitor_service import (
    MonitorSnapshot,
    ServiceCheck,
    SystemStats,
    get_monitor_snapshot,
    get_system_stats,
    get_uptime,
    reset_uptime_for_tests,
)

# ── get_system_stats ───────────────────────────────────────────────


class TestGetSystemStats:
    """Lectura de métricas de hardware vía psutil (mockeado)."""

    def test_retorna_system_stats_con_campos(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _mockear_psutil(monkeypatch, cpu=42.0)
        stats = get_system_stats()
        assert isinstance(stats, SystemStats)
        assert stats.cpu_percent == 42.0
        assert stats.ram_percent == 65.0
        assert stats.ram_used_mb == (8 * 1024)
        assert stats.ram_total_mb == (16 * 1024)
        assert stats.disk_percent == 70.0
        assert stats.disk_used_gb == 80.0
        assert stats.disk_total_gb == 160.0

    def test_cpu_se_redondea_a_un_decimal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _mockear_psutil(monkeypatch, cpu=33.456)
        assert get_system_stats().cpu_percent == 33.5

    def test_ram_se_redondea_a_un_decimal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _mockear_psutil(monkeypatch, cpu=10.0, ram_percent=77.456)
        assert get_system_stats().ram_percent == 77.5


def _mockear_psutil(
    monkeypatch: pytest.MonkeyPatch,
    cpu: float = 10.0,
    ram_percent: float = 65.0,
    disk_percent: float = 70.0,
) -> None:
    """Mockea las tres llamadas psutil que usa get_system_stats."""
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None: cpu)
    monkeypatch.setattr(
        psutil,
        "virtual_memory",
        lambda: SimpleNamespace(
            percent=ram_percent, used=8 * 1024 * 1024 * 1024, total=16 * 1024 * 1024 * 1024
        ),
    )
    monkeypatch.setattr(
        psutil,
        "disk_usage",
        lambda path: SimpleNamespace(
            percent=disk_percent,
            used=80 * 1024 * 1024 * 1024,
            total=160 * 1024 * 1024 * 1024,
        ),
    )


# ── get_uptime / reset ─────────────────────────────────────────────


class TestGetUptime:
    """Cálculo de uptime del proceso."""

    def test_uptime_positivo(self) -> None:
        assert get_uptime().total_seconds() >= 0

    def test_reset_fija_started_at(self) -> None:
        ahora = datetime.datetime.now(tz=datetime.UTC)
        reset_uptime_for_tests(ahora)
        # _STARTED_AT quedó en `ahora`; get_uptime calcula contra ahora + ε.
        delta = get_uptime()
        assert delta.total_seconds() >= 0
        # Restaurar para no contaminar otros tests.
        reset_uptime_for_tests()

    def test_reset_default_usa_ahora(self) -> None:
        antes = datetime.datetime.now(tz=datetime.UTC)
        reset_uptime_for_tests()
        # _STARTED_AT se fijó entre `antes` y este momento.
        assert antes <= monitor_service._STARTED_AT


# ── get_monitor_snapshot ───────────────────────────────────────────


class TestGetMonitorSnapshot:
    """Ensamblado del snapshot completo (sin I/O real)."""

    async def test_snapshot_contiene_system_y_services(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mockear_psutil(monkeypatch, cpu=15.0)
        monkeypatch.setattr(
            monitor_service,
            "check_services",
            _check_services_stub,
        )
        snap = await get_monitor_snapshot()
        assert isinstance(snap, MonitorSnapshot)
        assert snap.system.cpu_percent == 15.0
        assert len(snap.services) == 2
        assert snap.services[0].name == "SQLite"
        assert snap.services[0].ok is True
        assert snap.queue_depth == 0
        assert snap.uptime_seconds >= 0

    async def test_snapshot_propaga_servicio_caido(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mockear_psutil(monkeypatch, cpu=15.0)
        monkeypatch.setattr(
            monitor_service,
            "check_services",
            _check_services_caidos,
        )
        snap = await get_monitor_snapshot()
        assert snap.services[0].ok is False
        assert "sin conexión" in snap.services[0].detail


# ── dataclasses frozen ─────────────────────────────────────────────


class TestDataclassesFrozen:
    """Los snapshots son inmutables (frozen=True)."""

    def test_system_stats_es_inmutable(self) -> None:
        stats = SystemStats(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0)
        with pytest.raises(AttributeError):
            stats.cpu_percent = 99.0  # type: ignore[misc]

    def test_service_check_es_inmutable(self) -> None:
        check = ServiceCheck("X", True, "ok")
        with pytest.raises(AttributeError):
            check.ok = False  # type: ignore[misc]


# ── Stubs async para check_services ────────────────────────────────


async def _check_services_stub() -> list[ServiceCheck]:
    return [
        ServiceCheck("SQLite", True, "agrovoz.db · ok"),
        ServiceCheck("Open-WA", True, ":3000 · sesión activa"),
    ]


async def _check_services_caidos() -> list[ServiceCheck]:
    return [ServiceCheck("Open-WA", False, "sin conexión: timeout")]


# ── Acciones operativas ──────────────────────────────────────────────


class TestClearWeatherCache:
    """Limpieza del cache de clima en memoria."""

    def test_clear_devuelve_cero_si_cache_vacio(self) -> None:
        from app.services.monitor_service import clear_weather_cache

        # Vaciar primero para estado conocido.
        clear_weather_cache()
        result = clear_weather_cache()
        assert result == 0

    def test_clear_devuelve_entradas_eliminadas(self) -> None:
        from app.services.monitor_service import clear_weather_cache
        from app.services.weather_service import _cache_set, _clear_cache
        from app.services.weather_service import WeatherData

        # Limpiar estado previo.
        _clear_cache()

        wd = WeatherData(
            lat=-38.23, lon=-72.68, location="Traiguén",
            temperature_c=18.0, feels_like_c=17.0, humidity=65,
            description="nublado", wind_speed_ms=3.6, rain_1h_mm=None,
            texto="En Traiguén ahora: 18°C, nublado.",
        )
        _cache_set(-38.23, -72.68, wd)
        _cache_set(-33.45, -70.65, wd)  # Santiago

        result = clear_weather_cache()
        assert result == 2
        # Cache vacío tras clear.
        assert clear_weather_cache() == 0


class TestClearAudioTempFiles:
    """Limpieza de archivos de audio temporal."""

    def test_clear_devuelve_cero_si_directorio_no_existe(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
    ) -> None:
        import pathlib

        from app.services.monitor_service import clear_audio_temp_files

        nonexistent = tmp_path / "noexiste"
        monkeypatch.setattr(
            "app.services.audio_service._AUDIO_TEMP_DIR",
            nonexistent,
        )
        assert clear_audio_temp_files() == 0

    def test_clear_elimina_solo_wav_y_ogg(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
    ) -> None:
        import pathlib

        from app.services.monitor_service import (
            clear_audio_temp_files,
            get_audio_temp_count,
        )

        audio_dir = tmp_path / "audio_temp"
        audio_dir.mkdir()
        monkeypatch.setattr(
            "app.services.audio_service._AUDIO_TEMP_DIR",
            audio_dir,
        )

        # Crear archivos de audio y uno no-audio que no debe borrarse.
        (audio_dir / "consulta_001.wav").write_text("mock wav")
        (audio_dir / "respuesta_001.ogg").write_text("mock ogg")
        (audio_dir / "notas.txt").write_text("no borrar")

        assert get_audio_temp_count() == 2
        deleted = clear_audio_temp_files()
        assert deleted == 2
        assert get_audio_temp_count() == 0
        # El archivo .txt sigue intacto.
        assert (audio_dir / "notas.txt").exists()

    def test_get_audio_temp_count_devuelve_cero_sin_directorio(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
    ) -> None:
        import pathlib

        from app.services.monitor_service import get_audio_temp_count

        nonexistent = tmp_path / "vacio"
        monkeypatch.setattr(
            "app.services.audio_service._AUDIO_TEMP_DIR",
            nonexistent,
        )
        assert get_audio_temp_count() == 0


class TestReloadLlm:
    """Recarga del modelo LLM desde el dashboard."""

    def test_reload_con_modelo_ya_cargado(self) -> None:
        from app.services.monitor_service import reload_llm
        from app.services import llm_service

        # Simular modelo ya cargado.
        llm_service._model_loaded = True
        llm_service._model = object()  # no-None, cualquier objeto.
        try:
            result = reload_llm()
            assert result["status"] == "ok"
            assert result["was_loaded"] is True
            assert "ya está cargado" in str(result["detail"])
        finally:
            llm_service._model = None
            llm_service._model_loaded = False

    def test_reload_sin_modelo_dispara_preload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.services.monitor_service import reload_llm
        from app.services import llm_service

        # Asegurar estado: no cargado.
        llm_service._model = None
        llm_service._model_loaded = False
        llm_service._model_error = None

        called = False

        def _fake_preload() -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(llm_service, "preload_model", _fake_preload)
        result = reload_llm()
        assert called
        assert result["status"] == "ok"
        assert result["was_loaded"] is False

    def test_reload_limpia_error_previo_y_reintenta(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.services.monitor_service import reload_llm
        from app.services import llm_service

        llm_service._model = None
        llm_service._model_loaded = False
        llm_service._model_error = "error previo de carga"

        called = False

        def _fake_preload() -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(llm_service, "preload_model", _fake_preload)
        result = reload_llm()
        assert called
        assert llm_service._model_error is None
        assert result["status"] == "ok"


class TestCheckOpenwaNow:
    """Verificación manual de Open-WA."""

    async def test_check_openwa_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.services import monitor_service
        from app.services.monitor_service import ServiceCheck, check_openwa_now

        async def _fake_check() -> ServiceCheck:
            return ServiceCheck("Open-WA", True, ":3000 · sesión activa")

        monkeypatch.setattr(monitor_service, "_check_openwa", _fake_check)
        result = await check_openwa_now()
        assert result["ok"] is True
        assert "sesión activa" in str(result["detail"])

    async def test_check_openwa_caido(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.services import monitor_service
        from app.services.monitor_service import ServiceCheck, check_openwa_now

        async def _fake_check() -> ServiceCheck:
            return ServiceCheck("Open-WA", False, "sin conexión: timeout")

        monkeypatch.setattr(monitor_service, "_check_openwa", _fake_check)
        result = await check_openwa_now()
        assert result["ok"] is False
        assert "timeout" in str(result["detail"])
