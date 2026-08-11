# Tasks — global-llm-provider-order

## Meta

- **Feature:** global-llm-provider-order
- **Author:** Codex
- **Status:** in_progress
- **Linked spec:** `specs/global-llm-provider-order/requirements.md`
- **Linked design:** `specs/global-llm-provider-order/design.md`

## Phase 1: Foundation

- [x] T001 [US4] Confirmar orden global y límite de 2s.
- [x] T002 [US4] Agregar settings y variables en ambos Compose/examples.
- [x] T003 [US4] Actualizar AGENTS, ADR y guía de deploy.

## Phase 2: Provider orchestration

- [x] T004 [US1] Agregar policy de OpenRouter y tools read-only.
- [x] T005 [US1] Preservar history, cultivos, system tip y consulta_tipo.
- [x] T006 [US2] Implementar deadline total y fallback Qwen.
- [x] T007 [US3] Saltar remoto para escrituras y exigir tool válida.
- [x] T008 [US1/US2] Usar el orquestador en demo y pipeline.

## Phase 3: Tests

- [x] T009 [US1] Test remoto primero sin Qwen.
- [x] T010 [US2] Test timeout remoto y fallback local.
- [x] T011 [US3] Test de escritura sin invocación remota.
- [x] T012 [US4] Test Settings y Compose.

## Phase 4: Verify

- [x] T013 Ejecutar `make test`, ruff, mypy, diff check y Compose config.
- [x] T014 Registrar que falta prueba externa hasta configurar API key real.
