# Tasks — ai-agronomic-guidance

## Meta

- **Feature:** ai-agronomic-guidance
- **Author:** Sebastian Bravo / Codex
- **Status:** ready
- **Total tasks:** 10
- **Linked spec:** `specs/ai-agronomic-guidance/requirements.md`
- **Linked design:** `specs/ai-agronomic-guidance/design.md`

## Phase 1: Tests first — User Stories 1–3

- [x] T001 [US1] Agregar regresión de prompt local/remoto que permita orientación citada y prohíba improvisación en `backend/tests/test_llm_service.py`.
- [x] T002 [P] [US1] Agregar prueba de fast path para síntoma conocido con fuente/fecha en `backend/tests/test_fast_path.py`.
- [x] T003 [P] [US2] Verificar prueba de calendario con gate activo y fallback sin cobertura en `backend/tests/test_agricultural_calendar_service.py` y `backend/tests/test_fast_path.py`.
- [x] T004 [US3] Verificar la matriz de configuración: el flag permanece cerrado por defecto y acepta opt-in explícito en `backend/tests/test_feature_flags_matrix.py`.

**Checkpoint:** las regresiones nuevas fallan antes del cambio de prompts/configuración.

## Phase 2: Implementation

- [x] T005 [US1] Cambiar `backend/app/services/prompt_builder.py` para permitir solo recomendaciones devueltas por tools citadas.
- [x] T006 [US1] Cambiar `_OPENROUTER_SYSTEM_PROMPT` y descripciones de orientación en `backend/app/services/llm_service.py`.
- [x] T007 [US2] Activar `AGRONOMIC_RULES_ENABLED=true` en `.env.example`, `docker-compose.yml` y `docker-compose.prod.yml`, conservando `Settings` fail-closed sin env explícito.
- [x] T008 [US3] Agregar smoke dedicado de recomendación y calendario sin debilitar los 25 casos existentes.

**Checkpoint:** la IA local responde calendario/regla por camino determinista y las demás consultas permanecen sin cambio.

## Phase 3: Documentation and verification

- [x] T009 [P] [US1] Actualizar `README.md`, `docs/ARCHITECTURE.md`, `docs/DATA_HUB_SOURCES.md` y `docs/DEPLOYMENT.md` con capacidad, límites y activación.
- [x] T010 [US1] Actualizar `apply-progress.md`, checklist y ejecutar tests focalizados, suite completa, lint, mypy, landing y smoke local.

## Final Verification

- [x] Tests focalizados de reglas, calendario, fast path, demo y LLM pasan.
- [x] `make test` pasa con cobertura >= 85% (2258 passed, 3 skipped, 86.40%).
- [x] `make lint` y `make typecheck` pasan.
- [x] El smoke verifica respuestas citadas y no recomendaciones inventadas (32/32).
- [ ] Publicación/smoke productivo se mantiene separado hasta tener canal autorizado.
