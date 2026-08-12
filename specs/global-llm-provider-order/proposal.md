# Proposal — global-llm-provider-order

## Meta

- **Feature:** global-llm-provider-order
- **Author:** Codex
- **Status:** approved
- **Date:** 2026-08-11

## Intent

AgroVoz debe intentar OpenRouter primero para reducir la latencia del camino
LLM en la demo y WhatsApp, y pasar al Qwen local cuando el remoto no responde
rápidamente. El fast-path determinístico sigue antes de ambos proveedores.

## Scope

### In
- `LLM_PRIMARY_PROVIDER=openrouter|local` para controlar el orden global.
- Deadline total de OpenRouter de 2 segundos por defecto.
- Fallback automático al Qwen local y luego al fallback determinista existente.
- Contexto de conversación, cultivos, system tip y tipo de consulta preservados.
- OpenRouter primario limitado a tools de lectura.
- Operaciones mutables dirigidas al Qwen local para evitar duplicados.
- Configuración en ambos Compose, ejemplos de entorno, ADR y pruebas.

### Out
- Eliminar Qwen, Whisper o Piper.
- Agregar SDK nuevo o persistir prompts/API keys.
- Permitir que OpenRouter ejecute escrituras antes del local.
- Hacer una llamada externa real sin API key y autorización del equipo.

## Approach

Reutilizar el cliente HTTP y tool calling existente. Agregar un orquestador
común `answer_with_provider_order()` usado por pipeline y demo. El intento
remoto está envuelto en un timeout total, exige una tool válida y solo anuncia
lecturas. Consultas con marcadores de escritura saltan el remoto y van al local.

## Constitution Alignment

| Principle | Aligned? | Notes |
|---|---|---|
| Fallback local obligatorio | ✅ | Qwen sigue disponible y es segundo proveedor. |
| Datos grounded | ✅ | Se reutilizan tools allowlisted y el prompt restringido. |
| Secrets fuera de git | ✅ | La key llega por `.env`/Secrets UI. |
| Hardware degradado | ⚠️ | Hay dependencia de red en el primer escalón, pero el deadline y Qwen local limitan el impacto. |
| No duplicar escrituras | ✅ | El remoto primario no recibe tools mutables. |

## Rationale

| Alternative | Why Rejected |
|---|---|
| Qwen primero y OpenRouter fallback | Mantiene la latencia local aunque el remoto esté sano. |
| OpenRouter con todas las tools primero | Un timeout después de una escritura puede duplicar registros al reintentar local. |
| Solo cambiar timeout HTTP a 2s | No limita el loop completo de hasta tres requests. |
| Eliminar Qwen | Rompe el requisito de resiliencia local y la operación sin red. |

## Affected Areas

- `backend/app/core/config.py`
- `backend/app/services/llm_service.py`
- `backend/app/services/pipeline_service.py`
- `backend/app/services/demo_service.py`
- `backend/tests/test_llm_service.py`, `backend/tests/test_pipeline_service.py`, `backend/tests/test_demo_endpoint.py`
- `docker-compose.yml`, `docker-compose.prod.yml`, `.env.example`, `.env.production.example`
- `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/DEPLOYMENT.md`
