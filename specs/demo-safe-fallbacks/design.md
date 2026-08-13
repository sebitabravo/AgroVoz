# Design — demo-safe-fallbacks

## Summary
La corrección conserva la arquitectura actual. El backend falla cerrado, mantiene el fast-path y solo usa contexto demo para referencias climáticas explícitas. El cliente hace visible cada error y permite repetirlo.

## Technical Decisions
| Decision | Rejected Alternative | Reason |
|---|---|---|
| Retornar mensaje seguro sin modelo | Mock de producción | Un precio simulado viola grounding. |
| Rechazar comuna no soportada | Default silencioso a Traiguén | No se puede atribuir clima ajeno. |
| Historial en memoria/request | SQLite | La demo no requiere ni debe persistir consultas. |
| Precedencia para semillas | Precio de cultivo fresco | Son mercados y unidades diferentes. |

## Data Flow
```text
Demo UI (últimos 6 turnos) -> POST /demo/preguntar -> schema valida
  -> expansión determinista de seguimiento climático -> fast-path/LLM
  -> respuesta + TTS
```

Si `fetch` falla, la UI agrega una burbuja recibida con botón de reintento. No hay interpolación HTML de texto de usuario.

## Risks
| Risk | Mitigation |
|---|---|
| Contexto antiguo confunde | Se limita a seis turnos y referencia climática inequívoca. |
| Usuario espera precios de semilla | Se informa falta de fuente verificable. |
| UI duplica el reintento | Botón se deshabilita durante la petición. |
