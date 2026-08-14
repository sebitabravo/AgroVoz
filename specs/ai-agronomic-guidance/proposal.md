# Proposal — ai-agronomic-guidance

## Meta

- **Feature:** ai-agronomic-guidance
- **Author:** Sebastian Bravo / Codex
- **Status:** proposed
- **Date:** 2026-08-14

## Intent

El Data Hub y los corpus de reglas agronómicas ya existen, pero la IA no los
ofrece al agricultor: el prompt prohíbe recomendaciones y
`AGRONOMIC_RULES_ENABLED` permanece apagado. Esta propuesta convierte esa
capacidad en orientación conversacional acotada, citada y reproducible, sin
confundir un catálogo visual de la landing con una funcionalidad real del
agente.

## Scope

### In
- Activar en el runtime demo/producción las tools de reglas agronómicas y calendario.
- Permitir que la IA entregue solo reglas verificadas con fuente, fecha y límite.
- Mantener la resolución determinista del pipeline como camino principal.
- Agregar pruebas de prompt, configuración, herramienta y smoke de demo.
- Actualizar arquitectura, README y runbook con la capacidad real.

### Out
- Diagnóstico médico/agronómico personalizado o sustitución de un agrónomo.
- Dosis de pesticidas, tratamientos inventados o instrucciones fuera del corpus.
- Recomendaciones de crédito, elegibilidad o montos INDAP.
- Scraping continuo, un dataset monolítico de fine-tuning o nuevas fuentes no verificadas.

## Approach

Se conserva el motor determinista existente (`agronomic_rules_service.py` y
`agricultural_calendar_service.py`) y el fast path de `pipeline_service.py`.
Las tools se anuncian únicamente cuando el gate está activo; el LLM solo
verbaliza la respuesta devuelta por la regla y no genera el contenido factual.
El prompt se cambia de “nunca recomendaciones” a “solo recomendaciones
citadas por estas tools”. La configuración de contenedor habilita el gate; el
default de la clase `Settings` permanece cerrado para tests y procesos que no
declaren la capacidad explícitamente.

## Constitution Alignment

| Principle | Aligned? | Notes |
|---|---|---|
| Recomendaciones solo por regla citada | ✅ | Las respuestas vienen del corpus validado y fallan cerrado. |
| Fallback local obligatorio | ✅ | No agrega un proveedor externo; el fast path local precede al LLM. |
| WhatsApp primero / PWA opcional | ✅ | La misma capacidad sirve al pipeline de texto, audio y WhatsApp. |
| Hardware degradado y latencia | ⚠️ | El camino determinista es rápido; el LLM general no se usa para la recomendación. |

## Rationale

La capacidad ya tiene corpus, servicios y tests; activar esos componentes es
más seguro y pequeño que construir otro sistema de recomendaciones.

| Alternative | Why Rejected |
|---|---|
| Dejar solo el copy de la landing | No cambia el comportamiento de la IA. |
| Dejar al LLM recomendar libremente | Viola el hard constraint y permite alucinaciones. |
| Entrenar un modelo nuevo con un dataset gigante | No garantiza vigencia, trazabilidad ni licencia. |

## Affected Areas

`backend/app/services/prompt_builder.py`, `backend/app/services/llm_service.py`,
`docker-compose.yml`, `docker-compose.prod.yml`, `.env.example`, pruebas del
pipeline/LLM, smoke de demo y documentación de arquitectura.

## References

- `docs/ARCHITECTURE.md` — hard constraint y flujo de tools.
- `backend/corpus/reglas_agronomicas.yaml` — reglas INIA verificadas.
- `backend/corpus/calendario_agricola.yaml` — ventanas citadas de INIA.
