"""Servicio de retención selectiva de audio para el dataset de voz rural.

Issue #96 — Dataset de voz rural chilena. Solo retiene audio cuando el
productor tiene ``dataset_consent=True`` en ``user_prefs``. Los audios se
almacenan anonimizados bajo ``data/dataset/{phone_hash}/`` y se registra
un manifest JSONL con metadatos por muestra.

Privacy by design:
- El número de teléfono real NUNCA se persiste.
- Las rutas usan ``phone_hash`` (HMAC-SHA256).
- Default ``dataset_consent=False``: sin opt-in explicito no se retiene nada.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import logging
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.phone_hash import validate_phone_hash

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Directorio base para el dataset de voz rural chilena.
# En Docker: /app/data/dataset/ (volumen backend/data/).
_DATASET_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "dataset"

# Lock para proteger el append concurrente al manifest.jsonl.
_manifest_lock = threading.Lock()
_audit_lock = threading.Lock()
_AUDIT_KEY_VERSION = 1


class DatasetOperationError(RuntimeError):
    """La purga del dataset no pudo confirmarse de forma auditable."""


DatasetPurgeOutcome = Literal["completed", "no_records"]


@dataclass(frozen=True, slots=True)
class DatasetPurgeResult:
    """Resultado sin identificadores directos de una purga de dataset."""

    event_id: str
    records_deleted: int
    files_deleted: int
    outcome: DatasetPurgeOutcome


def get_dataset_dir() -> Path:
    """Retorna el directorio base del dataset, creandolo si no existe."""
    _DATASET_DIR.mkdir(parents=True, exist_ok=True)
    return _DATASET_DIR


def _get_manifest_path(dataset_dir: Path | None = None) -> Path:
    """Ruta al manifest.jsonl dentro del directorio del dataset."""
    directory = dataset_dir or get_dataset_dir()
    return directory / "manifest.jsonl"


def _get_audit_path(dataset_dir: Path | None = None) -> Path:
    """Ruta del ledger append-only de purgas, separado del manifest."""
    directory = dataset_dir or get_dataset_dir()
    return directory / "deletion_audit.jsonl"


def _get_audit_key() -> str:
    """Obtiene la clave dedicada o rechaza una operación no auditable."""
    audit_key = settings.consultation_history_audit_key.get_secret_value().strip()
    if len(audit_key) < 32:
        raise DatasetOperationError("La clave dedicada de auditoría no está configurada de forma segura.")
    return audit_key


def _subject_token(phone_hash: str) -> str:
    """Seudonimiza el sujeto del ledger sin persistir su hash original."""
    return hmac.new(
        _get_audit_key().encode("utf-8"),
        phone_hash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _normalize_event_id(event_id: str | None) -> str:
    """Normaliza el UUID idempotente o genera uno nuevo."""
    try:
        return str(uuid.UUID(event_id)) if event_id else str(uuid.uuid4())
    except (AttributeError, ValueError) as exc:
        raise DatasetOperationError("El event_id no es un UUID válido.") from exc


def _load_audit_entries(audit_path: Path) -> list[dict[str, object]]:
    """Carga eventos válidos, ignorando líneas corruptas sin exponer contenido."""
    if not audit_path.exists():
        return []
    entries: list[dict[str, object]] = []
    try:
        with open(audit_path, encoding="utf-8") as audit_file:
            for line in audit_file:
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Auditoría dataset parcialmente cargada — estado=linea_corrupta")
                    continue
                if isinstance(parsed, dict) and isinstance(parsed.get("event_id"), str):
                    entries.append(parsed)
    except OSError as exc:
        raise DatasetOperationError("No fue posible leer la auditoría del dataset.") from exc
    return entries


def _append_audit_entry(audit_path: Path, entry: dict[str, object]) -> None:
    """Agrega un evento sin PII al ledger de purgas."""
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(audit_path, "a", encoding="utf-8") as audit_file:
            audit_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        raise DatasetOperationError("No fue posible registrar la auditoría del dataset.") from exc


def _audit_count(entry: dict[str, object], field: str) -> int:
    """Lee un contador validado del ledger sin confiar en JSON arbitrario."""
    value = entry.get(field)
    return value if isinstance(value, int) and value >= 0 else 0


def _manifest_entry_belongs_to_subject(entry: dict[str, object], phone_hash: str) -> bool:
    """Determina pertenencia por hash o ruta relativa segura del manifest."""
    if entry.get("phone_hash") == phone_hash:
        return True
    audio_path = entry.get("audio_path")
    if not isinstance(audio_path, str):
        return False
    try:
        relative_path = Path(audio_path)
        return relative_path.parts[0] == phone_hash
    except (IndexError, TypeError):
        return False


def _purge_manifest_entries(manifest_path: Path, phone_hash: str) -> int:
    """Quita entradas del sujeto y conserva líneas ajenas o corruptas."""
    if not manifest_path.exists():
        return 0
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError as exc:
        raise DatasetOperationError("No fue posible leer el manifest del dataset.") from exc

    kept: list[str] = []
    removed = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if isinstance(entry, dict) and _manifest_entry_belongs_to_subject(entry, phone_hash):
            removed += 1
        else:
            kept.append(line)

    if removed == 0:
        return 0
    temporary_path = manifest_path.with_name(f"{manifest_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary_path.write_text("".join(kept), encoding="utf-8")
        temporary_path.replace(manifest_path)
    except OSError as exc:
        temporary_path.unlink(missing_ok=True)
        raise DatasetOperationError("No fue posible confirmar la purga del manifest.") from exc
    return removed


def purge_dataset_for_subject(
    phone_hash: str,
    *,
    dataset_dir: Path | None = None,
    event_id: str | None = None,
) -> DatasetPurgeResult:
    """Purga WAV y manifest de un sujeto tras revocar dataset_consent.

    La operación exige una clave de auditoría, usa un UUID idempotente y deja
    únicamente conteos y un token HMAC en el ledger. Sin clave, identificador
    inválido o fallo de almacenamiento se rechaza sin afirmar borrado.
    """
    if not validate_phone_hash(phone_hash):
        raise DatasetOperationError("phone_hash inválido para purga de dataset.")

    normalized_event_id = _normalize_event_id(event_id)
    subject_token = _subject_token(phone_hash)
    directory = dataset_dir or get_dataset_dir()
    audit_path = _get_audit_path(directory)

    with _audit_lock:
        existing = [
            entry
            for entry in _load_audit_entries(audit_path)
            if entry.get("event_id") == normalized_event_id
        ]
        if existing:
            latest = existing[-1]
            if latest.get("subject_token") != subject_token or latest.get("reason") != "consent_revoked":
                raise DatasetOperationError("El event_id ya pertenece a otra purga de dataset.")
            if latest.get("outcome") in {"completed", "no_records"}:
                return DatasetPurgeResult(
                    event_id=normalized_event_id,
                    records_deleted=_audit_count(latest, "records_deleted"),
                    files_deleted=_audit_count(latest, "files_deleted"),
                    outcome=cast(DatasetPurgeOutcome, latest["outcome"]),
                )

        target_dir = directory / phone_hash
        files_deleted = 0
        if target_dir.exists():
            try:
                for wav_path in target_dir.glob("*.wav"):
                    if wav_path.is_file():
                        wav_path.unlink()
                        files_deleted += 1
            except OSError as exc:
                raise DatasetOperationError("No fue posible purgar los audios del dataset.") from exc

        manifest_entries_deleted = _purge_manifest_entries(_get_manifest_path(directory), phone_hash)
        outcome: DatasetPurgeOutcome = (
            "completed" if files_deleted > 0 or manifest_entries_deleted > 0 else "no_records"
        )
        _append_audit_entry(
            audit_path,
            {
                "event_id": normalized_event_id,
                "subject_token": subject_token,
                "key_version": _AUDIT_KEY_VERSION,
                "reason": "consent_revoked",
                "records_deleted": manifest_entries_deleted,
                "files_deleted": files_deleted,
                "outcome": outcome,
                "occurred_at": datetime.datetime.now(datetime.UTC).isoformat(),
            },
        )
        logger.info(
            "Purga de dataset completada — estado=%s records=%d files=%d",
            outcome,
            manifest_entries_deleted,
            files_deleted,
        )
        return DatasetPurgeResult(
            event_id=normalized_event_id,
            records_deleted=manifest_entries_deleted,
            files_deleted=files_deleted,
            outcome=outcome,
        )


def has_dataset_consent(phone_hash: str, db: Session | None = None) -> bool:
    """Retorna True si el phone_hash tiene consentimiento para retener audio.

    Args:
        phone_hash: Hash HMAC-SHA256 del teléfono.
        db: Sesión de SQLAlchemy opcional. Si no se provee, crea una nueva.

    Returns:
        True solo si existe una fila UserPrefs con ``dataset_consent=True``.
        Cualquier error o ausencia de prefs retorna False (safe default).
    """
    if not phone_hash or phone_hash == "sin_chat":
        return False
    if not validate_phone_hash(phone_hash):
        logger.warning("Dataset de voz omitido — estado=identificador_invalido")
        return False

    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.user_prefs import UserPrefs

    own_session = db is None
    session = db if db is not None else SessionLocal()
    try:
        prefs = session.scalar(select(UserPrefs).where(UserPrefs.phone_hash == phone_hash))
        return bool(prefs is not None and prefs.dataset_consent)
    except SQLAlchemyError as exc:
        # Fail-closed: ante error de DB se asume sin consentimiento (privacidad
        # por defecto). Otros errores son bugs y deben propagarse.
        logger.error(
            "Dataset de voz omitido — estado=consentimiento_no_verificado error_type=%s",
            type(exc).__name__,
        )
        return False
    finally:
        if own_session:
            session.close()


def retain_audio(
    wav_path: Path,
    phone_hash: str,
    transcription: str,
    duration_ms: int,
    dataset_dir: Path | None = None,
    db: Session | None = None,
) -> Path | None:
    """Retiene una muestra de audio en el dataset si hay consentimiento.

    El consentimiento se consulta via ``has_dataset_consent``. Si no hay
    consentimiento, retorna None sin side effects (privacidad por defecto).

    Args:
        wav_path: Ruta al archivo .wav 16kHz mono listo para copiar.
        phone_hash: Hash HMAC-SHA256 del teléfono (usado en ruta y manifest).
        transcription: Texto transcrito por Whisper.
        duration_ms: Duración del audio en milisegundos.
        dataset_dir: Directorio base del dataset opcional (para tests).
        db: Sesión de SQLAlchemy opcional (para tests).

    Returns:
        Path al archivo retenido en el dataset, o None si no se retuvo.
    """
    # Defensa en profundidad: phone_hash forma parte de la ruta en disco.
    # has_dataset_consent tambien valida, pero este guard es independiente.
    if not validate_phone_hash(phone_hash):
        logger.warning("Dataset de voz omitido — estado=identificador_invalido")
        return None

    if not has_dataset_consent(phone_hash, db=db):
        logger.debug("Dataset de voz omitido — estado=sin_consentimiento")
        return None

    directory = dataset_dir or get_dataset_dir()
    sample_dir = directory / phone_hash
    sample_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
    sample_id = uuid.uuid4().hex[:8]
    dest_path = sample_dir / f"{timestamp}_{sample_id}.wav"

    try:
        shutil.copy2(wav_path, dest_path)
    except (OSError, shutil.Error) as exc:
        logger.error(
            "Dataset de voz no retenido — estado=error_copia error_type=%s",
            type(exc).__name__,
        )
        return None

    entry = {
        "audio_path": str(dest_path.relative_to(directory)),
        "transcripcion_whisper": transcription,
        "transcripcion_verificada": None,
        "duracion_ms": duration_ms,
        "fecha": datetime.datetime.now(datetime.UTC).isoformat(),
        "phone_hash": phone_hash,
    }

    # Atomicidad audio+manifest: una muestra sin entrada en el manifest es
    # invisible para eval_wer.py y el export. Si el manifest falla, se borra
    # la copia para no dejar audio huérfano en disco.
    manifest_path = _get_manifest_path(directory)
    with _manifest_lock:
        try:
            with open(manifest_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.error(
                "Dataset de voz no retenido — estado=error_manifest error_type=%s",
                type(exc).__name__,
            )
            dest_path.unlink(missing_ok=True)
            return None

    logger.info("Dataset de voz actualizado — estado=audio_retenido count=1")
    return dest_path


def load_manifest_entries(dataset_dir: Path | None = None) -> list[dict[str, object]]:
    """Carga todas las entradas del manifest.jsonl.

    Args:
        dataset_dir: Directorio base del dataset opcional (para tests).

    Returns:
        Lista de dicts con los metadatos de cada muestra retenida.
        Si el manifest no existe, retorna lista vacía.
    """
    manifest_path = _get_manifest_path(dataset_dir)
    if not manifest_path.exists():
        return []

    entries: list[dict[str, object]] = []
    with open(manifest_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.warning(
                    "Dataset de voz parcialmente cargado — estado=linea_corrupta error_type=%s",
                    type(exc).__name__,
                )
    return entries
