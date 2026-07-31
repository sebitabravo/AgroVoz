# AgroVoz — Asistente de Voz para la Agricultura Familiar Campesina

> Source of truth for AI/project instructions. `CLAUDE.md` points to this file via symlink.

## Project summary

**AgroVoz** es un asistente de IA que responde por voz a través de WhatsApp, diseñado para que pequeños agricultores chilenos accedan a precios agrícolas (ODEPA) y pronósticos climáticos (OpenMeteo) sin leer, escribir ni instalar aplicaciones. El productor envía un audio por WhatsApp y recibe una respuesta hablada con datos oficiales en tiempo real.

Problema: más de 205.000 agricultores INDAP pierden 40-60% del precio mayorista por asimetría de información. No tienen acceso a datos de mercado cuando negocian con intermediarios.

Nacido como proyecto estudiantil para Desafío Crea INACAP 2026, hoy es un producto en operación: pipeline E2E de voz (Whisper + LLM + TTS con Tool Calling sobre 10 tools), catálogo completo ODEPA (79 productos, 15 mercados), alertas proactivas de precio y clima, landing page (Astro 7 + Tailwind 4) y dashboard admin de 8 vistas (Jinja2 + HTMX + PWA). Backend: 72 módulos Python en `app/`, 1.664 tests recolectados.

## Team

- **Sebastián Bravo** — Líder técnico: backend, LLM, Tool Calling, integración Open-WA, arquitectura
- **Francisco Fernández** — Product Owner: investigación, pitch, enlace con productores en Traiguén
- **Matías Atuán** — Desarrollo: apoyo técnico, testing, documentación, validación de fuentes

## Academic context

- **Institución:** INACAP Temuco, Ingeniería en Informática
- **Competencia:** Desafío Crea INACAP 2026
- **Etapa actual:** producto implementado y desplegado; foco en validación en terreno y endurecimiento operativo
- **Entregables:** producto funcional, pitch, demo en vivo, documentación técnica
- **Piloto de validación:** 3-5 productores reales en Traiguén, 4 semanas

## Main goals

1. **Latencia sostenida < 15 s end-to-end** incluso en hardware degradado (ver "Hard constraints"). Objetivo abierto: el LLM es el cuello de botella bajo CPU limitada.
2. **Validación técnica:** precisión de Whisper small en español rural chileno, target WER < 15% en muestra piloto de Traiguén (stretch < 10%, a validar en piloto)
3. **Piloto en Traiguén:** 3-5 productores reales, 4 semanas de uso
4. **Dataset de voz rural chilena:** activo propietario para fine-tuning futuro
5. **Validación institucional:** contacto formal con PRODESAL/INDAP Araucanía

## Hard constraints

- **Stack 100% open-source.** Whisper, LLM, TTS y gateway WhatsApp corren localmente. Sin APIs pagas externas.
- **WhatsApp es la vía principal y suficiente.** Ningún flujo puede exigir instalar algo: todo lo que el productor necesita tiene que resolverse por WhatsApp. La PWA es un complemento opcional, nunca un requisito.
- **Sin app nativa iOS/Android.** Si hace falta una interfaz instalable, es PWA sobre el mismo backend.
- **Sin IoT/sensores.** Solo el micrófono y la cámara del teléfono. La cámara se usa para identificación visual de cultivos (por foto de WhatsApp o desde la PWA), no para sensores externos.
- **Recomendaciones agronómicas solo por regla citada.** El LLM nunca improvisa un consejo: enruta y verbaliza reglas resueltas de forma determinística desde hechos publicados por INIA/INDAP/ODEPA, y toda respuesta agronómica cita su fuente y su fecha. Si no hay regla con fuente vigente, el sistema dice que no tiene el dato. Precio y clima siguen siendo datos crudos, sin interpretación.
- **VPS Hetzner CX43** (8 vCPU, 16 GB RAM, 160 GB SSD) — EUR 12,49/mes (~CLP 13.000)
- **Debe funcionar en hardware degradado.** El peor caso soportado es 1 vCPU / 6 GB RAM: si el producto no responde ahí, no sirve. Todo cambio de rendimiento se valida contra ese piso, no solo contra el VPS.
- **Costo operativo:** CLP 100-150 por agricultor/mes (sin costos de API WhatsApp)
- **Latencia:** <15 segundos end-to-end
- **SQLite.** Sin servidor de DB separado.
- **Procesamiento síncrono.** Sin Celery/Redis: cada audio se procesa en el request del webhook.
- **Sin autenticación de usuarios.** Número WhatsApp = identidad.
- **Media temporal:** audios e imágenes eliminados del VPS en <24h. Transcripciones minimizadas y seudonimizadas.
- **Ley 21.719** (Protección de Datos, dic 2026) — auditoría formal pre-escalamiento.
- **Código comentado en español** (contexto académico INACAP)

## Preferred stack

### Backend
- Python 3.12+, FastAPI 0.115+, Uvicorn 0.34+
- SQLAlchemy 2.0+, SQLite 3.x, Alembic 1.14+
- Pydantic v2, pydantic-settings, httpx 0.28+
- Whisper open-source (modelo `small` o `tiny`)
- MobileNetV3 ONNX (~15 MB) para clasificación visual de enfermedades/plagas de cultivos (PlantVillage)
- LLM cuantizado ≤3B params, 4-bit (Qwen2.5-3B-Instruct Q4_K_M) vía llama-cpp-python
- Piper TTS (voz español `es_MX-claude-high`)
- ffmpeg (conversión de audio .ogg ↔ .wav)
- pytest + pytest-asyncio + pytest-cov
- ruff (linter/formatter), mypy (strict mode)

### Frontend — Landing
- Astro 7.x (SSG, zero JS por defecto)
- Tailwind CSS 4.x
- System fonts (system-ui, sin Google Fonts)
- Íconos SVG inline (Lucide)
- Hosteable en mismo VPS vía Dokploy o Cloudflare Pages

### Admin Dashboard
- Server-side rendering: Jinja2 + HTMX (parte del backend, sin build step)
- Chart.js servido local desde `app/static/` (la CSP es `script-src 'self'`, no admite CDN)
- Auth: cookie de sesión firmada para el dashboard HTML; API key en header `X-Admin-Key` para las APIs JSON

### Infra
- VPS Hetzner CX43, Ubuntu 24.04 LTS
- Docker Compose (dev y prod)
- Open-WA (gateway WhatsApp self-hosted, gratuito)
- Dokploy (PaaS self-hosted: Traefik + SSL Let's Encrypt automático)
- GitHub Actions (CI/CD)

### APIs externas
- Open-WA (WhatsApp Web protocol, self-hosted en VPS)
- OpenMeteo (gratuito, sin API key, 10.000 req/día)
- ODEPA (CSV datos abiertos, cron diario 06:00 AM)

## Product scope

**Implementado y en operación:**
- WhatsApp audio → transcripción → consulta ODEPA/clima → respuesta de voz
- **WhatsApp texto → misma consulta → respuesta escrita.** El productor no siempre puede mandar audio (lugar ruidoso, reunión, mala señal), así que el texto es una vía de entrada de primera clase. Salta Whisper y Piper: ~100 ms contra ~11 s del audio
- Tool Calling con whitelist estricta de 14 tools: `get_price`, `get_price_history`, `calculate_sale_value`, `calculate_margin`, `get_price_spread`, `get_weather`, `get_pronostico`, `get_clima_historico`, `search_corpus`, `register_expense`, `register_parcela`, `get_parcelas`, `get_regla_agronomica`, `get_link_resumen`. `register_expense` cumple su definición de hecho (#170) pero permanece desactivada por `EXPENSE_TRACKING_ENABLED=false` hasta cerrar la revisión operativa y legal de retención. `register_parcela`/`get_parcelas` (C5) siguen el mismo patrón: tabla propia, consentimiento independiente, TTL y borrado, apagadas por `PARCELA_TRACKING_ENABLED=false`. `get_regla_agronomica` (C1+C2) resuelve el hard constraint "recomendaciones solo por regla citada": corpus de reglas verificadas contra INIA con vigencia explícita, sin persistir nada personal, apagada por `AGRONOMIC_RULES_ENABLED=false` hasta que el equipo valide el corpus y el matching con productores reales. `get_link_resumen` (C3) genera un link firmado y de duración corta al panel PWA del agricultor — sin login ni cuenta, coherente con "sin autenticación de usuarios" — apagada por `FARMER_PANEL_ENABLED=false`. Mientras un gate esté apagado, esa tool ni siquiera se anuncia en el prompt.
- Panel PWA del agricultor (`GET /panel/{token}`, C3): shell offline-first con Service Worker propio (scope `/panel/`), que muestra comuna, cultivos, parcelas y alertas activas del productor — solo lo que ya consintió. Complementa WhatsApp, nunca lo reemplaza ni lo exige. Verificado visualmente con Playwright (golden path, link inválido/vencido, viewport móvil).
- Console web para agrónomos PRODESAL (`GET /agronomo/{token}`, C4): un agrónomo sigue a varios productores a través de un contacto `prodesal_group` (#173) — el equipo genera el link desde `POST /admin/agronomos/links` (header `X-Admin-Key`) y se lo entrega por fuera del sistema, ya que un agrónomo no tiene un WhatsApp propio en AgroVoz para pedirlo por voz como el panel del agricultor. Mismo patrón de link firmado HMAC de duración corta, sin login ni cuenta, con clave de firma dedicada (`agronomist_link_secret`, nunca compartida con `panel_link_secret`). El resumen expone comuna, localidad, cultivos y parcelas de cada productor del grupo — nunca el phone_hash — respetando el `parcela_consent` individual de cada contacto. Apagada por `AGRONOMIST_CONSOLE_ENABLED=false` hasta validar el flujo con PRODESAL.
- Catálogo ODEPA completo: 79 productos, 15 mercados, ~41.000 filas de precios (verificado: 79/79 responden en `get_price`, `get_price_spread`, `calculate_sale_value` y `get_price_history`)
- Clima actual e histórico, por comuna del productor (`user_prefs.comuna`), no coordenadas fijas
- Alertas proactivas de precio y clima (helada, lluvia extrema) con rate limit
- Historial de consultas con opt-in explícito (Ley 21.719) — `consultation_history`
- Máquina de estados de conversación con transiciones y timeout
- Dashboard admin de 8 vistas: dashboard, métricas, piloto, actividad, revisión, ODEPA, alertas, monitor
- Landing page + demo interactiva web

**En desarrollo (post-MVP):**
- WhatsApp imagen → identificación visual de enfermedades/plagas con MobileNetV3 ONNX + respuesta con regla INIA citada. Feature gate: `VISION_ENABLED=false`
- WhatsApp ubicación → clima por coordenadas GPS exactas de la parcela (one-time location share). Antes en backlog, ahora promovido
- Calendario agrícola por comuna/región → ventanas de siembra y cosecha basadas en reglas verificadas INIA con fuente citada
- Derivación a créditos y programas INDAP → información oficial de financiamiento y fomento sin evaluación de elegibilidad
- Registro de gastos por voz → `register_expense` (tabla `expenses` con consentimiento `expense_consent` y TTL 180 días)
- Directorio de cooperativas y servicios agrícolas → consulta de sedes INDAP/PRODESAL por comuna (Open Data datos.gob.cl)
- Reportes PDF de precios y clima enviados por WhatsApp (`sendFile`). Feature gate: `PDF_REPORTS_ENABLED=false`
- Gráficos interactivos de precios ODEPA (Chart.js) en el panel PWA del agricultor
- Cámara en vivo con identificación visual desde el panel PWA (complementa visión por WhatsApp)

**Backlog:**
- Cobertura de mercados fuera del catálogo ODEPA

**Spike técnico, no desplegado (canal IVR de respaldo, #172):** VAD,
streaming fragmentado y barge-in por voz sobre el dialplan local de
Asterisk vía ARI — el productor puede interrumpir la locución hablando
encima, en vez de esperar a que termine. Verificado localmente: la cadena
dialplan → Stasis → ARI → evento recibido responde con eventos reales de
Asterisk (`StasisStart` confirmado). Sin verificar: detección de voz con
audio real (el barge-in con `channel originate` no inyecta energía de voz)
y streaming real de audio (Asterisk reproduce cada fragmento completo, no
hay chunked transfer). Mismo estado que el resto del canal IVR: no se
despliega a PSTN. Detalle completo en `docs/spike-ivr.md`.

**Fuera de scope:**
- Multi-idioma (solo español chileno)
- App nativa iOS/Android
- Pagos integrados
- Planes premium directos al agricultor: el producto es gratis para él
- Servidor de DB separado: SQLite se mantiene
- Manifiesto de plugin público sin autenticación
- Búsqueda de insumos o tiendas cercanas: no hay fuente oficial y obliga a scraping comercial

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
  │  → LLM con whitelist de 10 tools (ej):             │
  │     ├─ get_price(producto, mercado) → SQLite ODEPA│
  │     └─ get_weather(lat, lon) → OpenMeteo API       │
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
│   ├── README.md          ← Índice de toda la documentación
│   ├── ARCHITECTURE.md
│   ├── DEV-GUIDE.md
│   ├── negocio/           ← Plan de negocio segmentado en 11 partes
│   ├── pmbok/             ← Gestión de proyecto para evaluación INACAP
│   ├── piloto/            ← Plan y kit operativo del piloto Traiguén
│   ├── legal/             ← Privacidad y aviso de responsabilidad
│   └── historico/         ← Postulación Crea congelada (NO editar)
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
  - 1 issue o descripción de la tarea
  - Hasta 3 archivos de referencia (`AGENTS.md`, `docs/ARCHITECTURE.md`, la skill que corresponda)
  - 1 archivo de código a modificar
- Si necesitai más contexto, **preguntar**, no leer "por si acaso".

### Flujo de trabajo

```
1. Leer el issue                         ← qué hay que hacer
2. Leer la skill que corresponde         ← cómo se hace acá
3. Leer solo los archivos necesarios
4. EJECUTAR (código, config, tests)
5. Validar (tests pasan, linter y mypy limpios)
6. Reportar: hecho + tests + riesgos + next step
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
- Listar próximo paso

## Fases de construcción (completadas)

Todas las fases del plan original (00–06) fueron implementadas entre mayo y julio 2026.
El detalle de cada fase está en el historial de git y en los issues cerrados del repo.
El trabajo ya no se organiza por fases: es mantención y evolución de un producto en operación.

| # | Fase | Estado |
|---|---|---|
| 00 | Inicialización del proyecto | ✅ Completado |
| 01 | Backend core (FastAPI + DB + APIs) | ✅ Completado |
| 02 | Pipeline de voz (Whisper + LLM + TTS) | ✅ Completado |
| 03 | Integración Open-WA (WhatsApp webhook self-hosted) | ✅ Completado |
| 04 | Landing page (Astro + Tailwind) | ✅ Completado |
| 05 | Admin dashboard (Jinja2 + HTMX) | ✅ Completado |
| 06 | Deploy y puesta en marcha (VPS + Dokploy + CI/CD) | ✅ Completado |

**Próximo hito**: piloto de validación con 3-5 productores en Traiguén (4 semanas).

**Deuda abierta conocida** (detectada en auditoría del 24/07/2026):

| Tema | Estado |
|---|---|
| `register_expense` bloqueado por revisión legal, no por código: tabla `expenses` con TTL por fila, purga programada, consentimiento propio, borrado en revocación, integración con margen y extracción 100% sobre 39 casos | Cerrado técnicamente (30/07/2026) — gate apagado hasta aprobar los 180 días de retención |
| Whisper `small` es el cuello del camino de voz: ~9 s de los ~11 s del pipeline en 1 vCPU (el camino de texto responde en ~100 ms) | Abierto — siguiente objetivo de latencia |
| Consulta que nombra un mercado específico no toma el fast-path y cae al LLM (lento en 1 vCPU) | Abierto |
| `_formatear_pesos` verbaliza centavos: "14.232 coma 14 pesos". El peso chileno no tiene centavos en circulación | Abierto — decisión de redondeo pendiente |
| `success_rate` del panel mide clasificación de intent, no entrega efectiva. Una consulta con timeout de LLM y envío fallido figura como "✓ ok" | Abierto |
| Log dice "Respuesta LLM generada" también cuando respondió el fast-path sin LLM | Abierto — cosmético |
| El demo web llama al backend por mismo origen (`window.location.origin`): solo funciona detrás de reverse proxy. Además `demo_endpoint_enabled=False` por defecto | Abierto |
| `<title>` y `meta description` no se traducen con el toggle ES/EN | Abierto — SEO |
| Toggle de idioma sin `aria-pressed`; drawer mobile cerrado sigue siendo tabbable | Abierto — accesibilidad |
| Textos de la página de alertas sin tildes ("minima", "mas", "maximo") | Abierto — ortografía |
| Datos tabulares del admin renderizados como `div` y no `table` (`/admin/activity`, `/admin/odepa`) | Abierto — accesibilidad |

**Resuelto en la auditoría del 24-26/07/2026:**

| Tema | Fix |
|---|---|
| El sistema **solo aceptaba audio**: el webhook descartaba los mensajes escritos con `reason="mensaje_no_audio"` | `process_text()` + `_is_text_message()`. Mismo pipeline (alertas, resumen, fast-path, tool calling, persistencia) sin Whisper ni TTS: ~100 ms |
| Whisper transcribía mal el vocabulario del dominio: "va a llover mañana" → "vaya chubes mañana", y la consulta quedaba sin clasificar | `initial_prompt` con productos, mercados, comunas y frases típicas + fallback de temperatura explícito. La frase ahora transcribe correcta |
| Timeout del LLM en 60 s: el peor caso era esperar 70 s para recibir "no te entendí" | Bajado a 25 s; el fallback por keywords entrega datos reales de ODEPA en vez de un mensaje genérico |
| Los tests del endpoint webhook empezaron a disparar el pipeline real al soportarse texto (el archivo pasó de 0,3 s a 113 s) | Fixture autouse que aísla los `test_webhook_*` del procesamiento en background |

| Tema | Fix |
|---|---|
| Latencia: 86 s con timeout de LLM a 60 s | 11 s en caliente. Fast-path determinista + cache de prompt KV + subset de tools por intent + `n_threads` sin sobresuscribir |
| `llama-cpp` no cargaba (`undefined symbol` de libstdc++): el LLM llevaba días en modo mock | Preload de libstdc++ con `RTLD_GLOBAL` + catch de `OSError`/`RuntimeError` |
| Schema drift: `migrations/` no montado en dev, 500 en `/admin/users/{hash}` | Volumen `./backend/migrations:/app/migrations` |
| La suite de tests escribía en `data/agrovoz.db`: ~83% de `consultations` era basura | Fixture autouse que aísla `SessionLocal` en todos los tests |
| Piper deletreaba unidades: "eme barra ese" por `m/s`, "barra" en fechas | `normalizar_para_voz()` antes de sintetizar |
| `scikit-learn` faltaba en la imagen: `search_corpus` nunca funcionó y una consulta de clima moría con `ModuleNotFoundError` | Rebuild de la imagen + guard de `ImportError` en `_force_corpus_search`, con el import dentro del branch de keywords |
| El panel (PWA de terreno) scrolleaba en horizontal en teléfono: tablas y grids de columnas fijas empujaban la página | `table { display:block; overflow-x:auto }` en la media query, `.split-grid` con `minmax(0,1fr)`, contenedores scrollables en ODEPA y métricas |
| `/demo` era inalcanzable: ningún enlace del sitio llevaba ahí | CTA "Probá la demo interactiva" en la sección `#demo` del landing |
| 13 anclas muertas en `/demo`: la nav y el footer apuntaban a secciones que solo existen en el index | Anclas root-relative (`/#como`) en `Header.astro` y `Footer.astro` |
| Skip link de accesibilidad roto en `/demo` (apuntaba a un `#top` inexistente) | `id="top"` en el `<main>` de `demo.astro` |
| `/demo` sin tildes ("Proba", "Escribi", "Traiguen", "manana") | Ortografía corregida en toda la página |

**Resuelto en la auditoría de contenido del motor agronómico (30/07/2026):**

Verificación con criterio de dominio (fact-check contra fuentes primarias INIA, no validación profesional de un agrónomo colegiado) sobre `corpus/reglas_agronomicas.yaml` y `corpus/inia_papa.yaml`:

| Tema | Fix |
|---|---|
| `agronomic_rules_service.py` no chequeaba su propio gate: con `AGRONOMIC_RULES_ENABLED=false` igual respondía el diagnóstico completo si la tool llegaba a ejecutarse (solo `_offered_tools` del LLM filtraba el anuncio, no la ejecución contra `WHITELIST_TOOLS`) | Chequeo de gate como primera línea del servicio, mismo patrón fail-closed que `expense_service`/`parcela_service`/`panel_service` |
| Rango de temperatura del tizón tardío: el corpus decía 10-25°C | Corregido a 15-25°C (pico de avance ~21°C), verificado contra `enfermedadespapa.inia.cl` |
| Solo matcheaba el síntoma tardío ("manchas marrones"); el síntoma inicial real (manchas acuosas verde oscuro con halo amarillo pálido en hojas inferiores) no tenía regla — se perdía la ventana de detección temprana, la única donde el fungicida preventivo sirve | Agregados síntomas tempranos al matching + al texto del diagnóstico |
| Ventana de siembra "agosto a noviembre, zona centro-sur" no cubría la plantación temprana real de Araucanía costera (julio-agosto, Carahue/Saavedra, ~10% de la producción regional) — un productor costero preguntando en julio se quedaba sin regla | Regla `papa_epoca_siembra` reescrita con las 3 épocas reales de La Araucanía |
| Rendimiento "25-35 t/ha secano, hasta 50 riego" mezclaba categorías sin fuente | Corregido con cifras trazables: 15-25 t/ha (temprana secano costera), 29,2 t/ha (promedio nacional INE/ODEPA), hasta 80 t/ha con riego tecnificado |
| Fuente citada como "INIA Chile — Ficha técnica: Cultivo de la papa" sin URL: documento irrastreable | `fuente_url` ahora obligatorio en el schema de `reglas_agronomicas.yaml`, cada regla cita su URL real |

Las 4 correcciones de contenido no cambian la decisión del gate: `AGRONOMIC_RULES_ENABLED` sigue en `false` — la condición de reapertura (agrónomo asesor que valide y firme las reglas) sigue sin cumplirse. Ver issues #130, #178, #101 (reabiertas/revisadas 30/07/2026 tras el cambio del hard constraint de "sin recomendaciones" a "solo por regla citada").
