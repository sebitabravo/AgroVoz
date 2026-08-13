# Apply Progress — agrovoz-data-ecosystem

## Meta

- **Feature:** agrovoz-data-ecosystem
- **Linked tasks:** `specs/agrovoz-data-ecosystem/tasks.md`
- **Batches completed:** 4
- **Started:** 2026-08-13

## Batch Log

### Batch 0 — SDD / auditoría

**Tasks:** T001, T002, T003

**Files modified:**
- `specs/agrovoz-data-ecosystem/proposal.md` — alcance, alternativas y límites.
- `specs/agrovoz-data-ecosystem/requirements.md` — historias, FR y criterios medibles.
- `specs/agrovoz-data-ecosystem/design.md` — arquitectura, modelo y riesgos.
- `specs/agrovoz-data-ecosystem/constitution.md` — reglas de procedencia, frescura y fail-closed.
- `specs/agrovoz-data-ecosystem/tasks.md` — tareas trazables.

**Notes:** El catálogo no activa automáticamente las gates sensibles; fuentes sin adaptador quedan explícitamente no conectadas.

## Current State

### Completed

- [x] T001 — SDD completo (batch 0).
- [x] T002 — Auditoría del repositorio y fuentes existentes (batch 0).
- [x] T003 — Contratos fail-closed/privacidad/frescura (batch 0).

### In Progress

- Ninguna tarea local pendiente.

### Pending

- Ninguna tarea local pendiente; quedan solo gates externos de deploy, sync productiva y smoke autenticado.

## Implementation Notes

- Mantener `odepa_prices` y OpenMeteo como servicios estructurados; `data_facts` no reemplaza sus unidades ni caches.
- No agregar credenciales ni conexiones externas al request.
- Cada batch debe agregar tests antes de declarar la tarea completa.

### Batch 1 — Data Hub foundational

**Tasks:** T004–T009

**Files modified:**
- `backend/corpus/fuentes_datos.yaml` — nueve fuentes con modo, cobertura, licencia, fechas y conexión real.
- `backend/app/models/data_hub.py` — `DataSource` y `DataFact` sin PII.
- `backend/app/schemas/data_hub.py` — contratos de catálogo, estado, sync y búsqueda.
- `backend/app/services/data_hub_service.py` — validación HTTPS, ingestión local, hash, estados y frescura.
- `backend/migrations/versions/a7b8c9d0e1f2_create_data_hub.py` — upgrade/downgrade reversible.
- `backend/tests/test_data_hub_service.py` — idempotencia, stale, manifest y no-connected.

**Evidence:** sync local determinista: 9 fuentes y 114 hechos; Alembic quedó en un único head `a7b8c9d0e1f2`.

### Batch 2 — RAG, conversación y APIs

**Tasks:** T010–T018

**Files modified:**
- `backend/app/services/rag_service.py` — metadata de procedencia, revisión fail-closed y guard ante manifest inválido.
- `backend/app/services/llm_keywords.py` — fast path de capacidades sin ampliar el prompt.
- `backend/app/services/odepa_service.py` — marca de frescura posterior a sync exitoso, tolerante a rollout de migración.
- `backend/app/api/data_hub.py`, `backend/app/api/admin/data_hub.py`, `backend/app/main.py` — endpoints y sync local de startup.
- `backend/tests/test_data_hub_api.py`, `backend/tests/test_data_hub_migration.py`, `backend/tests/test_rag_service.py` — contratos, rollback y regresiones.

**Evidence:** 298 tests focalizados pasan; búsquedas devuelven fuente/URL/fecha o mensaje fail-closed.

### Batch 3 — Ecosistema de producto y documentación

**Tasks:** T019–T024

**Files modified:**
- `landing/src/pages/index.astro`, `landing/src/components/Header.astro` — sección Ecosistema con copy honesto y catálogo visible.
- `README.md`, `docs/README.md`, `docs/ARCHITECTURE.md` — modelo, flujo, límites, fuentes conectadas/no conectadas y gates.
- `specs/agrovoz-data-ecosystem/` — trazabilidad de implementación y verificación.

**Evidence:** cinco fuentes conectadas por servicios/corpus versionado; INIA Pulso, CIREN/IDE Minagri, INE y CampoClick permanecen `not_connected` sin inventar adaptadores.

### Batch 4 — Verificación final

**Tasks:** T025–T028

**Evidence:** `make test` → 2236 passed, 3 skipped, 86.54% coverage; `make lint` pasa; `make typecheck` pasa sobre `app/`; mypy pasa sobre app y tests nuevos; `bun run build` genera 3 páginas; `git diff --check` pasa; revisión de seguridad sin hallazgos críticos. Una muestra local de 10 syncs del Data Hub dio máximo 55.83 ms y la primera búsqueda RAG 37.53 ms; no reemplaza una medición en el VPS mínimo.

## Estado de cierre local

- **Completed:** T001–T028 para el alcance del repositorio.
- **External gates:** no se hizo deploy/push; falta aplicar migración y correr sync/smoke en el entorno publicado; las cuatro fuentes sin adaptador siguen deliberadamente no conectadas.
- **Sensitive gates:** panel, reportes, registro de gastos, parcelas y reglas agronómicas no se activaron automáticamente.
