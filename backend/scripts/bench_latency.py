#!/usr/bin/env python3
"""Benchmark de latencia del LLM con las 7 tools activas.

Mide `answer()` end-to-end con consultas representativas de precio,
clima y margen. Resultados en data/bench_results.json.

Uso:
    cd backend
    uv run python scripts/bench_latency.py

Requiere el modelo Qwen2.5-3B Q4_K_M descargado en la ruta configurada
en LLM_MODEL_PATH (.env o default: models/qwen2.5-3b-q4_k_m.gguf).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sys
import time
from pathlib import Path

# Permitir importar app/ desde scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.services.llm_service import answer, preload_model

logger = logging.getLogger("bench_latencia")

# ── Queries de benchmark ──────────────────────────────────────────────
BENCH_QUERIES: list[dict[str, object]] = [
    {
        "query": "¿a cómo está la papa en Temuco?",
        "intent": "precio",
        "cultivos": ["papa"],
        "system_tip": None,
    },
    {
        "query": "¿cómo va a estar el tiempo mañana en Traiguén?",
        "intent": "clima",
        "cultivos": None,
        "system_tip": None,
    },
    {
        "query": "vendí 100 kilos de papa a $800 cada uno, ¿cómo me fue?",
        "intent": "margen",
        "cultivos": ["papa"],
        "system_tip": (
            "El agricultor menciona una venta YA REALIZADA. "
            "Si te da producto, cantidad, unidad y monto total, "
            "usa calculate_margin para comparar contra la referencia ODEPA."
        ),
    },
]


def _format_ms(ms: float) -> str:
    """Formatea milisegundos en formato legible."""
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.1f}s"


async def _run_bench() -> list[dict[str, object]]:
    """Ejecuta las queries de benchmark y retorna resultados."""
    results: list[dict[str, object]] = []

    for i, entry in enumerate(BENCH_QUERIES):
        query = str(entry["query"])
        intent = str(entry["intent"])
        cultivos = entry.get("cultivos")
        system_tip = entry.get("system_tip")

        if isinstance(cultivos, list):
            cultivos = [str(c) for c in cultivos]

        logger.info(
            "[%d/%d] Ejecutando query %s: %s",
            i + 1,
            len(BENCH_QUERIES),
            intent,
            query[:60],
        )

        t0 = time.perf_counter()
        try:
            response = await answer(
                query_text=query,
                history=[],
                cultivos=cultivos,  # type: ignore[arg-type]
                system_tip=str(system_tip) if system_tip else None,
            )
        except Exception as exc:
            logger.exception("Error en query %s: %s", intent, exc)
            response = f"ERROR: {exc}"
        t1 = time.perf_counter()

        total_ms = (t1 - t0) * 1000

        result: dict[str, object] = {
            "query": query,
            "intent": intent,
            "total_ms": round(total_ms, 1),
            "response_preview": response[:200] if response else "",
        }
        results.append(result)

        logger.info(
            "[%d/%d] %s → %s (respuesta: %.80s)",
            i + 1,
            len(BENCH_QUERIES),
            intent,
            _format_ms(total_ms),
            response,
        )

        # Pequeña pausa entre queries para dejar que el sistema respire.
        if i < len(BENCH_QUERIES) - 1:
            await asyncio.sleep(2)

    return results


def _compute_summary(results: list[dict[str, object]]) -> dict[str, object]:
    """Calcula estadísticas agregadas de los resultados."""
    ms_values = [float(r["total_ms"]) for r in results]  # type: ignore[arg-type]
    if not ms_values:
        return {"avg_total_ms": 0, "min_total_ms": 0, "max_total_ms": 0, "num_queries": 0}
    return {
        "avg_total_ms": round(sum(ms_values) / len(ms_values), 1),
        "min_total_ms": round(min(ms_values), 1),
        "max_total_ms": round(max(ms_values), 1),
        "num_queries": len(ms_values),
    }


async def main() -> None:
    """Punto de entrada del benchmark."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Verificar modelo
    model_path = settings.llm_model_path
    if not os.path.isfile(model_path):
        logger.error(
            "Modelo LLM no encontrado en %s. Descargalo con: make download-models",
            model_path,
        )
        sys.exit(1)

    logger.info("Benchmark de latencia LLM — n_ctx=%d, modelo=%s", 4096, model_path)
    logger.info("Cargando modelo (cold start)...")

    # Pre-cargar modelo (bloqueante para benchmark preciso).
    # preload_model() es async (thread daemon), asi que esperamos a que cargue.
    preload_model()
    # Dar tiempo al thread daemon para que termine la carga.
    await asyncio.sleep(0.5)

    logger.info("Iniciando %d queries de benchmark...", len(BENCH_QUERIES))
    t_start = time.perf_counter()

    results = await _run_bench()

    t_total = time.perf_counter() - t_start
    summary = _compute_summary(results)

    # ── Reporte ────────────────────────────────────────────────────
    output = {
        "device": "CPU",
        "n_ctx": 4096,
        "n_threads": os.cpu_count() or 4,
        "model": os.path.basename(model_path),
        "results": results,
        "summary": summary,
        "wall_clock_total_s": round(t_total, 1),
    }

    # Guardar JSON
    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / "bench_results.json"
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Resumen en stdout ──────────────────────────────────────────
    print()
    print("=" * 60)
    print("  BENCHMARK LATENCIA LLM — RESULTADOS")
    print("=" * 60)
    print(f"  Dispositivo:  CPU ({output['n_threads']} threads)")
    print(f"  Modelo:       {output['model']}")
    print(f"  n_ctx:        {output['n_ctx']}")
    print(f"  Queries:      {summary['num_queries']}")
    print("-" * 60)
    for r in results:
        print(f"  [{r['intent']:8s}] {_format_ms(float(r['total_ms'])):>8s}  "
              f"{r['query'][:50]}")
    print("-" * 60)
    print(f"  Promedio:     {_format_ms(float(summary['avg_total_ms']))}")  # type: ignore[arg-type]
    print(f"  Mínimo:       {_format_ms(float(summary['min_total_ms']))}")  # type: ignore[arg-type]
    print(f"  Máximo:       {_format_ms(float(summary['max_total_ms']))}")  # type: ignore[arg-type]
    print(f"  Wall clock:   {t_total:.1f}s")
    print("=" * 60)
    print(f"\nReporte detallado: {output_path}")

    # ── Diagnóstico vs target ───────────────────────────────────────
    target_s = 15.0
    llm_avg_s = float(summary["avg_total_ms"]) / 1000  # type: ignore[arg-type]
    if llm_avg_s > target_s:
        print(f"\n⚠️  LATENCIA EXCEDE TARGET (<15s E2E)")
        print(f"   Solo LLM: {llm_avg_s:.1f}s promedio")
        print(f"   Faltan: Whisper (~3-5s) + TTS (~2-3s) + red (~2s)")
        print(f"   Estimado E2E: ~{llm_avg_s + 8:.0f}s")
        print(f"   Acción requerida: comprimir prompt, reducir tools, o documentar trade-off.")
    else:
        print(f"\n✅  Latencia dentro del target (<15s E2E)")


if __name__ == "__main__":
    asyncio.run(main())
