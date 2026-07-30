"""Servicio de monitoreo de salud del sistema para el dashboard admin.

Expone métricas de hardware (CPU/RAM/disco vía psutil) y estado de los
servicios del pipeline (Whisper, LLM, Open-WA, SQLite). No persiste nada:
cada llamada lee el estado actual. Diseñado para polling desde el
dashboard admin vía HTMX o el endpoint JSON /admin/api/monitor.

El procesamiento del pipeline es síncrono en el MVP (sin Celery/Redis),
por lo que la profundidad de cola (queue_depth) es siempre 0. Se mantiene
el campo para compatibilidad con el modo asíncrono futuro.
"""

import asyncio
import datetime
import logging
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx
import psutil
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)

# Timestamp de arranque del proceso. Se captura al importar el módulo
# (que ocurre durante el lifespan de FastAPI). Sirve para calcular uptime.
_STARTED_AT = datetime.datetime.now(tz=datetime.UTC)

# Disco a monitorear. En el VPS Hetzner CX43 hay un único disco, así que
# "/" cubre DB, audio temporal y logs. Hardcodeado deliberadamente.
_DISK_PATH = "/"

# Alerta temprana del corpus INDAP (#136 A4): el servicio ya falla cerrado
# el día que vence, pero eso es tarde para el equipo. A 7 días de
# "revisar_antes_de" se marca degradado para dar tiempo a re-verificar antes
# del corte real.
_INDAP_REVIEW_WARNING_DAYS = 7


def _safe_error_detail(prefix: str, exc: BaseException) -> str:
    """Construye detalle operacional sin filtrar mensajes, paths ni URLs."""
    return f"{prefix} · error={type(exc).__name__}"


@dataclass(frozen=True)
class SystemStats:
    """Métricas de hardware del VPS en el instante de la consulta."""

    cpu_percent: float
    ram_percent: float
    ram_used_mb: float
    ram_total_mb: float
    disk_percent: float
    disk_used_gb: float
    disk_total_gb: float


@dataclass(frozen=True)
class ServiceCheck:
    """Estado de un servicio individual del pipeline."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class MonitorSnapshot:
    """Estado completo del sistema para el dashboard de monitoreo."""

    started_at: datetime.datetime
    uptime_seconds: float
    system: SystemStats
    services: list[ServiceCheck] = field(default_factory=list)
    queue_depth: int = 0


def get_system_stats() -> SystemStats:
    """Lee CPU, RAM y disco vía psutil.

    cpu_percent(interval=None) retorna el uso desde la última llamada
    (o 0.0 la primera vez). Aceptable para polling del dashboard.

    Si /proc no está disponible (container sin acceso al host), retorna
    SystemStats con valores cero — el dashboard muestra "N/D".
    """
    try:
        cpu = psutil.cpu_percent(interval=None)
    except (FileNotFoundError, OSError, PermissionError):
        cpu = 0.0
    try:
        mem = psutil.virtual_memory()
    except (FileNotFoundError, OSError, PermissionError):
        mem = type("_Mem", (), {"percent": 0.0, "used": 0, "total": 0})()
    try:
        disk = psutil.disk_usage(_DISK_PATH)
    except (FileNotFoundError, OSError, PermissionError):
        disk = type("_Disk", (), {"percent": 0.0, "used": 0, "total": 0})()
    return SystemStats(
        cpu_percent=round(cpu, 1),
        ram_percent=round(mem.percent, 1),
        ram_used_mb=round(mem.used / 1024 / 1024, 1),
        ram_total_mb=round(mem.total / 1024 / 1024, 1),
        disk_percent=round(disk.percent, 1),
        disk_used_gb=round(disk.used / 1024 / 1024 / 1024, 2),
        disk_total_gb=round(disk.total / 1024 / 1024 / 1024, 2),
    )


def _check_whisper() -> ServiceCheck:
    """Verifica si el modelo Whisper está cargado en memoria (lazy loading)."""
    from app.services.whisper_service import WhisperService

    try:
        svc = WhisperService()
    except (RuntimeError, OSError, ImportError) as exc:
        return ServiceCheck(
            "Whisper STT",
            False,
            _safe_error_detail("no disponible", exc),
        )
    if svc.is_loaded:
        return ServiceCheck("Whisper STT", True, f"{svc.model_name} · en memoria")
    return ServiceCheck("Whisper STT", False, f"{svc.model_name} · lazy (sin cargar)")


def _check_llm() -> ServiceCheck:
    """Verifica el estado del modelo LLM (Qwen2.5-3B) leyendo las variables de módulo."""
    # Import local: llm_service importa llama_cpp (pesado) solo al usar.
    # try/except por simetría con _check_whisper/_check_tts: si un refactor
    # renombra los attrs privados (_model, _model_loaded, _model_error) o
    # llama_cpp no está instalado, reportamos fallo del check en vez de
    # tirar toda la página de monitor (que se polled cada 30s vía HTMX).
    try:
        from app.services import llm_service

        loaded = llm_service._model is not None and llm_service._model_loaded
        error = llm_service._model_error
    except (AttributeError, ImportError) as exc:
        return ServiceCheck(
            "LLM Qwen 2.5",
            False,
            _safe_error_detail("no disponible", exc),
        )
    if loaded:
        return ServiceCheck("LLM Qwen 2.5", True, "3B Q4 · en memoria")
    if error:
        return ServiceCheck("LLM Qwen 2.5", False, error)
    return ServiceCheck("LLM Qwen 2.5", False, "lazy (sin cargar)")


def _check_tts() -> ServiceCheck:
    """Verifica el modelo Piper TTS (lazy loading, igual que Whisper).

    El modelo se carga en la primera síntesis. Si el archivo .onnx no
    existe se reporta como fallo claro; si existe pero aún no se cargó,
    queda en estado lazy (operativo a la primera consulta).
    """
    from app.services.tts_service import TTSService

    try:
        svc = TTSService()
    except (RuntimeError, OSError, ImportError) as exc:
        return ServiceCheck(
            "Piper TTS",
            False,
            _safe_error_detail("no disponible", exc),
        )
    if svc.is_loaded:
        return ServiceCheck("Piper TTS", True, f"{svc.voice_name} · en memoria")
    if not Path(svc.model_path).exists():
        return ServiceCheck("Piper TTS", False, f"{svc.voice_name} · modelo no encontrado")
    return ServiceCheck("Piper TTS", False, f"{svc.voice_name} · lazy (sin cargar)")


def _check_sqlite() -> ServiceCheck:
    """Ejecuta SELECT 1 contra la DB para verificar conectividad."""
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        return ServiceCheck(
            "SQLite",
            False,
            _safe_error_detail("consulta fallida", exc),
        )
    finally:
        db.close()
    return ServiceCheck("SQLite", True, "agrovoz.db · ok")


def _check_odepa_data() -> ServiceCheck:
    """Verifica la frescura de los precios ODEPA en la DB.

    El sync automático corre diario a las 06:00. Si el último precio
    tiene más de 7 días, los mercados pueden no haber publicado (fin de
    semana/feriados) o el sync está fallando: se marca como degradado
    para que el equipo lo revise.
    """
    from sqlalchemy import func

    from app.models.odepa_price import OdepaPrice

    db = SessionLocal()
    try:
        ultima_fecha = db.query(func.max(OdepaPrice.fecha)).scalar()
        total = db.query(func.count(OdepaPrice.fecha)).scalar() or 0
    except SQLAlchemyError as exc:
        return ServiceCheck(
            "ODEPA Datos",
            False,
            _safe_error_detail("consulta fallida", exc),
        )
    finally:
        db.close()

    if ultima_fecha is None:
        return ServiceCheck("ODEPA Datos", False, "sin datos · correr sync")

    dias = (datetime.date.today() - ultima_fecha).days
    detalle = f"{ultima_fecha.isoformat()} · {total} filas"
    if dias > 7:
        return ServiceCheck("ODEPA Datos", False, f"{detalle} · {dias}d sin actualizar")
    # Alerta temprana: sync no corrió en >3 días (#176).
    try:
        from app.jobs.sync_odepa import get_sync_stale_hours

        stale = get_sync_stale_hours()
        if stale is not None and stale > 72:
            return ServiceCheck("ODEPA Datos", False, f"{detalle} · sync {stale:.0f}h atrasado")
    except ImportError:
        pass
    return ServiceCheck("ODEPA Datos", True, detalle)


def _check_indap_corpus(*, today: datetime.date | None = None) -> ServiceCheck:
    """Verifica la vigencia del corpus de derivación crediticia INDAP.

    El servicio de derivación (indap_credit_service) ya falla cerrado el día
    que el corpus vence, respondiendo con un mensaje seguro en vez de datos
    obsoletos. Este check adelanta esa señal al dashboard para que el equipo
    re-verifique la fuente ANTES del corte, no después.

    Args:
        today: Fecha inyectable para tests deterministas.
    """
    from app.services.indap_credit_service import get_corpus_metadata

    metadata = get_corpus_metadata()
    if metadata is None:
        return ServiceCheck("Corpus INDAP", False, "corpus ilegible o corrupto")

    _verified_on, review_before = metadata
    effective_today = today or datetime.date.today()
    dias_restantes = (review_before - effective_today).days
    detalle = f"vigente hasta {review_before.isoformat()}"

    if dias_restantes < 0:
        return ServiceCheck("Corpus INDAP", False, f"{detalle} · vencido hace {-dias_restantes}d")
    if dias_restantes <= _INDAP_REVIEW_WARNING_DAYS:
        return ServiceCheck("Corpus INDAP", False, f"{detalle} · vence en {dias_restantes}d")
    return ServiceCheck("Corpus INDAP", True, detalle)


async def _check_openwa() -> ServiceCheck:
    """Ping a la API de Open-WA para verificar que el gateway WhatsApp responde.

    Timeout corto (3s) para no bloquear el dashboard si Open-WA está caído.
    Solo HTTP 2xx indica sesión activa: 401/403 significan auth fallida o
    sesión expirada (QR sin escanear, desconexión WhatsApp Web), y se
    reportan como fallo para que el equipo reaccione antes de que los
    mensajes de los agricultores queden sin responder.
    """
    base = settings.openwa_api_url.rstrip("/")
    headers: dict[str, str] = {}
    if settings.openwa_api_key:
        headers["X-API-Key"] = settings.openwa_api_key
    parsed = urlparse(base)
    if parsed.port:
        puerto = str(parsed.port)
    elif parsed.scheme == "https":
        puerto = "443"
    else:
        puerto = "80"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base}/api/sessions", headers=headers)
    except (httpx.HTTPError, OSError) as exc:
        return ServiceCheck(
            "Open-WA",
            False,
            _safe_error_detail("sin conexión", exc),
        )
    if 200 <= resp.status_code < 300:
        return ServiceCheck("Open-WA", True, f":{puerto} · sesión activa")
    if resp.status_code in (401, 403):
        return ServiceCheck("Open-WA", False, f":{puerto} · sesión NO autenticada (HTTP {resp.status_code})")
    return ServiceCheck("Open-WA", False, f"HTTP {resp.status_code}")


async def check_services() -> list[ServiceCheck]:
    """Estado de todos los servicios del pipeline.

    SQLite, ODEPA y Open-WA hacen I/O real (query DB, HTTP request).
    Whisper, LLM y TTS solo leen estado en memoria. Si un check lanza, se
    captura y se reporta como fallo del servicio sin abortar los demás.

    Orden de la grilla (3 columnas): pipeline de voz arriba (Whisper, LLM,
    Piper) e infraestructura/datos abajo (SQLite, ODEPA, Open-WA).
    """
    # _check_sqlite y _check_odepa_data son síncronos (SessionLocal + queries):
    # delegarlos a un thread evita bloquear el event loop durante el poll de
    # monitoreo (cada 30s vía HTMX), que compite con requests del pipeline.
    sqlite_check = await asyncio.to_thread(_check_sqlite)
    odepa_check = await asyncio.to_thread(_check_odepa_data)
    indap_check = await asyncio.to_thread(_check_indap_corpus)
    checks = [_check_whisper(), _check_llm(), _check_tts(), sqlite_check, odepa_check, indap_check]
    checks.append(await _check_openwa())
    return checks


def get_uptime() -> datetime.timedelta:
    """Tiempo transcurrido desde el arranque del proceso."""
    return datetime.datetime.now(tz=datetime.UTC) - _STARTED_AT


async def get_monitor_snapshot() -> MonitorSnapshot:
    """Snapshot completo: sistema + servicios + uptime. Para /admin/api/monitor."""
    uptime = get_uptime()
    return MonitorSnapshot(
        started_at=_STARTED_AT,
        uptime_seconds=uptime.total_seconds(),
        system=get_system_stats(),
        services=await check_services(),
        queue_depth=0,  # MVP síncrono: sin cola.
    )


def reset_uptime_for_tests(now: datetime.datetime | None = None) -> None:
    """Resetea _STARTED_AT para tests deterministas de uptime.

    Los tests de get_uptime no deben depender de cuándo se importó el módulo.
    Esta función permite fijar el arranque a un momento conocido.
    """
    global _STARTED_AT
    _STARTED_AT = now or datetime.datetime.now(tz=datetime.UTC)


# ── Acciones operativas (botones del dashboard) ──────────────────────


def clear_weather_cache() -> int:
    """Limpia el cache de clima en memoria. Retorna el número de entradas eliminadas."""
    from app.services.weather_service import clear_weather_cache as _do_clear

    return _do_clear()


def clear_audio_temp_files() -> int:
    """Elimina archivos de audio temporal en data/audio_temp/.

    Retorna el número de archivos eliminados. Solo borra archivos .wav y .ogg
    (extensiones del pipeline). No borra subdirectorios ni otros tipos de archivo.
    No lanza si el directorio no existe o está vacío.
    """
    from pathlib import Path

    from app.services.audio_service import _AUDIO_TEMP_DIR

    temp_dir = Path(_AUDIO_TEMP_DIR)
    if not temp_dir.exists():
        return 0

    deleted = 0
    for ext in (".wav", ".ogg"):
        for f in temp_dir.glob(f"*{ext}"):
            try:
                f.unlink()
                deleted += 1
            except OSError:
                logger.warning("No se pudo eliminar un archivo temporal")

    return deleted


def get_audio_temp_count() -> int:
    """Retorna el número de archivos de audio temporal existentes."""
    from pathlib import Path

    from app.services.audio_service import _AUDIO_TEMP_DIR

    temp_dir = Path(_AUDIO_TEMP_DIR)
    if not temp_dir.exists():
        return 0

    return len(list(temp_dir.glob("*.wav"))) + len(list(temp_dir.glob("*.ogg")))


def reload_llm() -> dict[str, object]:
    """Intenta cargar el modelo LLM si no está ya en memoria.

    Si el modelo ya está cargado, retorna status=ok sin hacer nada.
    Si no está cargado, dispara el preload en un hilo daemon.
    Si hubo un error previo, lo limpia y reintenta.

    Retorna un dict con: {status, detail, was_loaded}.
    """
    from app.services import llm_service

    was_loaded = llm_service._model_loaded and llm_service._model is not None

    if was_loaded:
        return {"status": "ok", "detail": "El modelo LLM ya está cargado en memoria.", "was_loaded": True}

    # Limpiar error previo si existe para permitir reintento.
    if llm_service._model_error:
        llm_service._model_error = None
        llm_service._model_loaded = False

    try:
        llm_service.preload_model()
    except (RuntimeError, OSError) as exc:
        # preload_model() solo hace Thread.start(); acotamos a lo que puede
        # lanzar esa llamada en runtime (creacion de thread / OS), sin
        # tragar programming bugs que deberian propagar.
        return {
            "status": "error",
            "detail": _safe_error_detail("No se pudo iniciar la carga del LLM", exc),
            "was_loaded": False,
        }

    return {"status": "ok", "detail": "Carga del modelo LLM iniciada en segundo plano.", "was_loaded": False}


async def check_openwa_now() -> dict[str, object]:
    """Verifica el estado de Open-WA en tiempo real.

    A diferencia del snapshot que corre en el poll de 30s, esta función
    fuerza un check inmediato y retorna el resultado.

    Retorna un dict con: {ok, detail}.
    """
    svc = await _check_openwa()
    return {"ok": svc.ok, "detail": svc.detail}
