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
3. Validar local: `make lint && make typecheck && make test-backend`.
4. **Squash** a 1 commit limpio (ver abajo).
5. Push: `git push -u origin type/descripcion`
6. Crear PR: `gh pr create` — GitHub carga la plantilla automáticamente.

## Squash obligatorio (1 commit limpio por branch)

```bash
git rebase -i origin/main              # marcar todos menos el primero como `squash`
git log origin/main..HEAD --oneline    # verificar: 1 línea
git push --force-with-lease            # si ya estaba pusheada
```

Merge en GitHub: **"Squash and merge"**. `main` queda con 1 commit por PR.

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
