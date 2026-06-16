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

Institución: INACAP Temuco, Ingeniería en Informática.

## Main goals

1. **MVP funcional (6 semanas):** pipeline end-to-end de voz en VPS Hetzner CX43
2. **Validación técnica:** precisión Whisper en español rural chileno (métrica WER)
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
- Hosteable en mismo VPS vía nginx o Cloudflare Pages

### Admin Dashboard
- Server-side rendering: Jinja2 + HTMX (parte del backend, sin build step)
- Chart.js desde CDN
- Auth: API key en header `X-Admin-Key`

### Infra
- VPS Hetzner CX43, Ubuntu 24.04 LTS
- Docker Compose (dev y prod)
- Open-WA (gateway WhatsApp self-hosted, gratuito)
- nginx + Let's Encrypt
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
├── DESIGN.md              ← Sistema de diseño visual
├── Makefile               ← Comandos de desarrollo
├── README.md
├── .env.example
├── .env.production.example
├── .gitignore
├── docker-compose.yml          ← Dev (backend + openwa)
├── docker-compose.prod.yml     ← Prod (backend + openwa + nginx + certbot)
├── .github/
│   └── workflows/
│       └── deploy.yml          ← CI/CD
├── nginx/
│   ├── nginx.dev.conf          ← Reverse proxy dev (backend + openwa)
│   └── nginx.prod.conf         ← Reverse proxy producción + SSL
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
├── scripts/               ← provision-vps.sh, deploy.sh, smoke-test.sh, setup-labels.sh
└── skills/                ← Guías de trabajo del equipo (leer ANTES de codear)
    ├── issue-creation/SKILL.md   ← Crear issues (flujo issue-first)
    ├── branch-pr/SKILL.md        ← Branches, PRs, squash
    ├── commit-hygiene/SKILL.md   ← Conventional Commits, scopes
    ├── python-standards/SKILL.md ← Type hints, capas, seguridad
    ├── testing-coverage/SKILL.md ← pytest, regresión, mocks
    └── docs-alignment/SKILL.md   ← Mantener docs sincronizados
```

## Working rules for AI agents

### Reglas de ejecución

1. **Leer antes de editar.** Nunca editar a ciegas. Leer el archivo primero.
2. **Consultar `skills/` antes de codear.** Issue, branch, commit, código Python, tests y docs tienen su guía en `skills/<tema>/SKILL.md`. Evitan errores tontos al vibecodear.
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
- **Squash obligatorio.** La branch debe llegar al PR con **1 commit limpio**. Si tenés varios commits locales, hacé squash ANTES de abrir o pushear:
  ```bash
  git rebase -i origin/main              # marcar todos menos el primero como `squash`
  git log origin/main..HEAD --oneline    # verificar: debe quedar 1 línea
  ```
- **Merge por squash.** Al aprobar, usar "Squash and merge" en GitHub. El `main` queda con 1 commit por PR y mensaje Conventional Commits.
- **Antes de pushear:** revisar `git log origin/main..HEAD --oneline`. Si hay más de 1 commit, squash primero.

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

# Frontend
cd landing && bun run dev
cd landing && bun run build

# Utilidades
make sync-odepa  # Forzar sync de precios ODEPA
make tunnel      # ngrok para exponer webhook local (desarrollo)
make clean       # Limpiar archivos temporales

# Git — squash de branch antes de PR (1 commit limpio)
git rebase -i origin/main              # marcar todos menos el primero como `squash`
git log origin/main..HEAD --oneline    # verificar: 1 línea
git push --force-with-lease            # reescribir branch remota ya pusheada
```

## Before closing a task

- Explicar qué cambió y por qué
- Listar archivos creados/modificados
- Verificar que tests pasan: `cd backend && uv run pytest tests/ -v`
- Verificar que linter está limpio: `cd backend && uv run ruff check app/`
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
| 06 | Deploy y puesta en marcha (VPS + nginx + CI/CD) | `docs/phases/06-deploy.md` | 2-3 |

**Total estimado**: 23-30 días de desarrollo (5-6 semanas).
Las fases 04 y 05 pueden ejecutarse en paralelo con 03 si hay más de un desarrollador.
