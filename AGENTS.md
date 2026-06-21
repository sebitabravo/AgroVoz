# AgroVoz — Asistente de Voz para la Agricultura Familiar Campesina

> Source of truth for AI/project instructions. `CLAUDE.md` points to this file via symlink.

## Project summary

**AgroVoz** es un asistente de IA que responde por voz a través de WhatsApp, diseñado para que pequeños agricultores chilenos accedan a precios agrícolas (ODEPA) y pronósticos climáticos (OpenWeatherMap) sin leer, escribir ni instalar aplicaciones. El productor envía un audio por WhatsApp y recibe una respuesta hablada con datos oficiales en tiempo real.

Problema: más de 205.000 agricultores INDAP pierden 40-60% del precio mayorista por asimetría de información. No tienen acceso a datos de mercado cuando negocian con intermediarios.

Proyecto estudiantil para Desafío Crea INACAP 2026. Etapa actual: IDEA con arquitectura definida. Sin código aún.

## Team

- **Sebastián Bravo** — Líder técnico: backend, LLM, Tool Calling, integración Open-WA, arquitectura
- **Francisco Fernández** — Product Owner: investigación, pitch, enlace con productores en Traiguén
- **Matías Atuán** — Desarrollo: apoyo técnico, testing, documentación, validación de fuentes

## Academic context

- **Institución:** INACAP Temuco, Ingeniería en Informática
- **Competencia:** Desafío Crea INACAP 2026
- **Etapa actual:** IDEA con arquitectura definida (sin código aún)
- **Entregables esperados:** prototipo funcional MVP, pitch, demo en vivo, documentación técnica
- **Piloto de validación:** 3-5 productores reales en Traiguén, 4 semanas

## Main goals

1. **MVP funcional (6 semanas):** pipeline end-to-end de voz en VPS Hetzner CX43
2. **Validación técnica:** precisión de Whisper small en español rural chileno, target WER < 15% en muestra piloto de Traiguén (stretch < 10%, a validar en piloto)
3. **Piloto en Traiguén:** 3-5 productores reales, 4 semanas de uso
4. **Dataset de voz rural chilena:** activo propietario para fine-tuning futuro
5. **Validación institucional:** contacto formal con PRODESAL/INDAP Araucanía

## Hard constraints

- **Stack 100% open-source.** Whisper, LLM, TTS y gateway WhatsApp corren localmente. Sin APIs pagas externas.
- **Sin app nativa.** WhatsApp ES la app. El agricultor no instala nada.
- **Sin IoT/sensores.** Solo el micrófono del teléfono.
- **Sin recomendaciones agronómicas.** El LLM entrega datos de precio y clima, no interpreta.
- **VPS Hetzner CX43** (8 vCPU, 16 GB RAM, 160 GB SSD) — EUR 12,49/mes (~CLP 13.000)
- **Costo operativo:** CLP 100-150 por agricultor/mes (sin costos de API WhatsApp)
- **Latencia:** <15 segundos end-to-end
- **SQLite** (sin servidor DB separado para MVP)
- **Procesamiento síncrono** (sin Celery/Redis para MVP)
- **Sin autenticación de usuarios** en MVP. Número WhatsApp = identidad.
- **Audio temporal:** eliminado del VPS en <24h. Transcripciones anonimizadas.
- **Ley 21.719** (Protección de Datos, dic 2026) — auditoría formal pre-escalamiento.
- **Código comentado en español** (contexto académico INACAP)

## Preferred stack

### Backend
- Python 3.12+, FastAPI 0.115+, Uvicorn 0.34+
- SQLAlchemy 2.0+, SQLite 3.x, Alembic 1.14+
- Pydantic v2, pydantic-settings, httpx 0.28+
- Whisper open-source (modelo `small` o `tiny`)
- LLM cuantizado ≤3B params, 4-bit (Qwen2.5-3B-Instruct Q4_K_M) vía llama-cpp-python
- Piper TTS (voz español `es_ES-carlfm-x_low`)
- ffmpeg (conversión de audio .ogg ↔ .wav)
- pytest + pytest-asyncio + pytest-cov
- ruff (linter/formatter), mypy (strict mode)

### Frontend — Landing
- Astro 5.x (SSG, zero JS por defecto)
- Tailwind CSS 4.x
- System fonts (system-ui, sin Google Fonts)
- Íconos SVG inline (Lucide)
- Hosteable en mismo VPS vía Dokploy o Cloudflare Pages

### Admin Dashboard
- Server-side rendering: Jinja2 + HTMX (parte del backend, sin build step)
- Chart.js desde CDN
- Auth: API key en header `X-Admin-Key`

### Infra
- VPS Hetzner CX43, Ubuntu 24.04 LTS
- Docker Compose (dev y prod)
- Open-WA (gateway WhatsApp self-hosted, gratuito)
- Dokploy (PaaS self-hosted: Traefik + SSL Let's Encrypt automático)
- GitHub Actions (CI/CD)

### APIs externas
- Open-WA (WhatsApp Web protocol, self-hosted en VPS)
- OpenWeatherMap (plan gratuito, 60 calls/min)
- ODEPA (CSV datos abiertos, cron diario 06:00 AM)

## Product scope

**MVP (piloto Traiguén):**
- WhatsApp audio → transcripción → consulta ODEPA/clima → respuesta de voz
- Keyword matching inicial para intents (Tool Calling completo es stretch goal)
- Solo precios de papa (primer producto), expandible a otros
- Solo clima de Traiguén (coordenadas fijas: -38.23, -72.68)
- Sin historial de consultas para el agricultor (solo métricas anonimizadas para el equipo)

**Post-MVP (si hay tiempo en Crea INACAP):**
- Tool Calling completo con whitelist
- Múltiples productos y mercados ODEPA
- Clima por coordenadas dinámicas (One-time location share de WhatsApp)
- Historial simple: "¿cuál fue el precio de la papa la semana pasada?"

**Fuera de scope para MVP:**
- Multi-idioma (solo español chileno)
- App nativa iOS/Android
- Dashboard para agricultores
- Alertas proactivas de precio/clima
- Pagos integrados

## Architecture overview

```
Productor → WhatsApp (audio) → Open-WA → VPS Hetzner
  ┌──────────────────────────────────────────────────┐
  │ Open-WA + FastAPI                                  │
  │  WhatsApp Web ← QR scan (1 vez)                   │
  │  Webhook POST /api/v1/webhook/whatsapp            │
  │  → Open-WA descarga audio .ogg                     │
  │  → ffmpeg: .ogg → .wav 16kHz mono                │
  │  → Whisper small: .wav → texto                    │
  │  → LLM con whitelist de herramientas:             │
  │     ├─ get_price(producto, mercado) → SQLite ODEPA│
  │     └─ get_weather(lat, lon) → OpenWeatherMap API │
  │  → Piper TTS: texto → .wav                        │
  │  → ffmpeg: .wav → .ogg                            │
  │  → Responde vía Open-WA API con audio              │
  └──────────────────────────────────────────────────┘
Productor ← WhatsApp (audio respuesta)
```

Detalle completo en `docs/ARCHITECTURE.md`.

## Project structure

```
AgroVoz/
├── AGENTS.md              ← Source of truth (este archivo)
├── CLAUDE.md → AGENTS.md  ← Symlink
├── Makefile               ← Comandos de desarrollo
├── README.md
├── .env.example
├── .env.production.example
├── .gitignore
├── docker-compose.yml          ← Dev (backend + openwa)
├── docker-compose.prod.yml     ← Prod (backend + openwa), desplegado por Dokploy
├── .github/
│   └── workflows/
│       ├── ci.yml              ← CI: tests (backend + landing)
│       └── pr-check.yml        ← Gate: issue-first + labels en PRs
├── backend/
│   ├── Dockerfile
│   ├── Makefile
│   ├── pyproject.toml
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   ├── alembic.ini
│   ├── migrations/
│   ├── scripts/
│   ├── models/            ← Modelos descargados (en .gitignore)
│   ├── data/              ← SQLite DB y audio temporal (en .gitignore)
│   ├── app/
│   │   ├── main.py
│   │   ├── api/           ← Routers HTTP
│   │   ├── core/          ← Config, DB, seguridad
│   │   ├── services/      ← Lógica de negocio
│   │   ├── models/        ← SQLAlchemy models
│   │   ├── schemas/       ← Pydantic DTOs
│   │   ├── jobs/          ← Cron jobs
│   │   └── admin/         ← Dashboard admin (Jinja2)
│   └── tests/
├── landing/               ← Astro static site
│   ├── astro.config.mjs
│   ├── package.json
│   └── src/
├── docs/
│   ├── ARCHITECTURE.md
│   └── phases/            ← Instrucciones por fase (00–06)
├── scripts/               ← provision-vps.sh, smoke-test.sh
└── skills/                ← Guías de trabajo del equipo (leer ANTES de codear)
    ├── issue-creation/SKILL.md   ← Crear issues (flujo issue-first)
    ├── branch-pr/SKILL.md        ← Branches, PRs, squash
    ├── commit-hygiene/SKILL.md   ← Conventional Commits, scopes
    ├── python-standards/SKILL.md ← Type hints, capas, seguridad
    ├── testing-coverage/SKILL.md ← pytest, regresión, mocks
    └── docs-alignment/SKILL.md   ← Mantener docs sincronizados
```

## Skills del equipo — leer ANTES de actuar

El repo trae **skills**: guías cortas de trabajo en `skills/<tema>/SKILL.md`. No son decorativas ni opcionales. **Antes de hacer una de estas acciones, abrí la skill que corresponde y seguila.** Existen para evitar errores tontos al vibecodear: issues sin formato, branches mal nombradas, commits sucios, código sin types, features sin tests, docs desincronizados.

| Cuando vayas a... | Leé primero | Te asegura |
|---|---|---|
| Crear un issue, reportar bug o pedir feature | `skills/issue-creation/SKILL.md` | Flujo issue-first, 1 issue = 1 objetivo claro |
| Crear branch, abrir PR o preparar review | `skills/branch-pr/SKILL.md` | Branch clara + PR vinculado a issue + squash |
| Crear, revisar o hacer squash de commits | `skills/commit-hygiene/SKILL.md` | Conventional Commits, historial limpio |
| Escribir o revisar código Python en `backend/` | `skills/python-standards/SKILL.md` | Type hints, capas, async, seguridad |
| Escribir feature, arreglar bug o revisar PR | `skills/testing-coverage/SKILL.md` | Cada cambio llega con tests + regresión |
| Tocar arquitectura, stack, constraints o fases | `skills/docs-alignment/SKILL.md` | Docs en sync con el código (fuente: `AGENTS.md` + `docs/`) |

Regla simple: **si tu tarea calza con una fila, esa skill es lectura obligatoria, no sugerencia.**

## Working rules for AI agents

### Reglas de ejecución

1. **Leer antes de editar.** Nunca editar a ciegas. Leer el archivo primero.
2. **Consultar la skill que corresponde antes de actuar** (ver tabla "Skills del equipo" arriba). Issue, branch, commit, código Python, tests y docs tienen su guía en `skills/<tema>/SKILL.md`. No es opcional: abrí la skill y seguila. Evitan errores tontos al vibecodear.
3. **Cambios pequeños y reversibles.** Un cambio por vez. Fácil de revisar.
4. **No inventar arquitectura.** Todo está en `docs/ARCHITECTURE.md`. Si no está ahí, preguntar.
5. **Si algo no está definido, elegir la opción más simple y documentarla** en `docs/ARCHITECTURE.md` (sección Decisiones).
6. **Preferir soluciones prácticas sobre "enterprise-ready".**
7. **No agregar dependencias sin razón clara.** Cada librería nueva = justificación.
8. **Mantener `.env`, secrets, credenciales fuera de git.**
9. **Tratar auth, DB migrations, deploy config, y webhooks como áreas sensibles.**

### Límites de contexto

- **Leer máximo 5 archivos por tarea atómica.**
  - 1 archivo de fase (`docs/phases/XX-*.md`)
  - Hasta 3 archivos de referencia (`AGENTS.md`, `docs/ARCHITECTURE.md`)
  - 1 archivo de código a modificar
- Si necesitai más contexto, **preguntar**, no leer "por si acaso".

### Flujo por fase

```
1. Leer docs/phases/XX-*.md           ← qué hay que hacer
2. Leer archivos de contexto necesarios  ← solo los que la fase pide
3. EJECUTAR (código, config, tests)
4. Validar (tests pasan, linter limpio)
5. Reportar: hecho + tests + next step
```

## Coding conventions

### Backend (Python)
- ruff (lint + format), mypy (strict)
- snake_case funciones/variables, PascalCase clases
- Routers en `app/api/` para HTTP, services en `app/services/` para lógica
- Docstrings en español para funciones públicas
- Type hints en TODAS las funciones (mypy strict)
- Async por defecto (FastAPI + httpx)
- `.env` NUNCA al repo. `.env.example` con placeholders.

### Frontend (Astro)
- Componentes `.astro` con PascalCase
- Tailwind-first. Sin CSS custom a menos que sea inevitable.
- Íconos SVG inline (Lucide), sin dependencia de icon library.
- Imágenes optimizadas (WebP, lazy loading).
- SEO básico en todas las páginas (title, description, og:image).

### Git
- Branches: `main`, `feature/*`, `fix/*`
- Conventional Commits: `feat(scope):`, `fix(scope):`, `refactor(scope):`
- Sin `Co-authored-by`, sin `[AI]`, sin huella de IA.
- `.env`, `data/`, `models/`, `*.db` en `.gitignore`.
- NUNCA `--no-verify`.

### Pull Requests
- **Usar la plantilla.** GitHub carga automáticamente `.github/pull_request_template.md` al abrir un PR. Completar TODAS las secciones (Resumen, Tipo, Issue, Módulos, Checklist, Cómo probar, Riesgos). No borrar headers ni comentarios `<!-- -->`.
- **1 PR = 1 cambio.** Mismo principio que los issues: un objetivo claro por PR. Si el cambio creció, dividir en varios PRs.
- **Commits atómicos, no un solo commit.** La branch puede tener varios commits limpios (1 por cambio lógico). No hay que aplastarlos manualmente: GitHub hace "Squash and merge" al aprobar y `main` recibe 1 solo commit con el mensaje Conventional Commits que elijas. Lo ÚNICO prohibido: commits WIP, `auto-save:`, o basura temporal.
- **NUNCA force-push.** `git push --force-with-lease` y `git push --force` reescriben historia remota compartida, invalidan comentarios de review, y rompen el flujo del equipo. Si necesitás ajustar algo ya pusheado: commit nuevo encima. GitHub lo aplasta todo al mergear.
- **Merge por squash.** Al aprobar, usar "Squash and merge" en GitHub. El `main` queda con 1 commit por PR y mensaje Conventional Commits.

## Useful commands

```bash
# Desarrollo
make up          # Levantar todo con Docker
make down        # Bajar todo
make logs        # Ver logs

# Backend
cd backend && uv run uvicorn app.main:app --reload --port 8000
cd backend && uv run pytest tests/ -v --cov=app
cd backend && uv run ruff check app/
cd backend && uv run mypy app/
cd backend && uv run python scripts/eval_wer.py --model small --samples 10

# Frontend
cd landing && bun run dev
cd landing && bun run build

# Utilidades
make sync-odepa  # Forzar sync de precios ODEPA
make tunnel      # ngrok para exponer webhook local (desarrollo)
make clean       # Limpiar archivos temporales

# Git — revisar commits antes de pushear
git log origin/main..HEAD --oneline    # verificar: commits atómicos, sin WIP ni auto-save
```

## Before closing a task

- Explicar qué cambió y por qué
- Listar archivos creados/modificados
- Verificar que tests pasan: `cd backend && uv run pytest tests/ -v --tb=short`
- Verificar que linter y types están limpios: `cd backend && uv run ruff check app/ && uv run mypy app/`
- Si el cambio tocó Whisper, verificar precisión: `cd backend && uv run python scripts/eval_wer.py --model small --samples 5`
- Si el cambio tocó el pipeline E2E, verificar logs: `docker compose logs backend | rg "Audio transcrito"`
- Listar riesgos o trade-offs
- Listar próximo paso (siguiente fase)

## Fases del proyecto (orden de ejecución)

| # | Fase | Archivo | Días est. |
|---|---|---|---|
| 00 | Inicialización del proyecto | `docs/phases/00-project-init.md` | 2-3 |
| 01 | Backend core (FastAPI + DB + APIs) | `docs/phases/01-backend-core.md` | 4-5 |
| 02 | Pipeline de voz (Whisper + LLM + TTS) | `docs/phases/02-pipeline-voz.md` | 5-6 |
| 03 | Integración Open-WA (WhatsApp webhook self-hosted) | `docs/phases/03-integracion-openwa.md` | 4-5 |
| 04 | Landing page (Astro + Tailwind) | `docs/phases/04-landing-page.md` | 3-4 |
| 05 | Admin dashboard (Jinja2 + HTMX) | `docs/phases/05-admin-dashboard.md` | 3-4 |
| 06 | Deploy y puesta en marcha (VPS + Dokploy + CI/CD) | `docs/phases/06-deploy.md` | 2-3 |

**Total estimado**: 23-30 días de desarrollo (5-6 semanas).
Las fases 04 y 05 pueden ejecutarse en paralelo con 03 si hay más de un desarrollador.
