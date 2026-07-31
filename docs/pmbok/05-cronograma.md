# Plan de Gestión del Cronograma

> **Versión:** 0.1 — borrador de control
> **Fecha de corte:** 29 de julio de 2026
> **Estado:** reconstrucción verificable del trabajo realizado y propuesta de control futuro
> **Aprobación de línea base:** pendiente

## 1. Propósito y criterio de evidencia

Este plan ordena el cronograma de AgroVoz sin convertir el historial en una línea base retroactiva.
Los commits, issues y pull requests prueban que hubo actividad en una fecha; no prueban que esa fecha
hubiera sido comprometida o aprobada de antemano.

Se usan cuatro estados:

- **Medido:** existe un comando y resultado reproducible en la fecha de corte.
- **Declarado:** consta en `AGENTS.md`, pero no fue vuelto a medir en esta revisión.
- **Planificado:** trabajo futuro acordado como objetivo, todavía sin resultado.
- **Pendiente:** falta fecha, responsable, aprobación o evidencia de cierre.

## 2. Snapshot reproducible

| Fuente | Resultado al corte | Qué demuestra | Qué no demuestra |
|---|---|---|---|
| Git local (`HEAD`) | 112 commits; commit raíz del 16-06-2026 y último commit de `HEAD` del 26-07-2026 | Ventana de historia disponible y secuencia de cambios confirmados | Inicio real del proyecto ni fechas comprometidas |
| Git por mes | 34 commits en junio y 78 en julio de 2026 | Concentración de actividad registrada | Esfuerzo, horas ni avance porcentual |
| GitHub Issues | 100 issues consultados: 86 cerrados y 14 abiertos | Flujo de trabajo y cierres registrados | Aceptación del usuario final |
| GitHub Pull Requests | 103 PR consultados: 89 mergeados, 4 abiertos y 10 cerrados sin merge | Integración de cambios por PR | Que cada merge haya cumplido una línea base de plazo |
| Worktree local | 143 entradas en `git status --porcelain` | El snapshot contiene trabajo aún no confirmado | Estado de `main` desplegado |
| Pytest, suite completa | 1.664 tests recolectados: 1.639 aprobados y 25 omitidos | Tamaño y regresión local del worktree | Calidad de terreno ni cumplimiento del cronograma |

Los conteos de GitHub son una fotografía de la consulta del 29-07-2026 y pueden cambiar. Los conteos
del worktree no deben usarse como evidencia de una entrega hasta quedar en un commit y PR trazables.

## 3. Reconstrucción histórica, no línea base

`AGENTS.md` declara que las fases 00–06 fueron completadas entre mayo y julio de 2026. La historia
Git disponible en este checkout comienza el 16 de junio, por lo que no permite asignar fechas
verificadas a cada fase ni confirmar actividad de mayo.

| Tramo | Estado documental | Evidencia disponible | Tratamiento en este plan |
|---|---|---|---|
| Fases 00–06: inicialización, backend, voz, Open-WA, landing, admin y despliegue | **Declarado como completado** | Tabla de fases de `AGENTS.md`; commits entre junio y julio | Hito histórico, sin fechas individuales ni varianza |
| Endurecimiento del producto | **Medido en Git** | Commits del 23 al 26 de julio sobre tests, latencia, historial, MCP, texto y alertas | Trabajo realizado; no se le asigna baseline retroactiva |
| Piloto con 3–5 productores en Traiguén | **Planificado** | Objetivo en `AGENTS.md` | No iniciado hasta contar con evidencia de onboarding y uso |
| Validación institucional PRODESAL/INDAP | **Planificado** | Objetivo en `AGENTS.md` | Sin fecha ni aceptación registradas |

No se calculan SPI, SV, valor ganado ni porcentaje de atraso: falta una línea base aprobada con
fechas y presupuesto temporal contra la cual comparar.

## 4. Hitos futuros y dependencias

Las fechas y duraciones permanecen pendientes hasta que el equipo asigne responsables y capacidad.
La duración de cuatro semanas del piloto sí es un objetivo ya declarado.

| ID | Hito o paquete de trabajo | Estado | Fecha/duración | Dependencias | Evidencia de salida |
|---|---|---|---|---|---|
| C0 | Cerrar bloqueos legales y de privacidad previos al piloto | Pendiente | Por definir | Responsable del tratamiento, revisión profesional y controles técnicos | Documento aprobado y hallazgos críticos cerrados |
| C1 | Endurecer operación del canal y recuperación | Planificado | Por definir | Sesión Open-WA, VPS, backups, monitoreo y smoke test | Prueba fechada de envío/recepción y procedimiento de recuperación |
| C2 | Validar latencia en 1 vCPU / 6 GB RAM | Planificado | Por definir | Build candidato, modelos locales y protocolo repetible | Distribución de latencia E2E y evidencia de umbral menor a 15 s |
| C3 | Establecer baseline WER rural | Planificado | Por definir | Muestras consentidas de Traiguén y script de evaluación | Dataset identificado, transcripciones de referencia y resultado WER |
| C4 | Revisión de preparación y decisión de inicio | Pendiente | Por definir | C0–C3 y kit de piloto | Acta explícita de decisión, alcance y responsables |
| C5 | Ejecutar piloto en Traiguén | Planificado | 4 semanas | C4 aprobado; 3–5 participantes incorporados válidamente | Bitácora real de uso, incidentes y retiros, sin completar por anticipado |
| C6 | Analizar resultados y decidir siguiente iteración | Planificado | Por definir | C5 finalizado | Informe con resultados medidos, limitaciones y decisión registrada |
| C7 | Validación institucional | Planificado | Por definir | Evidencia de C5–C6 | Registro de contacto y respuesta real de PRODESAL/INDAP |

La ruta crítica candidata es `C0/C1/C2/C3 → C4 → C5 → C6`. Es una hipótesis de planificación:
solo será una ruta crítica formal cuando existan duraciones, dependencias y calendario aprobados.

## 5. Reglas para aprobar la primera línea base

La línea base prospectiva debe incluir:

1. versión y fecha de aprobación;
2. responsable de cada hito y reemplazo operativo;
3. inicio y término comprometidos;
4. dependencias y capacidad disponible;
5. calendario de trabajo y restricciones académicas;
6. criterio de aceptación y enlace a su evidencia;
7. reserva explícita, si se aprueba alguna;
8. firma o registro de quienes realmente tienen autoridad.

Hasta entonces, mover una fecha futura no constituye formalmente una desviación. Sí debe quedar
registrado como cambio de planificación.

## 6. Seguimiento y control

| Control | Frecuencia propuesta | Registro | Estado actual |
|---|---|---|---|
| Revisar hitos, bloqueos y dependencias | Semanal durante preparación y piloto | Issue o minuta fechada | Pendiente de adopción |
| Registrar inicio y término reales | Al cambiar el estado de un hito | Issue/PR/evidencia operativa | Parcial: GitHub registra desarrollo, no todo el trabajo de terreno |
| Comparar contra baseline | Semanal, después de aprobarla | Tabla de variación | No aplicable todavía |
| Escalar bloqueo de ruta crítica | En cuanto se identifique | Issue con impacto y decisión | Flujo issue-first disponible |
| Rebaselinar | Solo mediante control de cambios | Solicitud aprobada, versión y motivo | Procedimiento definido en `11-control-cambios.md`; aprobación pendiente |

Un issue cerrado o un PR mergeado no cierra por sí solo un hito de piloto. Debe adjuntarse la
evidencia definida en la tabla de hitos.

## 7. Comandos de verificación

Ejecutar desde la raíz del repositorio:

```bash
git rev-list --count HEAD
git rev-list --max-parents=0 HEAD
git log -1 --date=short --format='%ad %h %s'
git log --date=format:'%Y-%m' --format='%ad' | sort | uniq -c
git shortlog -sn HEAD
git status --porcelain | wc -l
gh issue list --state all --limit 200 --json number,state,createdAt,closedAt
gh pr list --state all --limit 200 --json number,state,mergedAt
cd backend && uv run pytest tests/ --collect-only -q
```

Los resultados deben guardarse con fecha, SHA y estado del worktree. No se deben copiar tokens,
variables de entorno ni credenciales a la evidencia.

---

**Próxima actualización:** al aprobar responsables y fechas de C0–C4
