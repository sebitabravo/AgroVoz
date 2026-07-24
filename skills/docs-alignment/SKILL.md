# Skill: agrovoz-docs-alignment

## Propósito
Mantener docs en sincronía con el código. Fuente de verdad = `AGENTS.md` + `docs/`.

## Cuándo usarlo
Cuando un cambio afecta arquitectura, stack, restricciones o plan de fases.

## Fuentes de verdad (qué vive dónde)

| Doc | Contenido |
|---|---|
| `AGENTS.md` | Stack, hard constraints, convenciones, estructura, comandos |
| `docs/ARCHITECTURE.md` | Flujo, ADRs, schema DB, decisiones técnicas |
| `docs/DEV-GUIDE.md` | Guía de desarrollo y setup local |
| `CONTRIBUTING.md` | Cómo trabajamos en GitHub (issues, PR, review) |
| `README.md` | Pitch corto + setup rápido |

## Regla change → doc

| Cambio | Doc a actualizar |
|---|---|
| Cambio de stack (librería, runtime) | `AGENTS.md` (stack) + `docs/ARCHITECTURE.md` |
| Hard constraint nueva o modificada | `AGENTS.md` (hard constraints) |
| Decisión técnica significativa | `docs/ARCHITECTURE.md` (sección ADR) |
| Cambio de schema DB | migración Alembic + `docs/ARCHITECTURE.md` |
| Nuevo endpoint / flujo | `docs/ARCHITECTURE.md` (diagrama) |
| Fase completada | actualizar `AGENTS.md` (tabla de fases) |
| Convención de código nueva | `AGENTS.md` (convenciones) + skill |
| Workflow GitHub nuevo | `CONTRIBUTING.md` + skill |

## ADR (Architecture Decision Record)

Cada decisión significativa (¿por qué este modelo de Whisper? ¿por qué Open-WA y
no Twilio?) se documenta en `docs/ARCHITECTURE.md` sección Decisiones:

- **Contexto**: problema.
- **Opciones**: alternativas consideradas.
- **Decisión**: qué y por qué.
- **Consecuencias**: trade-offs.

## Reglas críticas

- NUNCA inventar arquitectura. Si no está en `docs/ARCHITECTURE.md`, **preguntar** antes de asumir.
- Si algo no está definido → opción más simple + documentarla en ADR.
- Un PR que cambia arquitectura pero no toca docs = **REQUEST_CHANGES**.
- Los comentarios en código explican el PORQUÉ local. La doc explica el PORQUÉ global.

## Anti-patrones

- Cambiar de Twilio a Open-WA en código pero dejar docs hablando de Twilio.
- Agregar endpoint sin actualizar diagrama de flujo.
- Documentar decisión solo en el PR (se pierde). → copiar a `docs/ARCHITECTURE.md`.
- Drift entre `AGENTS.md` (dice X) y `docs/ARCHITECTURE.md` (dice Y). → reconciliar.

## Cookbook

| Si... | Entonces... |
|---|---|
| Agregás campo a la DB | migración + actualizar doc de schema |
| Cambiás modelo Whisper (small → tiny) | ADR con motivo (latencia vs precisión) |
| Nueva feature o cambio de arquitectura | `AGENTS.md` + `docs/ARCHITECTURE.md` |
| Hard constraint nueva (ej: PII) | `AGENTS.md` hard constraints |
