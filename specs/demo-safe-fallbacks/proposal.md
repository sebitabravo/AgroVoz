# Proposal — demo-safe-fallbacks

## Meta
- **Feature:** demo-safe-fallbacks
- **Author:** Codex
- **Status:** approved
- **Date:** 2026-08-13

## Intent
Corregir los caminos de degradación de la demo pública para que nunca emitan datos simulados, ubicación inventada ni instrucciones internas; además, hacer explícitos el contexto conversacional y los errores de red.

## Scope
### In
- Fallback seguro cuando no hay LLM.
- Rechazo transparente de comuna explícita no soportada.
- Historial demo acotado, no persistido.
- Burbuja de error y reintento de red.
- Manejo conservador de semillas, plurales y consultas compuestas.

### Out
- Persistir historial o datos personales en SQLite.
- Agregar una fuente de precios de semillas o geocoding nacional.
- Cambiar ODEPA, OpenMeteo, el modelo comercial o WhatsApp.

## Approach
Reutilizar los fast-path deterministas. El mock queda como utilidad de tests, pero no como fallback runtime. El frontend mantiene hasta seis turnos textuales en memoria, que el backend valida y usa únicamente para expandir referencias climáticas inequívocas antes de entrar al proveedor LLM.

## Constitution Alignment
| Principle | Aligned? | Notes |
|---|---|---|
| No inventar datos | ✅ | Fallback y semillas fallan cerrados. |
| Fallback local obligatorio | ✅ | No se elimina OpenRouter/Qwen. |
| Datos transitorios | ✅ | Historial solo en memoria del navegador/request. |
| Latencia <15 s | ✅ | Se evita LLM ante fuera de dominio. |

## Affected Areas
- `backend/app/services/{llm_service,weather_service,demo_service,llm_keywords}.py`
- `backend/app/schemas/demo.py`
- `backend/tests/{test_llm_service,test_weather_service,test_demo_endpoint,test_mercado_fast_path}.py`
- `landing/src/pages/demo.astro`
