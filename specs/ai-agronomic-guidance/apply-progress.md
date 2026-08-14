# Apply Progress — ai-agronomic-guidance

## Current State

- ✅ Explore: se confirmó que el motor determinista y las tools existen.
- ✅ Proposal/Requirements/Design: documentados en esta spec.
- ✅ Apply: prompts, runtime Compose, regresiones y smoke agronómico implementados.
- ✅ Verify local: suite, lint/types, build y smoke pasan; publicación/smoke público queda como gate externo separado.

## Evidence Log

### 2026-08-14 — Explore

- `prompt_builder.py` dice `NUNCA recomendaciones agronómicas`.
- `_OPENROUTER_SYSTEM_PROMPT` repite la prohibición.
- `AGRONOMIC_RULES_ENABLED` default `false`.
- `pipeline_service.py` ya contiene fast paths deterministas para calendario y reglas.
- `agronomic_rules_service.py` y `agricultural_calendar_service.py` validan corpus, vigencia, fuente y fallback.

### 2026-08-14 — Apply

- Los prompts local/OpenRouter permiten solo orientación devuelta por
  `get_regla_agronomica`/`get_calendario_agricola` y prohíben improvisación,
  diagnósticos personalizados, dosis y tratamientos.
- `.env.example` y Compose demo/prod entregan `AGRONOMIC_RULES_ENABLED=true`;
  `Settings` sin env continúa en `false`.
- `SMOKE_AGRONOMIC_REGRESSION=1` prueba una regla de papa y un calendario de
  trigo con fuente/fecha; la suite pública de 25 casos no se modifica.
- Verificación focal: 40 pruebas pasaron antes de la suite completa; la suite final pasó 2258 tests.

### 2026-08-14 — Verify

- `make test`: 2258 passed, 3 skipped, cobertura total 86.40%.
- `make lint`: `All checks passed!`.
- `make typecheck`: `Success: no issues found in 104 source files`.
- `cd landing && bun run test`: 8 tests passed; `bun run build`: 3 páginas generadas.
- Smoke combinado en runtime temporal con el gate activo: 32/32 pasaron,
  incluyendo Data Hub, los 25 casos públicos y las dos respuestas citadas.
- El runtime temporal se eliminó después de la verificación. Producción no se
  marca como corregida sin publicación autorizada y smoke externo.

## Next Task

Sin tareas locales pendientes. El único gate abierto es externo: publicar el
artefacto y ejecutar el smoke público cuando exista un canal autorizado.
