#!/usr/bin/env python3
"""Evaluacion de Word Error Rate (WER) para Whisper en espanol chileno.

Uso:
    uv run python scripts/eval_wer.py                                   # default: dataset chileno completo
    uv run python scripts/eval_wer.py --model tiny                       # probar con modelo tiny
    uv run python scripts/eval_wer.py --samples 20                       # solo 20 muestras
    uv run python scripts/eval_wer.py --output resultados.json           # guardar resultados
    uv run python scripts/eval_wer.py --manifest data/test_audio/mi_test/manifest.json  # dataset custom

Requiere:
    pip install jiwer
    Modelo Whisper (se descarga automaticamente la primera vez)
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import jiwer

# Agregar backend/ al path para poder importar servicios
_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from app.services.whisper_service import WhisperService  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("eval_wer")

# Directorio default del dataset chileno
_DEFAULT_MANIFEST = _BACKEND_DIR / "data" / "test_audio" / "chilean_spanish" / "manifest.json"

# Transformaciones de normalizacion para WER justo en espanol chileno
_WER_TRANSFORM = jiwer.Compose([
    jiwer.ToLowerCase(),
    jiwer.RemoveMultipleSpaces(),
    jiwer.RemoveWhiteSpace(replace_by_space=True),
    jiwer.Strip(),
    jiwer.ReduceToListOfListOfWords(),
])


def normalize_text(text: str) -> str:
    """Normaliza texto para comparacion WER.

    - minusculas
    - eliminar puntuacion (.,;:!?¡¿"')
    - eliminar tildes (opcional, desactivado por ahora — Whisper las produce)
    - espacios simples
    """
    import re
    text = text.lower()
    text = re.sub(r"[.,;:!?¡¿\"'()\-]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def compute_wer(reference: str, hypothesis: str) -> float:
    """Calcula WER entre dos textos usando jiwer.

    Normaliza ambos textos antes de comparar.
    """
    ref_norm = normalize_text(reference)
    hyp_norm = normalize_text(hypothesis)

    if not ref_norm.strip():
        return 0.0

    return jiwer.wer(
        reference=ref_norm, hypothesis=hyp_norm,
        reference_transform=_WER_TRANSFORM,
        hypothesis_transform=_WER_TRANSFORM,
    )


def transcribe_batch(
    whisper: WhisperService,
    samples: list[dict],
    model_name: str = "small",
) -> list[dict]:
    """Transcribe un lote de muestras y mide latencia por muestra.

    Args:
        whisper: Instancia de WhisperService.
        samples: Lista de dicts con 'audio' (path relativo) y 'text' (referencia).
        model_name: Nombre del modelo Whisper.

    Returns:
        Lista de resultados con transcripcion y metadatos.
    """
    results = []
    total_start = time.monotonic()

    for i, sample in enumerate(samples):
        audio_path = _BACKEND_DIR / "data" / "test_audio" / "chilean_spanish" / sample["audio"]
        reference = sample["text"]
        duration_s = sample.get("duration_s", 0)

        if not audio_path.exists():
            logger.warning("Archivo no encontrado: %s", audio_path)
            continue

        # Transcribir
        t0 = time.monotonic()
        try:
            result = whisper.transcribe(str(audio_path))
            hypothesis = result.get("text", "")
        except Exception as e:
            logger.error("Error transcribiendo %s: %s", audio_path.name, e)
            hypothesis = ""

        t1 = time.monotonic()
        latency_s = round(t1 - t0, 3)

        # Calcular WER
        wer = compute_wer(reference, hypothesis) if hypothesis else 1.0
        rt_factor = round(latency_s / duration_s, 2) if duration_s > 0 else 0

        results.append({
            "file": sample["audio"],
            "reference": reference,
            "hypothesis": hypothesis,
            "duration_s": duration_s,
            "latency_s": latency_s,
            "rt_factor": rt_factor,
            "wer": round(wer, 4),
        })

        logger.info(
            "[%d/%d] WER=%.1f%% lat=%.1fs dur=%.1fs RT=%.1fx — %s",
            i + 1, len(samples),
            wer * 100, latency_s, duration_s, rt_factor,
            audio_path.name,
        )

    total_elapsed = time.monotonic() - total_start
    logger.info(
        "Batch completo: %d muestras en %.1fs (promedio %.2fs/muestra)",
        len(results), total_elapsed,
        total_elapsed / len(results) if results else 0,
    )

    return results


def compute_aggregate(results: list[dict]) -> dict:
    """Calcula metricas agregadas del lote."""
    if not results:
        return {}

    wer_values = [r["wer"] for r in results]
    wer_sorted = sorted(wer_values)
    wer_n = len(wer_sorted)
    wer_median = (
        (wer_sorted[wer_n // 2 - 1] + wer_sorted[wer_n // 2]) / 2
        if wer_n % 2 == 0
        else wer_sorted[wer_n // 2]
    )

    latencies = [r["latency_s"] for r in results]
    durations = [r["duration_s"] for r in results]
    rt_factors = [r["rt_factor"] for r in results]

    return {
        "total_samples": len(results),
        "total_duration_s": round(sum(durations), 1),
        "avg_duration_s": round(sum(durations) / len(durations), 2),
        "total_latency_s": round(sum(latencies), 1),
        "avg_latency_s": round(sum(latencies) / len(latencies), 3),
        "wer_mean": round(sum(wer_values) / len(wer_values), 4),
        "wer_median": round(wer_median, 4),
        "wer_min": round(min(wer_values), 4),
        "wer_max": round(max(wer_values), 4),
        "wer_std": round(
            (sum((w - sum(wer_values) / len(wer_values)) ** 2 for w in wer_values)
             / len(wer_values)) ** 0.5,
            4,
        ),
        "rt_mean": round(sum(rt_factors) / len(rt_factors), 2),
        "rt_max": round(max(rt_factors), 2),
        "model": "whisper",
    }


def format_wer(wer: float) -> str:
    """Formatea WER como porcentaje."""
    return f"{wer * 100:.1f}%"


def print_report(results: list[dict], aggregate: dict) -> None:
    """Imprime reporte formateado en consola."""
    print()
    print("=" * 70)
    print("  REPORTE WER — WHISPER ESPANOL CHILENO")
    print("=" * 70)
    print()
    print(f"  Muestras evaluadas:    {aggregate['total_samples']}")
    print(f"  Duracion total audio:  {aggregate['total_duration_s']:.1f}s ({aggregate['total_duration_s']/60:.1f} min)")
    print(f"  Duracion promedio:     {aggregate['avg_duration_s']:.1f}s")
    print()
    print("  -- WORD ERROR RATE (WER) --")
    print(f"  Media:                 {format_wer(aggregate['wer_mean'])}")
    print(f"  Mediana:               {format_wer(aggregate['wer_median'])}")
    print(f"  Minimo:                {format_wer(aggregate['wer_min'])}")
    print(f"  Maximo:                {format_wer(aggregate['wer_max'])}")
    print(f"  Desv. estandar:        {format_wer(aggregate['wer_std'])}")
    print()
    print("  -- LATENCIA --")
    print(f"  Tiempo total:          {aggregate['total_latency_s']:.1f}s")
    print(f"  Promedio por muestra:  {aggregate['avg_latency_s']:.3f}s")
    print()
    print("  -- REAL-TIME FACTOR --")
    print(f"  RTF medio:             {aggregate['rt_mean']}x")
    print(f"  RTF maximo:            {aggregate['rt_max']}x")
    print()
    print("  -- PEORES 5 WER --")
    for r in sorted(results, key=lambda x: x["wer"], reverse=True)[:5]:
        print(f"    {format_wer(r['wer'])} | {r['file']}")
        print(f"      ref:  {r['reference'][:80]}")
        print(f"      hyp:  {r['hypothesis'][:80]}")
    print()
    print("  -- MEJORES 5 WER --")
    for r in sorted(results, key=lambda x: x["wer"])[:5]:
        print(f"    {format_wer(r['wer'])} | {r['file']}")
        print(f"      ref:  {r['reference'][:80]}")
        print(f"      hyp:  {r['hypothesis'][:80]}")
    print()
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluar WER de Whisper en espanol chileno")
    parser.add_argument(
        "--manifest",
        type=str,
        default=str(_DEFAULT_MANIFEST),
        help="Path al manifest.json con las muestras de prueba",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="small",
        choices=["tiny", "base", "small", "medium", "large", "turbo"],
        help="Modelo Whisper a evaluar (default: small)",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=0,
        help="Limite de muestras a procesar (0 = todas)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Guardar resultados en archivo JSON",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        logger.error("Manifest no encontrado: %s", manifest_path)
        logger.error("Ejecuta primero la descarga del dataset o crea un manifest propio.")
        sys.exit(1)

    with open(manifest_path) as f:
        samples = json.load(f)

    if args.samples > 0:
        samples = samples[:args.samples]

    logger.info("Manifest: %s", manifest_path)
    logger.info("Modelo: whisper-%s", args.model)
    logger.info("Muestras: %d", len(samples))

    # Inicializar Whisper
    whisper = WhisperService(model_name=args.model)
    logger.info("Modelo cargado. Dispositivo: %s", whisper._device)

    # Transcribir
    results = transcribe_batch(whisper, samples, model_name=args.model)
    aggregate = compute_aggregate(results)

    # Reporte
    print_report(results, aggregate)

    # Guardar resultados
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump({
                "config": {
                    "model": args.model,
                    "samples": len(samples),
                    "manifest": str(manifest_path),
                },
                "aggregate": aggregate,
                "per_sample": results,
            }, f, ensure_ascii=False, indent=2)
        logger.info("Resultados guardados en: %s", output_path)

    # Exit code: 0 si WER < 15% (target MVP), 1 si no
    if aggregate.get("wer_mean", 1.0) < 0.15:
        logger.info("RESULTADO: WER %.1f%% — DENTRO DEL TARGET (< 15%%)", aggregate["wer_mean"] * 100)
        sys.exit(0)
    else:
        logger.warning("RESULTADO: WER %.1f%% — FUERA DEL TARGET (< 15%%)", aggregate["wer_mean"] * 100)
        sys.exit(1)


if __name__ == "__main__":
    main()
