#!/usr/bin/env python3
"""Microbenchmark saneado de latencia del LLM local.

Mide ``answer()`` con consultas sintéticas de precio, clima y margen. No mide
Whisper, TTS, Open-WA ni latencia E2E y, por sí solo, no cierra el issue #215.
El reporte omite consultas y respuestas para que pueda adjuntarse sin exponer
contenido conversacional.

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
import os
import platform
import sys
import time
from pathlib import Path

# Permitir importar app/ desde scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.services.llm_service import (
    answer,
    get_model_error,
    is_model_available,
    preload_model,
)

logger = logging.getLogger("bench_latencia")

# ── Queries de benchmark ──────────────────────────────────────────────
BENCH_QUERIES: list[dict[str, object]] = [
    {
        "case_id": "precio_mercado",
        "query": "¿a cómo está la papa en Temuco?",
        "intent": "precio",
        "cultivos": ["papa"],
        "system_tip": None,
    },
    {
        "case_id": "clima_futuro",
        "query": "¿cómo va a estar el tiempo mañana en Traiguén?",
        "intent": "clima",
        "cultivos": None,
        "system_tip": None,
    },
    {
        "case_id": "margen_venta",
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
        case_id = str(entry["case_id"])
        intent = str(entry["intent"])
        cultivos = entry.get("cultivos")
        system_tip = entry.get("system_tip")

        if isinstance(cultivos, list):
            cultivos = [str(c) for c in cultivos]

        logger.info(
            "[%d/%d] Ejecutando caso sintetico — case_id=%s intent=%s",
            i + 1,
            len(BENCH_QUERIES),
            case_id,
            intent,
        )

        t0 = time.perf_counter()
        try:
            response = await answer(
                query_text=query,
                history=[],
                cultivos=cultivos,  # type: ignore[arg-type]
                system_tip=str(system_tip) if system_tip else None,
            )
            status = "ok" if response else "empty"
            error_code = None
        except (TimeoutError, RuntimeError, OSError, ValueError) as exc:
            logger.error(
                "Caso sintetico fallo — case_id=%s error=%s",
                case_id,
                type(exc).__name__,
            )
            response = ""
            status = "error"
            error_code = type(exc).__name__
        t1 = time.perf_counter()

        total_ms = (t1 - t0) * 1000

        result: dict[str, object] = {
            "case_id": case_id,
            "intent": intent,
            "total_ms": round(total_ms, 1),
            "response_chars": len(response),
            "status": status,
            "error_code": error_code,
        }
        results.append(result)

        logger.info(
            "[%d/%d] Caso completado — case_id=%s intent=%s total=%s status=%s",
            i + 1,
            len(BENCH_QUERIES),
            case_id,
            intent,
            _format_ms(total_ms),
            status,
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


def _read_cgroup_value(path: str) -> str | None:
    """Lee un límite cgroup sin fallar fuera de Linux/contenedor."""
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


async def _wait_for_model(timeout_seconds: float = 60.0) -> bool:
    """Espera el preload con un límite explícito para un benchmark reproducible."""
    preload_model()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if is_model_available():
            return True
        await asyncio.sleep(0.1)
    return False


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

    logger.info(
        "Microbenchmark LLM — n_ctx=%d arquitectura=%s",
        4096,
        platform.machine(),
    )
    logger.info("Cargando modelo (cold start)...")

    if not await _wait_for_model():
        logger.error(
            "Worker LLM no quedo listo — error=%s",
            get_model_error() or "startup_timeout",
        )
        sys.exit(1)

    logger.info("Iniciando %d queries de benchmark...", len(BENCH_QUERIES))
    t_start = time.perf_counter()

    results = await _run_bench()

    t_total = time.perf_counter() - t_start
    summary = _compute_summary(results)

    # ── Reporte ────────────────────────────────────────────────────
    output = {
        "device": "CPU",
        "architecture": platform.machine(),
        "n_ctx": 4096,
        "n_threads": min(os.cpu_count() or 4, 8),
        "model": os.path.basename(model_path),
        "cgroup_cpu_max": _read_cgroup_value("/sys/fs/cgroup/cpu.max"),
        "cgroup_memory_max": _read_cgroup_value("/sys/fs/cgroup/memory.max"),
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
        total_ms_value = r["total_ms"]
        if not isinstance(total_ms_value, (int, float)):
            raise TypeError("total_ms invalido en resultado interno")
        print(f"  [{r['case_id']!s:16s}] {_format_ms(float(total_ms_value)):>8s}  {r['status']}")
    print("-" * 60)
    print(f"  Promedio:     {_format_ms(float(summary['avg_total_ms']))}")  # type: ignore[arg-type]
    print(f"  Mínimo:       {_format_ms(float(summary['min_total_ms']))}")  # type: ignore[arg-type]
    print(f"  Máximo:       {_format_ms(float(summary['max_total_ms']))}")  # type: ignore[arg-type]
    print(f"  Wall clock:   {t_total:.1f}s")
    print("=" * 60)
    print(f"\nReporte detallado: {output_path}")

    print("\nNOTA: este resultado es solo del LLM. Ejecute docs/validacion-operativa.md para evaluar el target E2E.")


if __name__ == "__main__":
    asyncio.run(main())
