# Instrucciones para GitHub Copilot — AgroVoz

> Copilot lee este archivo automáticamente como contexto del repo.
> NO duplica la documentación: la fuente de verdad es `AGENTS.md`, `docs/ARCHITECTURE.md`
> y las guías en `skills/`. Esto es solo el resumen que Copilot necesita para no equivocarse.

## Qué es AgroVoz

Asistente de voz por WhatsApp para pequeños agricultores chilenos. El productor manda
un audio, se transcribe (Whisper), un LLM con whitelist de herramientas consulta precios
(ODEPA) y clima (OpenWeatherMap), y se responde con audio (Piper TTS). Proyecto académico
INACAP, etapa MVP para el piloto en Traiguén.

## Antes de sugerir código

1. **Lee la guía del tema en `skills/`** — `python-standards`, `testing-coverage`,
   `commit-hygiene`, `branch-pr`, `issue-creation`, `docs-alignment`. Ahí está el detalle.
2. **No inventes arquitectura.** Si no está en `docs/ARCHITECTURE.md`, no lo asumas.

## Idioma

- **Comentarios, docstrings y docs: en español** (contexto académico).
- **Nombres de código (variables, funciones, clases, archivos): en inglés.**
- Docstrings en funciones públicas, en español.

## Convenciones de código (backend Python)

- **Python 3.12+, FastAPI.** Type hints en TODAS las funciones (`mypy --strict`).
- **Async por defecto** (FastAPI + `httpx`). Nada de `requests` síncrono.
- **Separación de capas estricta:** `api/` (routers HTTP) → `services/` (lógica de
  negocio) → clientes externos. Los routers NO llaman APIs externas directo: pasan por
  un service. NO metas lógica de negocio en un router.
- Funciones cortas, una responsabilidad. Early returns sobre `if/else` anidado.
- Menos de 4 parámetros por función; si son más, usa un DTO Pydantic.
- `snake_case` para funciones/variables, `PascalCase` para clases.
- Pydantic v2 para schemas, SQLAlchemy 2.0 para modelos, Alembic para migraciones.
- Linter/formatter: `ruff`. Tests: `pytest` + `pytest-asyncio`.

## Seguridad (prioridad en code review)

- **NUNCA hardcodees secretos** (tokens, API keys, credenciales). Todo en `.env`,
  documentado en `.env.example`. El `.env` jamás va al repo.
- **Valida SIEMPRE el input del webhook** en el backend (no confíes en el origen).
- **Webhook WhatsApp/Open-WA:** validar HMAC, rate limit, y hashear el número de
  teléfono (nunca guardarlo en claro — Ley 21.719).
- **SQL parametrizado** siempre. Nunca concatenes input de usuario en una query.
- **Nunca** `eval()`, `exec()`, `Function()`, `os.system()` con strings dinámicos.
  Para procesos externos (ffmpeg), usa `subprocess` con lista de args, sin `shell=True`.
- **Maneja errores explícitamente** en llamadas a APIs externas (ODEPA, OpenWeatherMap):
  timeout, status != 200, respuesta vacía. No asumas que siempre responden bien.
- **Audio temporal:** se elimina del VPS en <24h. Transcripciones anonimizadas.

## Errores comunes a EVITAR (lee bien)

- ❌ **NO uses Twilio.** La mensajería es **Open-WA** (self-hosted, gratuito). Cualquier
  referencia a Twilio en docs viejos está obsoleta.
- ❌ **NO uses APIs pagas externas.** Whisper, LLM, TTS y el gateway WhatsApp corren
  localmente en el VPS. Constraint duro del proyecto.
- ❌ **NO inventes endpoints de ODEPA u OpenWeatherMap.** Si no sabés la URL o el formato
  exacto, deja `# TODO: verificar endpoint real` en vez de inventar.
- ❌ **NO hagas recomendaciones agronómicas.** El LLM entrega datos de precio y clima,
  no interpreta ni aconseja qué sembrar.
- ❌ **NO rompas la separación de capas** (router → service → cliente externo).
- ❌ **NO agregues dependencias** sin justificación clara. Preferí la stdlib.

## Flujo de trabajo

1. **Issue primero.** Todo cambio nace de un issue aprobado (`status:approved`).
2. **Branch** desde `main`: `feat/...`, `fix/...`, `chore/...` (ver `skills/branch-pr`).
3. **Commits** Conventional Commits, sin huella de IA, sin `Co-authored-by`.
4. **PR** usando la plantilla, con `Closes #N`, en **1 commit squash**.
5. El workflow `pr-check.yml` bloquea el merge si falta el issue aprobado o el label `mod:*`.
