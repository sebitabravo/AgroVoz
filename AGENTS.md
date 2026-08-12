# AgroVoz — Asistente de Voz para la Agricultura Familiar Campesina

> Source of truth for AI agents. `CLAUDE.md` → symlink to this file.
> Arquitectura completa: @docs/ARCHITECTURE.md
> Índice de docs: @docs/README.md

## Identidad

**AgroVoz** responde por voz en WhatsApp a pequeños agricultores chilenos.
Precios agrícolas (ODEPA: 79 productos, 15 mercados) y pronósticos climáticos (OpenMeteo).
Pipeline E2E: Whisper small → LLM Qwen2.5-3B Q4_K_M (llama-cpp) → Piper TTS.
Backend: FastAPI + SQLite + SQLAlchemy 2.0. Infra: Docker Compose.
Sin APIs pagas obligatorias. OpenRouter puede ser el proveedor primario global
cuando existe una key; Qwen local queda como fallback. Sin app nativa. WhatsApp
es la vía principal, la PWA es complemento opcional.

**Stack:** Python 3.12+, FastAPI 0.115+, SQLAlchemy 2.0+, SQLite, Alembic.
Frontend: Astro 7 + Tailwind 4 (landing), Jinja2 + HTMX (admin dashboard).
Linter: ruff. Type checker: mypy strict. Tests: pytest + pytest-asyncio + pytest-cov.

## Hard constraints (NO negociables)

- **Fallback local obligatorio.** OpenRouter puede atender primero las consultas de lectura con `LLM_PRIMARY_PROVIDER=openrouter`; si falla en 2 segundos totales, Qwen local responde. Las tools con efectos persistentes van al local para evitar duplicados. Whisper, Qwen, Piper y WhatsApp gateway siguen disponibles localmente.
- **WhatsApp es la vía principal y suficiente.** Ningún flujo puede exigir instalar algo. La PWA es un complemento opcional, nunca un requisito.
- **Sin app nativa iOS/Android.** Si hace falta una interfaz instalable, es PWA sobre el mismo backend.
- **Sin IoT/sensores.** Solo micrófono y cámara del teléfono.
- **Recomendaciones agronómicas solo por regla citada.** El LLM nunca improvisa un consejo: enruta y verbaliza reglas resueltas de forma determinística desde hechos publicados por INIA/INDAP/ODEPA, y toda respuesta agronómica cita su fuente y su fecha. Si no hay regla con fuente vigente, el sistema dice que no tiene el dato. Precio y clima siguen siendo datos crudos, sin interpretación.
- **Debe funcionar en hardware degradado.** Mínimo 1 vCPU / 4 GB RAM. Si el producto no responde con esos recursos, no sirve. Todo cambio de rendimiento se valida contra ese piso.
- **Latencia < 15 s end-to-end.**
- **SQLite.** Sin servidor de DB separado.
- **Procesamiento síncrono.** Sin Celery/Redis: cada audio se procesa en el request del webhook.
- **Sin autenticación de usuarios.** Número WhatsApp = identidad.
- **Media temporal:** audios e imágenes eliminados del VPS en < 24h. Transcripciones minimizadas y seudonimizadas.
- **Ley 21.719** (Protección de Datos, dic 2026) — auditoría formal pre-escalamiento.
- **Código comentado en español** (contexto académico INACAP).

## Comandos esenciales

```bash
# Tests, lint, types
cd backend && uv run pytest tests/ -v --cov=app --tb=short
cd backend && uv run ruff check app/
cd backend && uv run mypy app/

# Desarrollo
make up                    # Docker dev
make down
cd backend && uv run uvicorn app.main:app --reload --port 8000
cd landing && bun run dev

# Utilidades
make sync-odepa            # Forzar sync ODEPA
make clean                 # Limpiar archivos temporales

# Git — siempre revisar antes de pushear
git log origin/main..HEAD --oneline
```

## Skills del equipo — leer ANTES de actuar

Skills en `skills/<tema>/SKILL.md`. Cada una es **lectura obligatoria** cuando tu tarea calza con su fila. No son sugerencias.

| Acción | Skill |
|---|---|
| Crear issue, bug o feature | `skills/issue-creation/SKILL.md` |
| Crear branch, PR o squash | `skills/branch-pr/SKILL.md` |
| Hacer o revisar commits | `skills/commit-hygiene/SKILL.md` |
| Escribir código Python en `backend/` | `skills/python-standards/SKILL.md` |
| Escribir feature, fix o revisar PR | `skills/testing-coverage/SKILL.md` |
| Tocar arquitectura, stack o constraints | `skills/docs-alignment/SKILL.md` |

## Reglas de ejecución del agente

1. **Leer antes de editar.** Nunca editar a ciegas. Leer el archivo primero.
2. **Consultar la skill que corresponde.** Ver tabla arriba. Si tu tarea calza, leé la skill. No es opcional.
3. **Cambios pequeños y reversibles.** Un cambio por vez. Fácil de revisar.
4. **No inventar arquitectura.** Todo está en `docs/ARCHITECTURE.md`. Si no está ahí, preguntar.
5. **Opción más simple.** Si algo no está definido, elegir la más simple y documentarla en `docs/ARCHITECTURE.md`.
6. **No agregar dependencias sin razón clara.** Cada librería nueva = justificación.
7. **`.env`, secrets, credenciales fuera de git.**
8. **Auth, DB migrations, deploy config, webhooks son áreas sensibles.**

### Contexto: leer solo lo necesario

- Máximo 5 archivos por tarea atómica: 1 issue + 3 referencia + 1 a modificar.
- Si necesitai más contexto, **preguntar**, no leer "por si acaso".

### Flujo de trabajo

```
1. Leer el issue           ← qué hay que hacer
2. Leer la skill           ← cómo se hace acá
3. Leer archivos           ← solo los necesarios
4. EJECUTAR                ← código, config, tests
5. Validar                 ← tests, linter, mypy limpios
6. Reportar                ← hecho + tests + riesgos + next step
```

## Convenciones

### Python (backend/)
- ruff + mypy strict. Type hints en TODAS las funciones.
- snake_case funciones/variables, PascalCase clases.
- Routers en `app/api/`, services en `app/services/`.
- Docstrings en español. Async por defecto.
- `.env` NUNCA al repo. `.env.example` con placeholders.

### Astro (landing/)
- Tailwind-first. Sin CSS custom.
- Íconos SVG inline (Lucide). Imágenes WebP con lazy loading.

### Git
- Branches: `main`, `feature/*`, `fix/*`
- Conventional Commits: `feat(scope):`, `fix(scope):`, `refactor(scope):`
- Sin `Co-authored-by`, sin `[AI]`, sin huella de IA.
- `.env`, `data/`, `models/`, `*.db` en `.gitignore`.
- **NUNCA `--no-verify`.**
- **NUNCA force-push.** Si algo ya fue pusheado, commit nuevo encima.

## Al cerrar una tarea

- Explicar qué cambió y por qué. Listar archivos creados/modificados.
- Verificar tests: `cd backend && uv run pytest tests/ -v --tb=short`
- Verificar lint + types: `cd backend && uv run ruff check app/ && uv run mypy app/`
- Si tocó Whisper: `cd backend && uv run python scripts/eval_wer.py --model small --samples 5`
- Listar riesgos/trade-offs y próximo paso.
