# Requirements — global-llm-provider-order

## Meta

- **Feature:** global-llm-provider-order
- **Author:** Codex
- **Status:** spec_ready
- **Date:** 2026-08-11
- **Constitution:** [x] Verificado contra `AGENTS.md` y ADR 32

## Context

La integración OpenRouter existía como fallback, pero el runtime usaba Qwen
primero. El equipo confirmó invertir el orden: remoto primero cuando hay key,
Qwen segundo, sin sacrificar fast-path ni permitir duplicados de escrituras.

## User Stories

### User Story 1 — Proveedor remoto primero (Priority: P1) 🎯 MVP

**Narrative:** Como operador, quiero que las consultas de lectura prueben
OpenRouter antes de Qwen para responder más rápido.

**Independent Test:** Mockear OpenRouter exitoso y Qwen fallando; verificar que
se retorna la respuesta remota.

**Acceptance Scenarios:**

1. **Given** provider global `openrouter` y API key, **When** una consulta no
   resuelve el fast-path, **Then** OpenRouter se llama antes de Qwen.
2. **Given** OpenRouter retorna una tool de lectura y respuesta final, **When**
   el pipeline procesa la consulta, **Then** se conserva el dato y el origen es
   `openrouter`.

### User Story 2 — Fallback por deadline (Priority: P1)

**Narrative:** Como usuario, quiero que una caída remota pase al Qwen sin dejar
la consulta bloqueada.

**Independent Test:** Mockear OpenRouter durmiendo más que el deadline y Qwen
respondiendo; verificar respuesta local.

**Acceptance Scenarios:**

1. **Given** deadline total de 2 segundos, **When** el loop remoto se demora,
   **Then** se cancela el intento y se llama Qwen.
2. **Given** ausencia de API key, **When** llega una consulta, **Then** se salta
   OpenRouter y se llama Qwen directamente.

### User Story 3 — Escrituras sin duplicación (Priority: P1)

**Narrative:** Como operador, quiero que registrar gastos/parcela/reportes no
se ejecute primero en un proveedor que podría expirar después del efecto.

**Independent Test:** Mockear una consulta mutable y verificar que el remoto no
se invoca y el local recibe la consulta.

**Acceptance Scenarios:**

1. **Given** un comando de escritura, **When** llega al orquestador, **Then** se
   usa Qwen local como primer proveedor.
2. **Given** un intento remoto primario, **When** se construyen sus tools,
   **Then** no se incluyen tools mutables.

### User Story 4 — Configuración y rollback (Priority: P2)

**Narrative:** Como operador, quiero cambiar el orden sin editar código y
volver a local si OpenRouter presenta problemas.

**Independent Test:** Instanciar Settings con `openrouter` y `local`, y renderizar
ambos Compose.

## Functional Requirements

### FR-001 — Orden global configurable

El sistema MUST aceptar `LLM_PRIMARY_PROVIDER` como `openrouter` o `local`, con
`openrouter` como default operativo y salto automático a local cuando no hay key.

### FR-002 — Deadline total

El intento remoto primario MUST tener un deadline total configurable por request,
2.0 segundos por defecto, incluyendo todas sus iteraciones de tool calling.

### FR-003 — Fallback local

Si OpenRouter retorna None, falla, excede deadline, recibe rate limit o no
produce una tool válida, MUST ejecutarse Qwen local antes del fallback keyword.

### FR-004 — Contexto preservado

El orquestador MUST pasar history, phone hash, cultivos, system tip y tipo de
consulta a los proveedores que corresponda.

### FR-005 — Seguridad de escrituras

El intento remoto primario MUST anunciar únicamente tools de lectura y MUST
saltar consultas con intención potencialmente mutable.

### FR-006 — Compose/documentación

Dev/prod MUST exponer provider, key, model, timeout por request, deadline total y
max tokens sin incluir secretos reales.

## Non-Functional Requirements

- Fast-path de precio/clima/saludo no realiza llamada remota.
- Logs no contienen key, prompt completo, teléfono ni argumentos sensibles.
- No se agregan dependencias.
- `LLM_PRIMARY_PROVIDER=local` es rollback operativo.

## Edge Cases

| Case | Expected Behavior |
|---|---|
| API key ausente | Saltar remoto y usar Qwen. |
| Timeout remoto | Cancelar el intento total y usar Qwen. |
| Respuesta directa sin tool en modo primario | Rechazarla y usar Qwen. |
| Tool mutable solicitada por remoto | Rechazar intento remoto y usar Qwen. |
| Comando de escritura | Ir directo a Qwen. |
| Fast-path | Responder sin OpenRouter ni Qwen. |
| Provider inválido | Settings falla por validación Literal. |

## Success Criteria

- **SC-001:** Tests prueban remoto primero, timeout y Qwen segundo.
- **SC-002:** Tests prueban que escrituras no se duplican por remoto.
- **SC-003:** `make test`, ruff, mypy, diff check y Compose config pasan.
- **SC-004:** Cobertura se mantiene sobre el umbral del proyecto.

## Out of Scope

- Prueba real de red sin key/autorización.
- Selección automática de modelos o facturación.
- Eliminación de modelos locales.
