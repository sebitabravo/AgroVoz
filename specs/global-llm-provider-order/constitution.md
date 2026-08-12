# Constitution — global-llm-provider-order

## Meta

- **Project:** AgroVoz
- **Version:** 1.0.0
- **Ratified:** 2026-08-11
- **Source:** `AGENTS.md` y `docs/ARCHITECTURE.md`

## Core Principles

1. **Proveedor remoto primero, respaldo local:** con
   `LLM_PRIMARY_PROVIDER=openrouter`, las consultas de lectura intentan
   OpenRouter primero y Qwen local queda como fallback; Whisper, Piper,
   WhatsApp gateway y las operaciones con efectos persistentes permanecen
   locales. `LLM_PRIMARY_PROVIDER=local` fuerza solo Qwen como rollback.
2. **Respuestas grounded:** la rama remota reutiliza el prompt y las tools
   allowlisted existentes; no inventa precios, clima ni reglas.
3. **Fail-safe y sin duplicados:** ausencia, timeout o error de OpenRouter
   deriva a Qwen local dentro del pipeline; las operaciones mutables no se
   envían al remoto para evitar efectos laterales duplicados.
4. **Secrets fuera de git:** la API key llega por `.env` local o Secrets UI.
5. **Evidencia antes de cierre:** `make test`, ruff, mypy, diff check y Compose
   config deben quedar verificables.

## Additional Constraints

- Performance: fast-path determinístico antes de cualquier red; el intento
  remoto primario tiene un deadline total de 2 segundos por defecto y la
  request HTTP conserva un timeout operativo de 8 segundos para otros usos.
- Privacy: una consulta solo sale del VPS cuando el operador opta por OpenRouter;
  la demo no persiste el contenido localmente.
- Operational: el rate limit existente de demo (5/min/IP) sigue vigente.

## Quality Gates

1. `make test`
2. `cd backend && uv run ruff check app/`
3. `cd backend && uv run mypy app/`
4. `git diff --check`
5. `docker compose -f docker-compose.yml config --quiet` y equivalente prod
