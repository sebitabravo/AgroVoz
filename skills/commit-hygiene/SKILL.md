# Skill: agrovoz-commit-hygiene

## Propósito
Forzar Conventional Commits e historial limpio y legible.

## Cuándo usarlo
Al crear, revisar o hacer squash de commits.

## Formato

```
type(scope): descripcion
```

Regex de validación:
```
^(feat|fix|chore|docs|style|refactor|perf|test|ci|build|revert)(\([a-z0-9._-]+\))?!?: .+
```

## Types válidos

| Type | Uso |
|---|---|
| feat | Nueva feature o comportamiento |
| fix | Bug fix |
| docs | Solo documentación |
| chore | Mantenimiento, deps, tooling |
| refactor | Reestructuración sin cambio de comportamiento |
| test | Agregar o arreglar tests |
| ci | Cambios en GitHub Actions |
| style | Formato, whitespace, sin lógica |
| perf | Mejora de performance |
| build | Sistema de build |
| revert | Revertir commit previo |

## Scopes válidos para AgroVoz

| Scope | Cubre |
|---|---|
| backend | `app/api/`, `app/core/`, `app/models/`, `app/schemas/`, FastAPI |
| voz | `app/services/` Whisper / LLM / TTS, pipeline |
| openwa | `app/services/` Open-WA, webhook, HMAC |
| landing | `landing/` (Astro) |
| admin | `app/admin/` (Jinja2 + HTMX) |
| datos | `app/jobs/` ODEPA sync, OpenMeteo |
| infra | Docker, Dokploy, VPS, scripts/ |
| ci | `.github/workflows/` |
| config | `.env`, `pyproject.toml`, Makefile |
| docs | `docs/`, AGENTS.md, CONTRIBUTING.md |

## Reglas críticas

- NUNCA `Co-Authored-By` ni atribución de IA (hooks bloquean el commit).
- Un cambio lógico por commit. No "add X and fix Y and update docs".
- Modo imperativo: "add" no "added", "fix" no "fixed".
- Referenciar issue cuando aplique: `feat(backend): endpoint de precios (#12)`.
- Subject ≤ 72 caracteres.
- Si necesita explicación → body separado por línea en blanco.
- NUNCA `--no-verify`. Si un hook bloquea, se arregla el problema.

## Ejemplos

```
feat(backend): endpoint GET /precios/{producto}
fix(openwa): valida HMAC antes de procesar webhook
fix(voz): maneja audio vacio en whisper
test(datos): regression test de cron odepa
refactor(openwa): extrae cliente httpx a servicio
ci: agrega pr-check de issue-first
docs(arquitectura): agrega ADR para Open-WA
chore: actualiza fastapi a 0.115.4
```

## Anti-patrones

```
# MAL — vago
update stuff

# MAL — atribución IA
feat: add feature

Co-Authored-By: Claude <claude@anthropic.com>

# MAL — múltiples concerns
feat(backend): add endpoint and fix webhook and update readme

# MAL — pasado
fix(openwa): validó el hmac
```
