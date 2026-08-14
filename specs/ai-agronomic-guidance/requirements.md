# Requirements — ai-agronomic-guidance

## Meta

- **Feature:** ai-agronomic-guidance
- **Author:** Sebastian Bravo / Codex
- **Status:** spec_ready
- **Date:** 2026-08-14
- **Constitution:** [x] Verified against project constitution

## Context

La recomendación recibida pide que la IA ayude al agricultor con orientación,
no que el producto solo enumere fuentes. El agente debe usar información
integrada y actualizable, pero responder únicamente cuando exista una regla o
ventana publicada, con fuente y fecha.

## User Stories

### User Story 1 — Orientación ante síntomas (Priority: P1) 🎯 MVP

**Narrative:** Como agricultor, quiero describir un problema de mi cultivo y
recibir una orientación factual basada en una regla oficial citada.

**Why this priority:** Es la diferencia entre un catálogo de datos y una IA
agrícola útil, sin permitir consejos inventados.

**Independent Test:** Preguntar por manchas marrones en papa con el gate activo;
la respuesta debe citar INIA y fecha, y no debe invocar al LLM para inventar el
diagnóstico.

**Acceptance Scenarios:**

1. **Given** un corpus vigente, **When** se describe un síntoma conocido,
   **Then** se entrega la regla citada y su siguiente paso seguro.
2. **Given** un síntoma no cubierto, **When** se consulta la IA, **Then** se
   informa que no existe una regla verificada y no se improvisa.

### User Story 2 — Calendario por cultivo y comuna (Priority: P1)

**Narrative:** Como agricultor, quiero preguntar cuándo sembrar o cosechar un
cultivo en mi comuna y recibir solo una ventana publicada por INIA.

**Why this priority:** Es una recomendación accionable y verificable para el
piloto territorial.

**Independent Test:** Preguntar por siembra de trigo en Traiguén y comprobar
que se cita la ficha INIA; una comuna sin cobertura debe fallar cerrado.

**Acceptance Scenarios:**

1. **Given** una ventana vigente, **When** se entrega cultivo y comuna,
   **Then** la IA responde la ventana, fuente y fecha.

### User Story 3 — Límites honestos (Priority: P1)

**Narrative:** Como agricultor, quiero saber cuándo AgroVoz no tiene una regla
vigente para no tomar una decisión basada en información antigua.

**Why this priority:** La seguridad y trazabilidad son parte del producto,
especialmente para consejos agronómicos.

**Independent Test:** Vencer o corromper el snapshot y comprobar una respuesta
fail-closed sin contenido de recomendación.

**Acceptance Scenarios:**

1. **Given** un snapshot vencido o inválido, **When** se consulta,
   **Then** se rechaza la recomendación y se explica el límite.

## Functional Requirements (EARS)

### FR-001 — Reglas agronómicas citadas

**Type:** Event-Driven

**Description:** When the user describes a covered symptom or asks for a
covered agronomic rule, the system MUST resolve the response from the verified
rule service and include source and verification date.

**Acceptance Criteria:**
- [x] Un síntoma conocido de papa retorna texto de INIA y fecha.
- [x] No se usa la salida generativa para completar el diagnóstico.

### FR-002 — Calendario territorial

**Type:** Event-Driven

**Description:** When the user asks for planting, harvest or rotation timing,
the system MUST require a covered crop/location pair and return only the
published calendar row.

**Acceptance Criteria:**
- [x] Traiguén con cultivo cubierto retorna ventana, fuente y fecha.
- [x] Comuna o cultivo sin cobertura retorna ausencia de dato.

### FR-003 — Gate operativo

**Type:** State-Driven

**Description:** While `AGRONOMIC_RULES_ENABLED` is false, the system MUST NOT
announce or execute the agronomic tools; when enabled, it MUST expose only the
two cited tools for agronomic queries.

**Acceptance Criteria:**
- [x] Tests pass for gate off/on at service and LLM tool layers.

### FR-004 — Prompt seguro

**Type:** Ubiquitous

**Description:** The local and OpenRouter prompts MUST permit only cited
agronomic orientation and MUST forbid personalized diagnosis, pesticide doses,
and invented recommendations.

**Acceptance Criteria:**
- [x] Los dos prompts no contienen una prohibición absoluta que bloquee las tools.
- [x] Ambos prompts exigen fuente/fecha y límites explícitos.

### FR-005 — Configuración reproducible

**Type:** State-Driven

**Description:** Demo y producción MUST declare the feature flag in Compose;
plain library defaults remain fail-closed when no environment opts in.

**Acceptance Criteria:**
- [x] `.env.example` documenta el flag.
- [x] Compose demo/prod entrega `AGRONOMIC_RULES_ENABLED=true` salvo override explícito.

## Key Entities

La feature no agrega tablas: reutiliza reglas y calendarios versionados con
`fuente`, `fuente_url`, `fecha`, `verificado_el` y `revisar_antes_de`.

## Non-Functional Requirements

### Performance
- El camino determinista debe mantenerse debajo del presupuesto de 15 s E2E.

### Security
- No pedir RUT, deuda, ingreso ni datos personales; no entregar tratamientos
  personalizados; falla cerrado ante corpus vencido o corrupto.

### Accessibility
- La respuesta debe ser breve y apta para voz; la demo conserva texto y TTS.

## Edge Cases

| Case | Expected Behavior |
|---|---|
| Cultivo/comuna faltante | Solicita el dato faltante o informa que no puede resolverlo. |
| Regla fuera de corpus | Responde que no tiene regla verificada. |
| Snapshot vencido | Rechaza la orientación y no usa el texto antiguo. |
| LLM no disponible | El fast path determinista sigue resolviendo calendario/regla. |

## Success Criteria

### Measurable Outcomes

- **SC-001**: Las consultas cubiertas de regla/calendario entregan fuente y fecha en 100% de los tests.
- **SC-002**: Las consultas fuera de cobertura entregan cero recomendaciones inventadas.
- **SC-003**: El flujo determinista de recomendación no invoca el LLM generativo.

## Assumptions

- El alcance inicial cubre las reglas actuales de papa y el calendario versionado de INIA.
- Las respuestas son orientación pública, no asesoría personalizada profesional.
- La actualización de corpus sigue siendo un sync/admin operable separado.

## Out of Scope

- Recomendaciones libres generadas por el LLM.
- Nuevas integraciones no verificadas o recomendaciones financieras.

## References

- `docs/ARCHITECTURE.md`
- `backend/corpus/reglas_agronomicas.yaml`
- `backend/corpus/calendario_agricola.yaml`
