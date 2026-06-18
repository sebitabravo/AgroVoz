# Skill: agrovoz-branch-pr

## Propósito
Estandarizar creación de branches y PRs. Branch clara + PR vinculado a issue aprobado.

## Cuándo usarlo
Al crear branch, abrir PR o preparar cambios para review.

## Nombre de branch

Formato: `type/descripcion` — validado por convención (regex):

```
^(feat|fix|chore|docs|style|refactor|perf|test|ci|build|revert)\/[a-z0-9._-]+$
```

| Type | Ejemplo |
|---|---|
| feat | `feat/endpoint-precios-papa` |
| fix | `fix/validacion-hmac-webhook` |
| docs | `docs/migrar-twilio-openwa` |
| chore | `chore/actualizar-deps-backend` |
| refactor | `refactor/servicio-openwa` |
| test | `test/regresion-cron-odepa` |
| ci | `ci/agregar-pr-check` |
| perf | `perf/cache-precios` |

**Reglas**: descripción en minúsculas, solo `a-z`, `0-9`, `.`, `_`, `-`. Referenciar issue: `feat/011-endpoint-precios-papa`.

## Título de PR (Conventional Commits)

```
^(feat|fix|chore|docs|style|refactor|perf|test|ci|build|revert)(\([a-z0-9._-]+\))?!?: .+
```

Scopes válidos: `backend`, `voz`, `openwa`, `landing`, `admin`, `infra`, `datos`, `docs`, `ci`, `config`.

Ejemplos:
- `feat(backend): endpoint de precios de papa`
- `fix(openwa): valida HMAC antes de procesar webhook`
- `fix!: cambia formato de respuesta de voz` (breaking)

## Flujo de PR

1. Branch desde main actualizada: `git checkout main && git pull && git checkout -b type/descripcion`
2. Programar siguiendo skills relevantes (python-standards, testing-coverage).
3. **Commits atómicos y descriptivos.** Cada cambio lógico su propio commit. Múltiples commits en la branch están bien — no hay que aplastarlos manualmente. Lo ÚNICO prohibido: commits WIP, `auto-save:`, o basura temporal.
4. Validar local: `make lint && make typecheck && make test-backend`.
5. Push: `git push -u origin type/descripcion`
6. Crear PR: `gh pr create` — GitHub carga la plantilla automáticamente.

## Squash lo hace GitHub, no tú

La branch puede tener **varios commits atómicos** (1 por cambio lógico). Eso es normal y deseable durante el review: cada commit cuenta una historia clara, reversible, revisable.

Al aprobar el PR, el botón **"Squash and merge"** de GitHub aplasta todos los commits de la branch en **1 solo commit en `main`** con el mensaje Conventional Commits que elijas. El historial de `main` queda limpio sin que tengas que reescribir la branch.

### Lo que NO se hace

- **NUNCA `git push --force-with-lease` ni `--force`.** Reescribir historia remota compartida rompe el flujo de review, invalida comentarios línea-por-línea, y es mala práctica en equipos.
- **NUNCA commit de WIP, `auto-save:`, `tmp:`, o basura.** Si generaste basura local, hace `git reset -p` o `git rebase -i` ANTES del primer push. Una vez pusheado, commits nuevos encima, sin reescribir.
- **NUNCA `git commit --amend` de un commit ya pusheado.** Si necesitás ajustar algo, commit nuevo encima. GitHub lo aplasta todo al mergear.

### Lo que SÍ se hace

- **Commits atómicos durante el desarrollo.** 3 cambios independientes = 3 commits. Cada uno compila y pasa tests.
- **Push normal (`git push`).** Sin flags raros.
- **Si el PR pide cambios en review:** commit nuevo con el fix, push normal. El historial de la branch refleja fielmente lo que pasó.
- **Al mergear:** "Squash and merge" → 1 commit limpio en `main`.

## Reglas críticas

- **Cada PR DEBE vincular un issue aprobado**: `Closes #N` en el cuerpo. El workflow `pr-check.yml` lo verifica automáticamente (bloquea el merge si no).
- **El issue DEBE tener `status:approved`**. `pr-check.yml` bloquea el merge si no.
- El PR DEBE tener al menos un label `mod:*`. `pr-check.yml` lo verifica.
- NUNCA `Co-Authored-By` ni huella de IA.
- `make lint && make test-backend` antes de pushear. Sin excepciones.
- PRs sin issue aprobado se cierran sin review.

## Cookbook

| Si... | Entonces... | Ejemplo |
|---|---|---|
| PR de bug fix | branch `fix/...`, label `bug` + `mod:*` | `fix/validacion-hmac-webhook` |
| PR de feature | branch `feat/...`, label `enhancement` + `mod:*` | `feat/endpoint-precios-papa` |
| PR de docs | branch `docs/...`, label `documentation` | `docs/migrar-twilio-openwa` |
| PR de refactor | branch `refactor/...`, label `refactor` | `refactor/servicio-openwa` |
