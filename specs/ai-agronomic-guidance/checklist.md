# Checklist — ai-agronomic-guidance

## Explore / Design

- [x] Se leyó `AGENTS.md`, README y `docs/ARCHITECTURE.md`.
- [x] Se confirmó que la landing solo comunica el ecosistema.
- [x] Se confirmó que las tools y los servicios deterministas ya existen.
- [x] Se definió que no habrá recomendaciones libres del LLM.

## Apply

- [x] Prompt local permite orientación citada.
- [x] Prompt OpenRouter permite orientación citada.
- [x] Compose demo y producción activan el gate explícitamente.
- [x] Las regresiones cubren síntoma, calendario y ausencia de regla.

## Verify

- [x] Tests focalizados pasan (40 passed).
- [x] Suite completa, lint y mypy pasan (2258 tests, 86.40%).
- [x] Smoke de demo cubre recomendaciones.
- [x] Documentación refleja la capacidad real.
- [ ] Producción publicada y smoke externo: gate separado, pendiente de canal autorizado.
