# Apply Progress — global-llm-provider-order

## Meta

- **Feature:** global-llm-provider-order
- **Linked tasks:** `specs/global-llm-provider-order/tasks.md`
- **Batches completed:** 3

## Batch Log

### Batch 1 — 2026-08-11

- Decisión confirmada: OpenRouter primero, Qwen después.
- Se identificó que el timeout HTTP no limita el loop completo.
- Se identificaron tools mutables que no pueden ejecutarse en el intento remoto primario.

### Batch 2 — 2026-08-11

**Files modified:**
- `backend/app/core/config.py` — provider global y deadline total.
- `backend/app/services/llm_service.py` — policy read-only, contexto y orquestador.
- `backend/app/services/pipeline_service.py` — orden global y fallback keyword.
- `backend/app/services/demo_service.py` — mismo orquestador global.
- `backend/tests/test_llm_service.py`, `test_pipeline_service.py`, `test_demo_endpoint.py` — regresiones.
- Compose/examples/docs/AGENTS — configuración y operación.

**Evidence parcial:**
- Tests focalizados: 283 passed.
- Ruff y mypy: passed.

### Batch 3 — 2026-08-11

**Corrección adicional:**
- `LLM_PRIMARY_PROVIDER=local` ahora fuerza solo Qwen y no vuelve a enviar la
  consulta a OpenRouter si Qwen falla.
- El detector de intención mutable se amplió para cubrir guardar, anotar,
  agregar, crear y modificar, además de gastos, parcelas y reportes.
- Se actualizó la constitución SDD y la documentación de rollback.

**Evidence final:**
- `make test`: 2209 passed, 3 skipped, 78 warnings, cobertura 86.84%.
- `ruff check app/` y tests afectados: passed.
- `mypy app/`: 97 archivos, passed.
- `git diff --check`: passed.
- Compose dev/prod `config --quiet`: passed con variables productivas
  redacted.

## Current State

### Completed
- [x] Provider remoto primero cuando está configurado.
- [x] Deadline total configurable de 2.0s.
- [x] Qwen como segundo proveedor.
- [x] Escrituras protegidas contra duplicación.
- [x] Demo y WhatsApp usan el mismo orden.

### Pending
- [x] Suite completa `make test` y Compose config después de los últimos cambios.
- [ ] Prueba real contra OpenRouter con API key y autorización.

## Implementation Notes

- Sin API key, OpenRouter se salta y Qwen funciona directamente.
- `LLM_PRIMARY_PROVIDER=local` es rollback.
- Fast-path, Whisper y Piper siguen locales.
