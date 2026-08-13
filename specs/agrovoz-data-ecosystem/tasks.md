# Tasks — agrovoz-data-ecosystem

## Meta

- **Feature:** agrovoz-data-ecosystem
- **Author:** spec-author
- **Status:** implemented_locally
- **Total tasks:** 28
- **Linked spec:** `specs/agrovoz-data-ecosystem/requirements.md`
- **Linked design:** `specs/agrovoz-data-ecosystem/design.md`

## Phase 1: Setup / SDD

- [x] T001 Completar propuesta, requisitos, diseño y constitución en `specs/agrovoz-data-ecosystem/`.
- [x] T002 Mapear fuentes existentes y decidir catálogo + `data_sources`/`data_facts` sin fine-tuning.
- [x] T003 Definir criterios fail-closed, no PII y no activación automática de gates.

## Phase 2: Foundational Data Hub

- [x] T004 [P] [US2] Crear `backend/corpus/fuentes_datos.yaml` con las fuentes y estados reales.
- [x] T005 [P] [US2] Crear `backend/app/models/data_hub.py` y exportar modelos desde `app/models/__init__.py`.
- [x] T006 [US2] Crear migración Alembic en `backend/migrations/versions/` y registrar modelos en `migrations/env.py`.
- [x] T007 [US2] Implementar schemas y validadores en `backend/app/schemas/data_hub.py`.
- [x] T008 [US2] Implementar `backend/app/services/data_hub_service.py` para manifest, sync, hash, status y catálogo.
- [x] T009 [P] [US2] Agregar tests de modelo/servicio en `backend/tests/test_data_hub_service.py` (GREEN verificado).

**Checkpoint:** catálogo y hechos sincronizan de forma idempotente en una DB temporal y distinguen stale/not_connected.

## Phase 3: User Story 1 — Información integrada trazable

- [x] T010 [P] [US1] Enriquecer `backend/app/services/rag_service.py` con source URL, dominio, verificación y revisión.
- [x] T011 [US1] Agregar tests de frescura y citas en `backend/tests/test_rag_service.py` y `backend/tests/test_data_hub_api.py`.
- [x] T012 [US1] Integrar Data Hub/catálogo con la tool `search_corpus` y un fast path de capacidades en `llm_keywords.py` sin agregar otra tool al prompt.
- [x] T013 [P] [US1] Agregar tests de consulta conversacional y fuente en `backend/tests/test_data_hub_service.py`, `backend/tests/test_data_hub_api.py` y `backend/tests/test_llm_service.py`.

**Independent Test:** `pytest tests/test_data_hub_service.py tests/test_rag_service.py -q` y consultas de precio/clima/conocimiento sin dato simulado.

## Phase 4: User Story 2 — Operación y auditoría

- [x] T014 [US2] Crear `backend/app/api/data_hub.py` con catálogo público y búsqueda read-only.
- [x] T015 [US2] Crear `backend/app/api/admin/data_hub.py` con status/sync protegidos.
- [x] T016 [US2] Registrar routers en `backend/app/main.py` y conectar métricas de estado sin bloquear startup.
- [x] T017 [P] [US2] Agregar tests de contrato en `backend/tests/test_data_hub_api.py`.
- [x] T018 [US2] Integrar el estado de sync de ODEPA con el registro sin modificar su lógica de precios.

**Checkpoint:** operador puede inspeccionar/sincronizar; usuario público ve capacidades sin PII.

## Phase 5: User Story 3 — Ecosistema conversacional y producto

- [x] T019 [US3] Agregar intent/fast path seguro para “qué puede hacer AgroVoz” y consultas del catálogo.
- [x] T020 [US3] Verificar integración con INIA, INDAP, CIREN, directorio e INE como snapshot/live/not_connected según manifest.
- [x] T021 [P] [US3] Actualizar `landing/src/pages/index.astro` y copy de demo sin marketplace ni afirmaciones falsas.
- [x] T022 [P] [US3] Actualizar `README.md` y `docs/ARCHITECTURE.md` con Data Hub, esquema, flujo y límites.
- [x] T023 [US3] Revisar que preferencias, consentimiento, alertas y panel existentes sigan sus gates; no activar capacidades sensibles.
- [x] T024 [P] [US3] Agregar tests/fixtures de catálogo sin PII y fallback de fuente no conectada.

**Independent Test:** API pública y demo/LLM explican capacidades y límites con fuentes reales; `make test` conserva los regresions previos.

## Phase 6: Polish & Verification

- [x] T025 Ejecutar migración en DB temporal y revisar rollback.
- [x] T026 Ejecutar `make test`, `make lint`, `make typecheck`, build de landing y `git diff --check`.
- [x] T027 Revisar diff final por secretos, debug, TODOs, PII y cambios fuera de alcance.
- [x] T028 Completar checklist y `apply-progress.md`; documentar bloqueos externos (deploy/credenciales/adaptadores sin API).

## Traceability

- FR-001: T004, T007, T009, T014, T017.
- FR-002: T008, T015, T017, T018.
- FR-003: T005, T006, T009, T024.
- FR-004: T008, T010, T011, T017.
- FR-005: T010, T012, T013, T019.
- FR-006: T012, T014, T019, T021.
- FR-007: T016, T020, T023, T027.

## Final Verification

- [x] All tests pass: `make test`.
- [x] Linter limpio: `make lint`.
- [x] Type check limpio: `make typecheck`.
- [x] Landing build: `cd landing && bun run build`.
- [x] `git diff --check` limpio.
- [x] Cobertura no menor que el gate nativo.
- [x] Cada user story es independientemente funcional.
