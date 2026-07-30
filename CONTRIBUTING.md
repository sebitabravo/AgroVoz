# Contribuir a AgroVoz

Guía de workflow en GitHub para el equipo (Sebastián, Francisco, Matías).
Las reglas del proyecto (stack, restricciones, convenciones de código) viven en
[`AGENTS.md`](./AGENTS.md) — leerlo primero. Acá cubrimos solo la mecánica de GitHub:
issues, GitHub Project (kanban), branches, pull requests y code review.

> **Fuente de verdad**: `AGENTS.md`. Este archivo describe CÓMO trabajamos en GitHub.
> `docs/ARCHITECTURE.md` describe QUÉ construimos. `docs/DEV-GUIDE.md` describe cómo montar el entorno.

---

## 1. Antes de tocar código

1. Leer [`AGENTS.md`](./AGENTS.md): stack, hard constraints, convenciones.
2. Leer [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md): flujo, ADRs, schema DB.
3. Revisar [`skills/`](./skills): guías de issue-creation, branch-pr, commit-hygiene,
   python-standards, testing-coverage, docs-alignment. Evitan errores tontos.

---

## 2. Issues

### Cómo crearlas

GitHub carga las plantillas automáticamente al abrir un issue:
- **🐛 Bug** → `.github/ISSUE_TEMPLATE/bug_report.yml`
- **✨ Feature** → `.github/ISSUE_TEMPLATE/feature_request.yml`

No se permiten issues en blanco (`blank_issues_enabled: false`). Esto obliga a
completar módulo + ambiente/fase + severidad/prioridad + responsable.

### Campos clave (ya vienen en las plantillas)

| Campo | Bug | Feature | Para qué sirve |
|---|---|---|---|
| Módulo | ✅ | ✅ | Filtrar y enrutar (CODEOWNERS + label `mod:*`) |
| Ambiente | ✅ | — | Dónde se reproduce (dev/VPS/Traiguén) |
| Área | ✅ | ✅ | A qué frente pertenece (`area:*`) |
| Severidad | ✅ | — | Priorizar bugs (Bloqueante → Baja) |
| Prioridad MoSCoW | — | ✅ | Must / Should / Could / Won't |
| Responsable | ✅ | ✅ | Quién lo toma (tentativo) |
| Criterio de aceptación | — | ✅ | Qué = "done" (checkboxes verificables) |

### Labels

Los labels ya están creados en el repo. Si falta alguno, agregalo desde
**Issues → Labels** o con `gh label create`.

Set de labels: `mod:*` (módulo), `area:*` (frente de trabajo), `prio:*` (MoSCoW),
`status:*` (flujo issue-first), `bug`, `enhancement`, `documentation`, `refactor`,
`chore`, `ci`, `security`.

Regla: cada issue lleva **1 label de tipo** + **1 `mod:*`** + **1 `area:*`** + **1 `prio:*`** (si aplica)
+ **1 `status:*`** (gestionado por el tech lead).

> **Sobre las fases 00–06.** Las etiquetas `fase:*` correspondían al plan de construcción original,
> que se completó entre junio y julio de 2026. El trabajo ya no se organiza por fases: es mantención
> y evolución de un producto en operación. Los issues históricos conservan su `fase:*`; los nuevos
> usan `area:*` (piloto, negocio, pmbok, legal, producto, infra).

### Ciclo de vida del issue (issue-first)

```
Created → status:needs-review → status:approved → abrir PR (Closes #N)
                              → status:rejected → cerrado, sin PR
```

El tech lead (Sebastián) aprueba los issues. El workflow `pr-check.yml` **bloquea**
el merge de cualquier PR cuyo issue no tenga `status:approved`, que no tenga
`Closes #N`, o que no tenga label `mod:*`.

### Seguridad

⚠️ Si el issue es de **seguridad** (secrets, auth, HMAC del webhook, ban de número,
fuga de datos de productores) → **no abras issue pública**. Usá
[Security Advisories](https://github.com/sebitabravo/AgroVoz/security/advisories/new)
o avisá por interno.

---

## 3. GitHub Project (Kanban)

Crear un **Project (v2)** en GitHub: `Projects → New project → Board`.

### Columnas (Status)

```
Backlog  →  Todo  →  In Progress  →  In Review  →  Done
```

### Campos personalizados (Settings → Custom fields)

Mapean 1:1 con los dropdowns de las plantillas, así no hay que re-escribir datos:

| Campo | Tipo | Valores |
|---|---|---|
| Status | Single select | Backlog, Todo, In Progress, In Review, Done |
| Módulo | Single select | backend, voz, openwa, landing, admin, infra, datos |
| Área | Single select | piloto, negocio, pmbok, legal, producto, infra |
| Prioridad | Single select | Must, Should, Could, Won't |
| Severidad | Single select | Bloqueante, Alta, Media, Baja |
| Responsable | Iteration/Assignee | Sebastián, Francisco, Matías |

### Vistas recomendadas

- **Board por Status** (kanban general): agrupado por Status, ordenado por Prioridad.
- **Board por Área**: agrupado por Área → ve el avance de cada frente (piloto, negocio, PMBOK, legal).
- **Board por Responsable**: agrupado por Responsable → quién tiene qué.

### Automatización (Settings → Workflows / Automation)

- Issue abierta → Status = `Todo`.
- PR abierta que cierra la issue (`Closes #N`) → Status = `In Review`.
- PR mergeada → Issue cerrada → Status = `Done`.
- Issue asignada a alguien → aparece en su vista.

---

## 4. Branches y desarrollo

### Branches

- `main` — siempre verde (CI pasa, deployable).
- `feature/<slug>` — nueva funcionalidad.
- `fix/<slug>` — corrección de bug.
- `refactor/<slug>` — refactor sin cambio funcional.

Nombrado del slug: kebab-case, referenciando el issue. Ej: `feature/011-endpoint-precios-papa`.

### Flujo

```bash
git checkout main && git pull
git checkout -b feature/011-endpoint-precios-papa
# ... programar (ver sección 6) ...
git add -A && git commit -m "feat(backend): endpoint de precios de papa"
# Antes de abrir PR: squash a 1 commit limpio (ver sección 5)
git push -u origin feature/011-endpoint-precios-papa
# Abrir PR en GitHub (carga la plantilla automáticamente)
```

---

## 5. Pull Requests

### Plantilla

GitHub carga `.github/pull_request_template.md` automáticamente al abrir el PR.
Completar **todas** las secciones: Resumen, Tipo, Issue (`Closes #N`), Módulos,
Checklist, Cómo probar, Riesgos.

### Squash obligatorio

La branch llega al PR con **1 commit limpio**. Si tenés varios commits locales:

```bash
git rebase -i origin/main              # marcar todos menos el primero como `squash`
git log origin/main..HEAD --oneline    # verificar: 1 línea
git push --force-with-lease            # si ya estaba pusheada
```

Al aprobar → **"Squash and merge"** en GitHub. `main` queda con 1 commit por PR,
mensaje Conventional Commits (`feat(backend): ...`).

### Antes de pushear

```bash
git log origin/main..HEAD --oneline     # ver qué commits vas a pushear
```

### Gate automático (pr-check.yml)

El workflow `.github/workflows/pr-check.yml` bloquea el merge si el PR no cumple:

1. **Referencia un issue**: `Closes #N` (o Fixes/Resolves) en el cuerpo.
2. **El issue está aprobado**: tiene label `status:approved`.
3. **Tiene módulo**: al menos un label `mod:*`.

Marcar los 3 checks de `pr-check` como **required** en branch protection
(Settings → Branches → main → Require status checks).

---

## 6. Programar (comandos)

Todo vía el `Makefile` de la raíz. `make help` lista los 28 targets.

```bash
make setup-dev            # entorno de desarrollo (deps, env, modelos)
make up                   # levantar todo (backend + openwa) con Docker
make dev-backend          # backend en hot-reload (sin Docker)
make test-backend         # pytest + coverage
make lint                 # ruff
make typecheck            # mypy
make sync-odepa           # forzar sync de precios ODEPA
make db-migrate           # generar migración Alembic
```

Backend directo:

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8000
cd backend && uv run pytest tests/ -v --cov=app
cd backend && uv run ruff check app/ && uv run mypy app/
```

### Definition of Done (por issue)

Un issue se considera done cuando:
1. Código sigue `AGENTS.md` (convenciones, type hints, async, comentarios en español).
2. `make lint && make typecheck` pasan limpio.
3. `make test-backend` pasa (cobertura no baja).
4. Bug fix tiene test de regresión que falla sin el fix.
5. No hay huella de IA en commits.
6. PR aprobado por el codeowner del módulo (CODEOWNERS) + mergeado con squash.

---

## 7. Code Review

### Quién revisa

GitHub usa [`.github/CODEOWNERS`](./.github/CODEOWNERS) para auto-requestar el
reviewer según la ruta tocada:

| Ruta | Codeowner |
|---|---|
| `/backend/` (api, services, core, models) | Sebastián |
| `/landing/` | Francisco, Matías |
| `/backend/app/admin/` | Sebastián, Francisco |
| `/.github/`, `/scripts/`, compose | Sebastián |
| `/docs/` | Matías |
| Default (todo lo demás) | Sebastián (tech lead) |

> ⚠️ Reemplazar los handles en `CODEOWNERS` por los usernames reales de GitHub.

### Qué mirar al revisar

- **Acorde al issue**: el PR resuelve lo que dice el issue, ni más ni menos.
- **Hard constraints** (no negociable, ver `AGENTS.md`):
  - Sin APIs pagas externas.
  - Input validado en backend; output saneado (XSS).
  - Webhook: HMAC + rate limit + hash de número.
  - Sin `eval`/`exec`/`system` con strings dinámicos.
  - Sin secrets en código (`.env`, vault).
  - Audio temporal <24h; transcripciones minimizadas y seudonimizadas (Ley 21.719).
- **Calidad**: type hints, async, DRY a escala módulo, funciones cortas, early returns.
- **Tests**: cubren happy path + edge cases + errores. Bug fix con test de regresión.
- **Sin drive-by refactors**: el PR toca solo lo que el issue pide.

### Severidad de hallazgos

| Nivel | Cuándo | Acción |
|---|---|---|
| 🔴 Crítico | Secret expuesto, SQLi, XSS, bypass de auth | Bloquea el merge |
| 🟠 Alto | Bug funcional, validación faltante, test ausente | Solicitar cambios |
| 🟡 Medio | Code smell, dependencia vulnerable | Resolver esta iteración |
| 🟢 Bajo | Estilo,命名, nitpick | Sugerencia, no bloqueante |

### Veredicto

- **APPROVE** — listo para merge (squash).
- **REQUEST_CHANGES** — hay hallazgos Crítico/Alto que resolver.
- **COMMENT** — dudas o sugerencias no bloqueantes.

---

## 8. Convenciones de commit (Conventional Commits)

```
feat(backend): endpoint de precios de papa
fix(openwa): valida HMAC antes de procesar webhook
refactor(voz): extrae conversión de audio a servicio
docs(arquitectura): agrega ADR para Open-WA
test(admin): regression test de métricas ODEPA
chore(ci): actualiza versión de uv en workflow
```

- Sin `Co-authored-by`, sin `[AI]`, sin huella de IA.
- Asunto en minúscula, sin punto final, ≤50 chars.
- NUNCA `--no-verify`. Si un hook bloquea, se arregla el problema.
