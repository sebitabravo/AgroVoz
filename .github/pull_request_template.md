<!-- AgroVoz — Plantilla de Pull Request. GitHub carga esto automáticamente al abrir un PR. -->

## Resumen

<!-- 1-3 bullets: qué cambia y por qué. Máximo 3 líneas. -->

-

## Tipo de cambio

- [ ] 🐛 Fix (corrige bug, sin breaking change)
- [ ] ✨ Feature (agrega funcionalidad, sin breaking change)
- [ ] 💥 Breaking change
- [ ] 📝 Documentación
- [ ] 🎨 Refactor
- [ ] 🚀 Performance
- [ ] ⚙️ Config / Infra (Docker, Dokploy, CI/CD, variables)

## Issue relacionada

Closes #
Fase: `docs/phases/0X-*.md` (si aplica):

## Módulos afectados

- [ ] Backend (FastAPI, Python)
- [ ] Pipeline de voz (Whisper / LLM / TTS)
- [ ] Integración Open-WA (WhatsApp)
- [ ] Landing (Astro)
- [ ] Admin (Jinja2 + HTMX)
- [ ] Infra (Docker, Dokploy, CI/CD)
- [ ] Docs

## Checklist

### Calidad

- [ ] Leí `AGENTS.md` y respeté el alcance de la issue
- [ ] Hice auto-review del código
- [ ] Comentarios en español (contexto académico INACAP)
- [ ] No hay huella de IA en commits (`Co-authored-by`, `[AI]`)
- [ ] Cambio chico, reversible, alineado al MVP

### Seguridad y datos (no negociable)

- [ ] No subí secretos / `.env` / `*.db` / `models/` / `data/`
- [ ] No sugerí ni integré APIs pagas externas (hard constraint del proyecto)
- [ ] Input del webhook validado (HMAC, rate limiting, hash de número)
- [ ] Audio temporal se elimina (<24h), transcripciones anonimizadas (Ley 21.719)

### Validación ejecutada

```bash
# Pegá acá comandos y resultados reales. Ejemplo:
# cd backend && uv run ruff check app/    -> OK
# cd backend && uv run pytest tests/ -v   -> 24 passed
# cd landing && bun run build             -> OK
```

- [ ] Backend: `cd backend && uv run ruff check app/ && uv run mypy app/`
- [ ] Backend: `cd backend && uv run pytest tests/ -v --cov=app`
- [ ] Landing (si tocaste): `cd landing && bun run build`
- [ ] Docker (si tocaste): `docker compose config --quiet`

## Cómo probar

<!-- Pasos para que el reviewer valide. Precondiciones + pasos + resultado esperado. -->

1.
2.
3.

## Riesgos y rollback

- **Riesgo:** (Low / Medium / High / Critical)
- **Rollback:** (cómo revertir si falla en producción)

## Review asistida por IA (opcional)

Si pediste review a una IA, dejá el veredicto acá:
- Veredicto: APPROVE / REQUEST_CHANGES / BLOCK
- Hallazgos críticos/high resueltos: sí / no / n/a
