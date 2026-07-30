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
import json
import logging
import shutil
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

from app.core.phone_hash import validate_phone_hash

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Directorio base para el dataset de voz rural chilena.
# En Docker: /app/data/dataset/ (volumen backend/data/).
_DATASET_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "dataset"

# Lock para proteger el append concurrente al manifest.jsonl.
_manifest_lock = threading.Lock()


def get_dataset_dir() -> Path:
    """Retorna el directorio base del dataset, creandolo si no existe."""
    _DATASET_DIR.mkdir(parents=True, exist_ok=True)
    return _DATASET_DIR


def _get_manifest_path(dataset_dir: Path | None = None) -> Path:
    """Ruta al manifest.jsonl dentro del directorio del dataset."""
    directory = dataset_dir or get_dataset_dir()
    return directory / "manifest.jsonl"


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
