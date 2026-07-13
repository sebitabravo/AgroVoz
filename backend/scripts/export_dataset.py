#!/usr/bin/env python3
"""Exporta el dataset de voz rural chilena para fine-tuning o evaluacion WER.

Issue #96. Lee ``data/dataset/manifest.jsonl``, aplica filtros opcionales y
 genera uno de los siguientes artefactos:

- Directorio con audios + ``manifest.csv``
- Archivo ``.zip`` con audios + ``manifest.csv``
- ``manifest.json`` compatible con ``scripts/eval_wer.py``

Toda la salida usa ``phone_hash``; el numero de telefono real nunca se
expone.

Ejemplos:
    uv run python scripts/export_dataset.py --output dataset_export.zip
    uv run python scripts/export_dataset.py --format json --output eval_manifest.json
    uv run python scripts/export_dataset.py --phone-hash abc... --since 2026-07-01 --output mi_export
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import shutil
import sys
import zipfile
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

# Agregar backend/ al path para importar servicios.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from app.services.dataset_service import get_dataset_dir, load_manifest_entries  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("export_dataset")


def _parse_date(value: str) -> date:
    """Parsea una fecha en formato ISO 8601 (YYYY-MM-DD)."""
    return datetime.strptime(value, "%Y-%m-%d").date()


def _entry_date(entry: dict[str, Any]) -> date | None:
    """Extrae la fecha de una entrada del manifest si existe."""
    fecha = entry.get("fecha")
    if not fecha:
        return None
    try:
        return datetime.fromisoformat(str(fecha)).date()
    except ValueError:
        return None


def _reference_text(entry: dict[str, Any]) -> str:
    """Retorna la transcripcion de referencia para evaluacion WER.

    Prioriza la transcripcion verificada manualmente. Si no existe,
    fallback a la transcripcion automatica de Whisper.
    """
    verified = entry.get("transcripcion_verificada")
    if verified is not None:
        return str(verified)
    return str(entry.get("transcripcion_whisper", ""))


def _filter_entries(
    entries: list[dict[str, Any]],
    phone_hashes: set[str] | None,
    since: date | None,
    until: date | None,
) -> list[dict[str, Any]]:
    """Aplica filtros por phone_hash y/o rango de fechas."""
    result: list[dict[str, Any]] = []
    for entry in entries:
        if phone_hashes and entry.get("phone_hash") not in phone_hashes:
            continue

        entry_d = _entry_date(entry)
        if since is not None and (entry_d is None or entry_d < since):
            continue
        if until is not None and (entry_d is None or entry_d > until):
            continue

        result.append(entry)
    return result


def _write_csv(entries: list[dict[str, Any]], output_path: Path) -> None:
    """Escribe el manifest como CSV."""
    fieldnames = [
        "audio_path",
        "transcripcion_whisper",
        "transcripcion_verificada",
        "duracion_ms",
        "fecha",
        "phone_hash",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for entry in entries:
            writer.writerow({k: entry.get(k, "") for k in fieldnames})


def _write_eval_manifest(
    entries: list[dict[str, Any]],
    output_path: Path,
) -> None:
    """Escribe un manifest.json compatible con scripts/eval_wer.py."""
    eval_entries = []
    for entry in entries:
        audio_path = entry.get("audio_path")
        if not audio_path:
            continue
        duration_ms = entry.get("duracion_ms") or 0
        eval_entries.append({
            "audio": str(audio_path),
            "text": _reference_text(entry),
            "duration_s": round(int(duration_ms) / 1000, 3),
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(eval_entries, f, ensure_ascii=False, indent=2)


def _copy_audios(
    entries: list[dict[str, Any]],
    dataset_dir: Path,
    dest_dir: Path,
) -> int:
    """Copia los archivos de audio al directorio de destino.

    Retorna la cantidad de archivos copiados.
    """
    copied = 0
    for entry in entries:
        rel_path = entry.get("audio_path")
        if not rel_path:
            continue
        src = dataset_dir / rel_path
        if not src.exists():
            logger.warning("Audio no encontrado: %s", src)
            continue
        dst = dest_dir / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    return copied


def _build_zip(
    entries: list[dict[str, Any]],
    dataset_dir: Path,
    output_path: Path,
) -> None:
    """Crea un .zip con audios + manifest.csv."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in entries:
            rel_path = entry.get("audio_path")
            if not rel_path:
                continue
            src = dataset_dir / rel_path
            if not src.exists():
                logger.warning("Audio no encontrado: %s", src)
                continue
            zf.write(src, rel_path)

        # Escribir manifest.csv dentro del zip.
        csv_lines = []
        fieldnames = [
            "audio_path",
            "transcripcion_whisper",
            "transcripcion_verificada",
            "duracion_ms",
            "fecha",
            "phone_hash",
        ]
        csv_lines.append(",".join(fieldnames))
        for entry in entries:
            row = {k: entry.get(k, "") for k in fieldnames}
            csv_lines.append(",".join(str(row[k]) for k in fieldnames))
        zf.writestr("manifest.csv", "\n".join(csv_lines).encode("utf-8"))


def export_dataset(args: argparse.Namespace) -> int:
    """Orquesta la exportacion segun los argumentos CLI.

    Retorna exit code 0 si se genero al menos una muestra, 1 en caso contrario.
    """
    dataset_dir = args.dataset_dir or get_dataset_dir()
    entries = load_manifest_entries(dataset_dir)
    logger.info("Entradas cargadas del manifest: %d", len(entries))

    phone_hashes: set[str] | None = None
    if args.phone_hash:
        phone_hashes = set(args.phone_hash)

    filtered = _filter_entries(entries, phone_hashes, args.since, args.until)
    logger.info("Entradas despues de filtros: %d", len(filtered))

    if not filtered:
        logger.warning("No hay muestras que coincidan con los filtros.")
        return 1

    output_path = Path(args.output)

    if args.format == "json":
        _write_eval_manifest(filtered, output_path)
        logger.info("Manifest JSON compatible con eval_wer.py escrito en: %s", output_path)
        return 0

    if args.format == "csv":
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_csv(filtered, output_path)
        logger.info("CSV escrito en: %s", output_path)
        return 0

    # format == "dir" o "zip".
    if args.format == "zip" or output_path.suffix == ".zip":
        if output_path.suffix != ".zip":
            output_path = output_path.with_suffix(output_path.suffix + ".zip")
        _build_zip(filtered, dataset_dir, output_path)
        logger.info("ZIP exportado en: %s (%d muestras)", output_path, len(filtered))
        return 0

    # Directorio plano: audios + manifest.csv.
    output_path.mkdir(parents=True, exist_ok=True)
    copied = _copy_audios(filtered, dataset_dir, output_path)
    _write_csv(filtered, output_path / "manifest.csv")
    logger.info(
        "Directorio exportado en: %s (%d muestras, %d audios copiados)",
        output_path,
        len(filtered),
        copied,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada del script CLI."""
    parser = argparse.ArgumentParser(
        description="Exporta el dataset de voz rural chilena para fine-tuning o evaluacion WER."
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Ruta de salida (directorio, archivo .zip, .csv o .json).",
    )
    parser.add_argument(
        "--format",
        choices=["dir", "zip", "csv", "json"],
        default="dir",
        help="Formato de salida (default: dir).",
    )
    parser.add_argument(
        "--phone-hash",
        action="append",
        help="Filtrar por phone_hash (puede repetirse).",
    )
    parser.add_argument(
        "--since",
        type=_parse_date,
        help="Fecha inicial inclusive (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--until",
        type=_parse_date,
        help="Fecha final inclusive (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        help="Directorio base del dataset (default: data/dataset).",
    )
    args = parser.parse_args(argv)
    return export_dataset(args)


if __name__ == "__main__":
    sys.exit(main())
