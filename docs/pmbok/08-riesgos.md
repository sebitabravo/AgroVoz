# Plan de Gestión de Riesgos

> **Versión:** 0.1 — registro inicial
> **Fecha de corte (snapshot histórico):** 29 de julio de 2026
> **Estado:** evaluación técnica preliminar; propietarios y aceptación formal pendientes

> **Alcance del snapshot:** la evidencia y los conteos de esta versión son históricos y no
> representan el estado actual de `HEAD`, `main` ni producción. Los claims no reproducidos mediante
> una nueva ejecución fechada quedan **pendientes**; una feature gate apagada no los cierra.

## 1. Propósito y límites

Este plan registra amenazas reales para el próximo hito de AgroVoz. No declara que una respuesta
esté implementada por aparecer en la tabla. Cada fila distingue la condición observada, el evento
incierto, su impacto y la acción pendiente.

No se estiman porcentajes, pérdida monetaria ni fechas de ocurrencia sin datos. Las categorías
**alta**, **media** y **baja** son priorización cualitativa preliminar:

- **Probabilidad alta:** la condición está presente y el evento puede ocurrir durante el próximo hito.
- **Probabilidad media:** existe una ruta plausible, pero falta frecuencia observada.
- **Probabilidad baja:** requiere varias condiciones o hay controles preventivos verificables.
- **Impacto alto:** bloquea el servicio/piloto, expone datos o viola un hard constraint.
- **Impacto medio:** degrada resultados o plazo con recuperación posible.
- **Impacto bajo:** efecto acotado sin pérdida material del objetivo.

Estas valoraciones deben revisarse con el equipo antes de convertirse en aceptación de riesgo.

## 2. Evidencia del snapshot histórico

| Evidencia | Resultado | Lectura de riesgo |
|---|---|---|
| Arquitectura y constraints en `AGENTS.md` | Open-WA, un VPS, SQLite, request síncrono y piso de 1 vCPU / 4 GB | Dependencias y cuellos de botella explícitos |
| Git | 112 commits; 93 bajo dos identidades del mantenedor principal y 19 de Dependabot | Conocimiento y entrega técnica concentrados |
| GitHub | 14 issues abiertos; 4 PR abiertos al consultar | Trabajo y dependencias pendientes |
| Pytest completo | 1.639 pasaron, 0 fallaron y 25 fueron omitidos | Gate de regresión local cubierto; integraciones externas no ejecutadas |
| Regresión seguridad + webhook | 49/49 pasaron en el orden antes afectado | Aislamiento del rate limiter verificado |
| Config local | Historial, estado, MCP y demo apagados; audio 24 h; Whisper `small` | Feature gates reducen exposición, pero no validan producción |
| Advertencias locales | Pepper por defecto y Open-WA sin clave en el entorno de desarrollo | Riesgo de configuración; no prueba el estado del VPS |
| GitHub Actions | CI técnico exitoso en ejecuciones observadas; policy checks fallidos en PR abiertos | El control detecta desviaciones, que aún no están resueltas |

No se incluyen valores de secretos ni identificadores de productores.

## 3. Registro priorizado

Las respuestas son propuestas o trabajo pendiente salvo que la columna **Control verificado** diga lo
contrario.

| ID | Riesgo: causa → evento → impacto | P | I | Señal o disparador | Respuesta prevista | Control verificado | Propietario |
|---|---|---:|---:|---|---|---|---|
| R01 | Open-WA depende del protocolo de WhatsApp Web → sesión, endpoint o cuenta deja de operar → se corta el canal principal | Alta | Alta | QR inválido, 401, desconexión o envío sin confirmación | Mitigar con healthcheck, smoke, recuperación documentada y alternativa evaluada | Spike de alternativa registrado en Git; recuperación E2E pendiente | Liderazgo técnico |
| R02 | Whisper `small` consume la mayor parte del camino de voz → hardware 1 vCPU supera 15 s → incumplimiento del piso soportado | Alta | Alta | Benchmark reproducible excede el objetivo | Mitigar perfilando etapas y validando cada cambio en 1 vCPU / 4 GB | `AGENTS.md` reporta ~11 s caliente; no medido en este corte | Liderazgo técnico |
| R03 | Responsable, base, retención y transferencia internacional requieren cierre → piloto trata datos sin respaldo suficiente → exposición legal y bloqueo | Alta | Alta | Llegar al go/no-go sin revisión y decisiones documentadas | Evitar inicio hasta cerrar hallazgos críticos y obtener revisión profesional | **PENDIENTE / BLOQUEADOR:** feature gates cerrados reducen exposición, pero no hay owner, autoridad ni fecha de aceptación formal | **Pendiente de designar; sin aceptación** |
| R04 | No existe WER medido con voz rural de Traiguén → reconocimiento falla en terreno → respuestas erróneas o abandono | Media | Alta | WER igual o mayor a 15% o errores sistemáticos de vocabulario | Mitigar con muestra consentida, referencia humana y análisis de errores | Prompt de dominio reportado; medición de piloto pendiente | Liderazgo técnico + piloto |
| R05 | Contribuciones técnicas Git están concentradas en un mantenedor → indisponibilidad o salida detiene cambios/recuperación → atraso y mayor MTTR | Alta | Alta | Incidente que otra persona no puede diagnosticar o desplegar | Mitigar con runbooks, revisión real, acceso de respaldo y ejercicios de recuperación | Equipo de tres declarado; independencia operativa no demostrada | Equipo, responsable por acordar |
| R06 | SQLite y procesamiento síncrono son constraints → consultas largas bloquean recursos o generan lock/timeout → degradación concurrente | Media | Alta | Locks, colas de requests, timeouts o latencia creciente | Mitigar con límites de concurrencia, transacciones cortas y pruebas de carga del piso | Tests unitarios; carga concurrente de piloto no medida | Liderazgo técnico |
| R07 | Backend, DB, sesión y modelos residen en un VPS → falla de host/disco o error de despliegue → pérdida de servicio o datos | Media | Alta | Healthcheck caído, disco lleno o restore necesario | Mitigar con backup cifrado, restore probado y rollback | VPS desplegado declarado; prueba de restore no aportada | Liderazgo técnico |
| R08 | ODEPA y OpenMeteo son fuentes externas → caída o datos stale → respuesta ausente/desactualizada | Media | Alta | Sync ODEPA stale por más de 3 días, timeout o schema inesperado | Mitigar con cache, alerta de stale, fallback permitido y fecha/fuente en respuesta | Alerta/fallback ODEPA reportados; smoke continuo pendiente | Liderazgo técnico |
| R09 | Retenciones y logs pueden contener datos seudonimizados → borrado incompleto o filtración → daño al titular y hallazgo legal | Alta | Alta | Solicitud de supresión que no cubre todas las copias o log sensible | Evitar contenido sensible en logs; definir TTL y borrado E2E | **PENDIENTE / BLOQUEADOR:** controles y tests reducen exposición, pero cobertura integral, owner, autoridad y fecha de aceptación no están acreditados | **Pendiente de designar; sin aceptación** |
| R10 | `success_rate` no equivale a entrega efectiva → panel informa éxito aunque falle LLM/envío → decisiones operativas incorrectas | Alta | Media | Diferencia entre intent clasificado y entrega confirmada | Mantener la definición de entrega efectiva y verificar monitoreo operativo | **Control implementado en código y regresión:** fórmula basada en entrega efectiva y test `test_success_rate_mide_entrega_no_intent`; monitoreo de producción pendiente | Liderazgo técnico + Product Owner |
| R11 | Piloto de 3–5 personas es pequeño y aún no ocurre → resultados se generalizan en exceso → decisión institucional débil | Media | Media | Informe usa porcentajes sin denominador o afirma representatividad | Mitigar reportando conteos, contexto y limitaciones; no extrapolar | Diseño de 4 semanas declarado; datos reales inexistentes | Product Owner |
| R12 | LLM puede interpretar o recomendar → se cruza el límite de solo datos → riesgo para el productor y el proyecto | Media | Alta | Respuesta usa “debería”, califica precio o prescribe una acción | Evitar con prompt, fast-path, whitelist, tests y revisión de muestra | Límites y tests implementados; monitoreo de campo pendiente | Liderazgo técnico |
| R13 | Costo unitario depende de usuarios y operación real → objetivo CLP 100–150 no se verifica → modelo económico inválido | Media | Media | Costo real por usuario supera el rango | Medir VPS, almacenamiento y soporte sobre usuarios activos | Costo VPS declarado; costo unitario no medido | Product Owner |
| R14 | Configuración o fallback puede apartarse del stack local → datos salen a un tercero o cambia el costo → incumplimiento de constraint | Media | Alta | Activación de proveedor externo o dependencia paga en candidato | Revisar config/dependencias y probar operación sin claves externas | Git registra un fallback externo histórico; estado de activación no auditado aquí | Liderazgo técnico |
| R15 | Checks o tests dependen de estado compartido → resultados cambian por orden → falsos rojos o falsos verdes | Baja | Media | Un caso pasa aislado y falla dentro de la suite | Mantener reset de todas las instancias y regresión del orden | Registro débil de instancias implementado; suite global y 49 casos focales verdes | Liderazgo técnico |

## 4. Problemas actuales versus riesgos

Un problema ya ocurrido no debe esconderse como evento incierto.

| ID | Tipo | Estado observado | Acción inmediata |
|---|---|---|---|
| I01 | Resuelto al corte histórico | La suite completa tenía 6 fallos en el worktree | 1.639 aprobados, 0 fallos y 25 omitidos tras las correcciones; vigencia actual pendiente |
| I02 | Resuelto al corte | El test ODEPA esperaba un método de logging distinto al saneamiento actual | Contrato alineado sin restaurar detalle sensible; 14/14 casos pasan |
| I03 | Resuelto al corte | Cinco tests webhook dependían del orden/estado de la suite | Todas las instancias del rate limiter se resetean; regresión 49/49 |
| I04 | Problema | Falta evidencia de revisión legal y responsable con autoridad | Mantener bloqueado el inicio del piloto |
| I05 | Problema | WER del piloto no existe porque el piloto no ha comenzado | No publicar precisión rural como resultado |

## 5. Respuestas y reservas

No hay una reserva de cronograma o costo aprobada. Tampoco hay evidencia de transferencia,
aceptación o cierre formal de los riesgos anteriores. En particular, **R03 y R09 permanecen
pendientes y bloqueadores**: no tienen owner designado ni autoridad/fecha de aceptación del riesgo
residual. No se inventan personas para completar esos campos.

Para cada riesgo prioritario se debe registrar:

1. propietario que acepta la responsabilidad;
2. acción preventiva con issue y criterio de cierre;
3. disparador observable;
4. contingencia que pueda ejecutarse;
5. riesgo residual después de probar el control;
6. autoridad que acepta el residual y fecha.

“Mitigado” exige evidencia. Una feature flag apagada reduce exposición, pero no cierra por sí sola
un riesgo de diseño, datos o activación futura.

## 6. Cadencia de revisión

| Momento | Acción |
|---|---|
| Semanal antes y durante el piloto | Revisar riesgos altos, problemas abiertos y señales |
| Antes de activar una feature gate | Revaluar privacidad, autorización, carga y rollback |
| Antes de la decisión C4 | Confirmar propietarios y cierre de bloqueos |
| Después de incidente o fallo de fuente | Registrar causa, impacto, respuesta y riesgo residual |
| Al cerrar el piloto | Comparar riesgos previstos con eventos reales, sin rellenar retrospectivamente |

La frecuencia es una propuesta de gestión, no evidencia de reuniones ya realizadas.

## 7. Comandos de verificación

```bash
git shortlog -sn HEAD
git log --date=short --format='%ad %h %s' -n 30
git status --porcelain
gh issue list --state all --limit 200
gh pr list --state all --limit 200
gh run list --limit 12
cd backend && uv run pytest tests/ -q --tb=short
cd backend && uv run ruff check app/
cd backend && uv run mypy app/
```

Las mediciones de latencia, WER, restore y piloto se adjuntarán solo después de ejecutarlas con SHA,
entorno y fecha.

---

**Próxima revisión:** antes de la decisión de inicio del piloto
