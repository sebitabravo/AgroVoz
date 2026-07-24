"""Test de regresión para configuración de workers en producción.

ISSUE: Bug P1 — LLM latencia 60s en lugar de <2s cuando hay --workers 2.
CAUSA: Global singletons (_model, _model_cache) NO se comparten entre procesos.
Cada worker recarga Whisper (462MB) + Qwen (2.1GB) desde disco (~10-15s cada uno).

SOLUCIÓN: --workers 1 en docker-compose.prod.yml para guardar estado en memoria.

Este test es un "regression gate": falla si alguien vuelve a cambiar workers a 2+ sin
una justificación explícita documentada.
"""

import re
from pathlib import Path


def test_docker_compose_prod_has_single_worker() -> None:
    """Valida que docker-compose.prod.yml tenga --workers 1 (no 2+).

    Regresión del bug P1: con --workers 2, cada worker recarga los modelos LLM/Whisper
    desde cero (no comparte memoria con el padre ni otros workers), causando:
      - llm_ms: 1-2s → 40-60s (20-60x degradación)
      - whisper_ms: 1.7s → 18s (10x degradación)
      - latency_ms: 4.5s → 82s

    Con --workers 1: modelo singleton en memoria, reutilizado por todos los requests.
    En piloto de 3-5 productores, throughput de 1 worker > latencia de N workers.

    Trade-offs documentados en AGENTS.md, línea "Hard constraints: Latencia".
    """
    compose_path = Path(__file__).parent.parent.parent / "docker-compose.prod.yml"
    assert compose_path.exists(), f"docker-compose.prod.yml no encontrado en {compose_path}"

    content = compose_path.read_text()

    # Buscar la sección de uvicorn command
    pattern = r'--workers["\s]*,\s*["\s]*(\d+)'
    matches = re.findall(pattern, content)

    assert matches, (
        "No se encontró --workers en docker-compose.prod.yml. "
        "Verifica que uvicorn command incluya --workers explícitamente."
    )

    # Debe haber un solo match y su valor debe ser "1"
    assert len(matches) == 1, (
        f"Se encontraron {len(matches)} valores de --workers. "
        "Debe haber exactamente 1."
    )

    workers_value = int(matches[0])
    assert workers_value == 1, (
        f"docker-compose.prod.yml tiene --workers {workers_value}, pero debe ser 1. "
        f"\nMotivo: Global singletons (_model, _model_cache) NO se comparten entre procesos. "
        f"Con --workers 2+ cada worker recarga Qwen (2.1GB) + Whisper (462MB) desde disco, "
        f"causando latencia 60s en lugar de <2s. \n"
        f"Si necesitas más throughput, considerar: \n"
        f"  1. Cachés de aplicación (Redis) para compartir estado entre workers.\n"
        f"  2. Load balancer con múltiples instancias de la app.\n"
        f"  3. Documentación de la decisión en AGENTS.md si cambias esto."
    )


if __name__ == "__main__":
    test_docker_compose_prod_has_single_worker()
    print("✓ docker-compose.prod.yml tiene --workers 1 (correcto)")
