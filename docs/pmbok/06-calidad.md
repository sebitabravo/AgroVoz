# Plan de Gestión de la Calidad

> **Versión:** 0.1 — borrador de control
> **Fecha de medición:** 29 de julio de 2026
> **Estado:** criterios definidos; gate de piloto no aprobado

## 1. Objetivo

La calidad de AgroVoz se evalúa por funcionamiento verificable en el hardware mínimo, exactitud de
voz, seguridad y utilidad para el productor. Cantidad de código, issues cerrados o tests recolectados
no sustituyen una medición de producto.

Este documento distingue:

- **Requisito:** condición obligatoria o meta declarada.
- **Medido:** resultado ejecutado en la fecha indicada.
- **Reportado:** antecedente de `AGENTS.md` que no se repitió en esta revisión.
- **Pendiente:** falta ejecutar o aprobar evidencia.

## 2. Resultado técnico del corte

| Verificación | Comando | Resultado medido |
|---|---|---|
| Suite completa y colección | `cd backend && uv run pytest tests/ -q --tb=short` | **1.664 recolectados: 1.639 pasaron, 0 fallaron y 25 fueron omitidos** |
| Regresión del rate limiter | `pytest tests/test_security.py tests/test_webhook.py -q --tb=short` | 49/49 pasaron en el orden que antes exponía contaminación |
| Regresión de sincronización ODEPA | `pytest tests/test_sync_odepa.py -q --tb=short` | 14/14 pasaron con logging saneado |
| Linter | `cd backend && uv run ruff check app/ tests/ scripts/` | Aprobado |
| Tipado | `cd backend && uv run mypy app/` | Aprobado sobre 72 archivos fuente |
| Migraciones | `alembic upgrade head` sobre SQLite temporal desde cero | Cadena completa aplicada hasta `f2a8c1d7e4b6` |
| Landing | `cd landing && bun run build` | 2 rutas estáticas construidas |
| Compose | `docker compose ... config --quiet` | Dev, producción y override de piso válidos |
| CI remoto reciente | `gh run list` y checks de PR | CI técnico exitoso en ejecuciones observadas; existen fallos de policy checks en PR abiertos |

La suite completa está verde en este snapshot. La contaminación entre instancias del rate limiter y
la expectativa obsoleta del test ODEPA fueron corregidas con regresiones focales. Los 25 omitidos
dependen de integración E2E, modelos o hardware que este entorno local no aporta; no se cuentan como
validación de WhatsApp real ni del piso operativo.

La medición se hizo sobre un worktree con cambios sin commit; no representa automáticamente `main`
ni producción.

## 3. Matriz de calidad

| Dimensión | Requisito o meta | Método de verificación | Estado al corte |
|---|---|---|---|
| Latencia de voz | Menor a 15 s E2E en 1 vCPU / 6 GB RAM | Benchmark repetible con audio, Whisper, resolución, TTS y entrega | **Pendiente.** `AGENTS.md` reporta ~11 s en caliente, no reejecutado aquí |
| Latencia de texto | Camino sin Whisper ni TTS | Medición desde webhook hasta entrega escrita | **Reportado** ~100 ms; pendiente serie fechada |
| Precisión Whisper | WER menor a 15%; stretch menor a 10% | `eval_wer.py` sobre muestra consentida de español rural de Traiguén | **Pendiente; no hay resultado de piloto** |
| Funcionalidad de datos | Precios y clima con fuente, sin inventar | Tests por tool, casos de catálogo y smoke con fuentes | Suite automatizada verde; smoke externo pendiente |
| Cobertura ODEPA | 79 productos y 15 mercados | Consulta automatizada del catálogo vigente | **Reportado** en `AGENTS.md`; no reejecutado en este corte |
| Restricción agronómica | Entregar datos, no recomendaciones | Tests de prompt, respuestas determinísticas y revisión de muestra | Controles implementados; requiere monitoreo continuo |
| Stack local | IA y gateway locales, sin dependencia paga obligatoria | Inventario de dependencias, config y prueba sin claves externas | Declarado; auditoría reproducible pendiente |
| Retención de audio | Eliminar temporales en menos de 24 h | Config, job de limpieza y prueba sobre archivos fallidos/exitosos | Config medido en 24 h; ejecución operativa y backups pendientes |
| Seguridad de datos | Sin secretos ni contenido sensible en logs | Tests `caplog`, revisión de logs y secret scanning | Tests específicos presentes; revisión operativa pendiente |
| Disponibilidad | Responder pese a fallos recuperables de fuentes/canal | Pruebas de timeout, retry, fallback y smoke de entrega | Cobertura automatizada parcial; Open-WA sigue siendo punto crítico |
| Costo | CLP 100–150 por agricultor/mes, sin API WhatsApp | Costos reales del VPS y usuarios activos del período | **Pendiente; no hay piloto ni costo unitario medido** |
| Accesibilidad | Voz comprensible y vías de texto/admin utilizables | Pruebas técnicas más validación con productores | Controles web parciales; validación de terreno pendiente |

## 4. Configuración observada

La introspección de `Settings` local mostró:

| Parámetro no sensible | Valor local | Interpretación |
|---|---:|---|
| `whisper_model` | `small` | Modelo objetivo actual |
| `audio_retention_hours` | `24` | Umbral configurado; no prueba el cleanup en producción |
| `consultation_history_enabled` | `false` | Historial contextual cerrado por defecto |
| `consultation_history_ttl_days` | `28` | TTL si se habilita; no aplica a todas las tablas |
| `use_conversation_state` | `false` | Estado conversacional cerrado por defecto |
| `mcp_enabled` | `false` | MCP cerrado por defecto |
| `expense_tracking_enabled` | `false` | Registro de gastos cerrado hasta resolver persistencia, retención e integración |
| `demo_endpoint_enabled` | `false` | Demo backend cerrada por defecto |

La carga local emitió advertencias por valores de desarrollo no aptos para producción. Eso no
demuestra cómo está configurado el VPS. Nunca se deben guardar los valores de claves en este plan.

## 5. Gates de calidad

| Gate | Condición de salida | Estado |
|---|---|---|
| Q0 — Estática | `ruff check app/` y `mypy app/` sin errores | **Cumplido en el worktree medido** |
| Q1 — Regresión | Suite completa sin fallos inesperados; skips justificados | **Cumplido en el worktree: 1.639 aprobados, 25 omitidos** |
| Q2 — Hardware mínimo | Latencia sostenida menor a 15 s en 1 vCPU / 6 GB | **Pendiente** |
| Q3 — Voz rural | WER menor a 15% sobre muestra válida | **Pendiente** |
| Q4 — Seguridad y privacidad | Hallazgos críticos cerrados y revisión requerida completada | **Pendiente** |
| Q5 — Preparación de piloto | Smoke real de WhatsApp, recuperación y kit aprobado | **Pendiente** |
| Q6 — Aceptación de terreno | Resultados reales de 3–5 productores durante 4 semanas | **No iniciado** |

El estado actual es **no-go para declarar calidad de piloto completa**. No equivale a que el producto
no funcione: Q0 y Q1 están cubiertos localmente, pero faltan hardware mínimo, WER, revisión
legal/privacidad y un smoke real de WhatsApp.

## 6. Protocolo de medición pendiente

### Latencia

- Registrar SHA, imagen, modelo, número de vCPU, RAM y condición fría/caliente.
- Medir desde recepción del webhook hasta confirmación de entrega.
- Separar descarga, ffmpeg, Whisper, resolución/tool, LLM, TTS y Open-WA.
- Publicar la distribución y los fallos; no solo el mejor caso.
- Comparar siempre con 1 vCPU / 6 GB, aunque también se mida el CX43.

No se fija aquí un número de repeticiones: debe aprobarse antes de iniciar para evitar seleccionar la
muestra después de ver los resultados.

### WER

- Usar audio con consentimiento válido y transcripción humana de referencia.
- Identificar versión del dataset sin incluir teléfonos ni nombres.
- Reportar sustituciones, inserciones, eliminaciones y WER.
- Separar resultado global y condiciones relevantes de ruido/habla cuando la muestra lo permita.
- No afirmar representatividad estadística con 3–5 participantes sin justificarla.

## 7. Gestión de defectos

| Severidad | Criterio | Tratamiento |
|---|---|---|
| Crítica | Expone datos, entrega una recomendación prohibida, corrompe datos o impide el servicio | Bloquea piloto/despliegue; issue y mitigación inmediata |
| Alta | Resultado oficial incorrecto, entrega fallida no reflejada o incumplimiento del hardware mínimo | Bloquea el gate relacionado |
| Media | Degradación con workaround o métrica incompleta sin daño inmediato | Priorizar antes de la siguiente baseline |
| Baja | Presentación, texto o deuda sin impacto material actual | Backlog trazable |

La severidad y el cierre deben respaldarse con evidencia. “No reproducido” no significa “resuelto”.

## 8. Responsabilidades

| Actividad | Responsable operativo conocido | Aprobación |
|---|---|---|
| Ejecutar tests, lint, types y benchmarks | Liderazgo técnico | Pendiente definir revisor independiente |
| Validar utilidad y coordinar piloto | Product Owner | Pendiente acta de inicio |
| Revisar fuentes y documentación | Equipo de desarrollo/documentación | Pendiente según entregable |
| Aceptar riesgo legal o de privacidad | **No asignado** | Requiere autoridad y revisión profesional |
| Aceptar calidad de piloto | **No formalizado** | No inferir de un merge o demo |

## 9. Evidencia mínima por entrega

Cada entrega candidata debe conservar:

1. SHA y estado limpio del worktree;
2. issue y PR asociados;
3. comandos y resultados completos;
4. entorno de la medición;
5. fallos, skips y advertencias;
6. evidencia de rollback o limitación conocida;
7. responsable y decisión de aceptación reales.

---

**Próxima actualización:** después de corregir la suite global y ejecutar Q2–Q4
