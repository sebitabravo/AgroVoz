# Plan de Control Integrado de Cambios

> **Versión:** 0.1 — procedimiento propuesto
> **Fecha de corte:** 29 de julio de 2026
> **Estado:** flujo técnico parcialmente implementado; autoridad de aprobación no formalizada

## 1. Objetivo

Este plan controla cambios de alcance, cronograma, costo, calidad, datos, arquitectura y operación.
No reemplaza los hard constraints de `AGENTS.md`: una solicitud no puede autorizar por sí sola una
app nativa, recomendaciones agronómicas sin fuente citada, servicios pagos obligatorios, otra base de datos o un
despliegue que no funcione en 1 vCPU / 4 GB.

## 2. Controles definidos y brechas de evidencia

| Control | Evidencia al corte | Estado |
|---|---|---|
| Flujo issue-first | Convención obligatoria en `AGENTS.md`; 100 issues consultados | Definido como práctica; cumplimiento por cambio se verifica en PR |
| Branch y PR por objetivo | Reglas `feature/*`, `fix/*` y 1 PR = 1 cambio | Definido |
| Plantilla de PR | Secciones de resumen, tipo, issue, módulos, pruebas y riesgos exigidas | Definido; completar sigue siendo responsabilidad del autor |
| Checks CI | Backend tests, E2E, lint/types, landing y Compose aparecen en PR observados | Definido/observado en PR; no prueba ejecución de todos los cambios |
| Checks de política | Referencia a issue, `status:approved` y label `mod:*` | Definido/observado; PR abiertos muestran fallos detectados |
| Historial | Conventional Commits y squash; 89 PR mergeados consultados | Evidencia de uso, no de aprobación independiente |
| Revisión humana | `reviewDecision=REVIEW_REQUIRED` en PR consultados | Requerida, pero no se verificó una matriz de autoridad |
| CCB o comité de cambios | Sin evidencia recogida en esta revisión | **Pendiente** |
| Autorización de despliegue | Dokploy/producción declarados en `AGENTS.md` | Procedimiento y firmante no formalizados aquí |
| Revisión legal externa | Requerida para cambios de datos y piloto | **Pendiente** |

Los fallos de policy checks en PR abiertos son evidencia de que el control puede detectar
incumplimientos. No se deben interpretar como PR aprobados ni corregir omitiendo el gate. No hay
evidencia suficiente para declarar formalizados un CCB, una autoridad de aprobación, cadencias de
revisión o un checklist operativo; todos quedan pendientes.

## 3. Clasificación de cambios

| Clase | Ejemplos | Tratamiento mínimo |
|---|---|---|
| Estándar reversible | Texto, test o corrección acotada sin alterar contratos | Issue, cambio pequeño, tests focales, PR y CI |
| Producto | Nuevo intent, tool, alerta, métrica o cambio de experiencia | Análisis de alcance, aceptación del Product Owner y regresión |
| Técnico sensible | Auth, webhook, migración, DB, deploy, modelos, backups o concurrencia | Revisión técnica, rollback probado y evidencia de seguridad |
| Datos/legal | Consentimiento, retención, identificadores, transferencias o nuevas finalidades | Revisión de privacidad y profesional cuando corresponda; no autoaprobar |
| Constraint/arquitectura | Stack, hardware mínimo, procesamiento síncrono o alcance prohibido | ADR y actualización de fuente de verdad; aprobación explícita |
| Emergencia | Incidente activo de seguridad o indisponibilidad | Cambio mínimo y reversible; trazabilidad y revisión posterior obligatorias |

Que un cambio sea de configuración no lo convierte en estándar. Activar MCP, historial, estado,
demo, fallback externo o una nueva integración cambia la superficie de riesgo.

## 4. Flujo de una solicitud

### 4.1 Registrar

Crear un issue con:

- problema y evidencia;
- resultado esperado y fuera de alcance;
- hard constraints afectados;
- módulos, datos y terceros involucrados;
- impacto preliminar en plazo, costo, calidad y riesgo;
- pruebas y evidencia de aceptación;
- rollback o forma de desactivar;
- responsable propuesto.

No incluir secretos, números de teléfono, audios ni consultas reales.

### 4.2 Analizar

El responsable técnico prepara alternativas y consecuencias. Si faltan datos, el cambio queda
**pendiente de información**; no se inventa una estimación.

La evaluación debe responder:

1. ¿cambia una baseline aprobada?
2. ¿toca un hard constraint?
3. ¿crea una finalidad o dato nuevo?
4. ¿afecta 1 vCPU / 4 GB o el límite de 15 s?
5. ¿requiere migración, backup o recuperación?
6. ¿qué tests y mediciones demuestran el resultado?
7. ¿cómo se revierte sin reescribir historia compartida?

### 4.3 Decidir

La autoridad todavía no está formalizada. Esta matriz es **propuesta**, no asigna personas reales y
no prueba aprobaciones pasadas. Hasta contar con una designación documentada, cada decisión queda
pendiente de autoridad y no puede presentarse como aprobación institucional:

| Impacto | Revisión necesaria | Decisión requerida |
|---|---|---|
| Estándar reversible | Revisión técnica distinta del autor, si está disponible | Autoridad por formalizar; registrar decisión y evidencia |
| Producto/piloto | Revisión técnica y de producto | Autoridad por formalizar; registrar aceptación y evidencia |
| Técnico sensible | Revisión técnica, operación y seguridad | Autoridad por formalizar; no autoaprobar |
| Datos/legal | Revisión técnica, privacidad y profesional externo cuando aplique | Responsable por designar; no autoaprobar |
| Constraint/arquitectura | Revisión técnica, producto y documentación | Autoridad por formalizar; registrar decisión |
| Emergencia | Contención por la persona disponible | Aceptación posterior por la autoridad correspondiente, aún no designada |

Nadie debe figurar como aprobador por defecto solo por participar en el proyecto. Nombre, autoridad,
fecha y decisión deben quedar registrados por la persona real cuando la formalización exista.

### 4.4 Implementar y verificar

- Crear branch `feature/*` o `fix/*`.
- Hacer commits atómicos con Conventional Commits.
- Mantener cambios pequeños y reversibles.
- Ejecutar tests focales y regresión proporcional al riesgo.
- Ejecutar `ruff`, `mypy`, build o smoke según módulos afectados.
- Actualizar documentación exigida por `docs-alignment`.
- Adjuntar resultados completos, incluidos fallos, skips y advertencias.

No usar `--no-verify`, force-push ni reescritura destructiva para hacer pasar el cambio.

### 4.5 Revisar, integrar y cerrar

- Abrir PR con la plantilla completa y issue vinculado.
- Resolver checks de CI y de política sin desactivarlos.
- Obtener la decisión requerida para la clase de cambio.
- Integrar por squash cuando corresponda.
- Registrar despliegue, smoke y rollback si hubo cambio operativo.
- Cerrar el issue solo con evidencia del resultado, no por haber escrito código.

## 5. Gates por impacto

| Impacto | Evidencia mínima antes de aprobar |
|---|---|
| Alcance | Criterios de aceptación y no-objetivos |
| Cronograma | Hitos afectados; nueva fecha solo si fue aprobada |
| Costo | Diferencia estimada y supuesto; medición posterior |
| Calidad | Tests, lint/types, benchmark o WER según corresponda |
| Seguridad | Threat review, logs saneados, auth y manejo de secretos |
| Privacidad | Finalidad, base propuesta, campos, retención, borrado y terceros |
| Operación | Backup, migración, smoke, monitoreo y rollback |
| Documentación | Fuente de verdad y material de piloto alineados |

Una aprobación técnica no sustituye aceptación legal, de producto o de terreno.

## 6. Configuración y feature gates

El snapshot local mostró `MCP_ENABLED=false`, `USE_CONVERSATION_STATE=false`,
`CONSULTATION_HISTORY_ENABLED=false` y `DEMO_ENDPOINT_ENABLED=false`.

Para activar cualquiera:

1. issue específico con finalidad y usuarios;
2. inventario de datos y permisos;
3. pruebas de autorización, abuso y rollback;
4. actualización de configuración de ejemplo sin secretos;
5. aprobación correspondiente;
6. activación controlada y evidencia del smoke;
7. monitoreo y criterio para volver a `false`.

El valor local no prueba el valor del VPS. La evidencia de producción debe mostrar nombres de
variables y estado, nunca claves.

## 7. Rollback y cambios de emergencia

| Tipo | Estrategia |
|---|---|
| Código | Revertir mediante un commit nuevo y PR; nunca reset destructivo o force-push |
| Feature gate | Volver al estado seguro documentado y verificar que el flujo queda cerrado |
| Migración SQLite | Backup previo, plan de compatibilidad y corrección forward/downgrade revisada |
| Deploy/Compose | Conservar artefacto anterior, restaurar versión y ejecutar smoke |
| Datos | Detener escritura afectada, preservar evidencia mínima y ejecutar procedimiento aprobado |

Estas estrategias están definidas como requisito; no se afirma que todas hayan sido ensayadas.

En emergencia:

- priorizar contención y reversibilidad;
- no copiar datos sensibles al issue o chat;
- no omitir controles que protegen integridad o secretos;
- registrar la decisión y evidencia tan pronto como sea seguro;
- realizar revisión posterior y crear acciones preventivas.

## 8. Registro de decisión

Cada cambio aprobado debe conservar:

| Campo | Contenido |
|---|---|
| ID | Issue/solicitud |
| Versión | Baseline, release o configuración afectada |
| Solicitante | Persona real |
| Clase | Estándar, producto, sensible, datos/legal, arquitectura o emergencia |
| Motivo | Problema y evidencia |
| Alternativas | Incluida no hacer el cambio |
| Impactos | Alcance, plazo, costo, calidad, riesgo y datos |
| Decisión | Aprobar, rechazar, pedir información o posponer |
| Decisor y fecha | Persona con autoridad real |
| Evidencia | PR, checks, pruebas, benchmark, migración y smoke |
| Rollback | Procedimiento y resultado si se ejecutó |

## 9. Métricas futuras del proceso

Se propone medir:

- solicitudes por clase;
- tiempo desde registro hasta decisión;
- cambios rechazados o devueltos por falta de evidencia;
- fallos posteriores, rollback e incidentes;
- cambios de emergencia;
- requisitos reabiertos.

No hay una serie histórica validada en esta revisión. Los 100 issues y 103 PR son conteos de
artefactos, no métricas de eficacia del control de cambios.

## 10. Comandos de auditoría

```bash
git log --oneline --decorate -n 30
git log origin/main..HEAD --oneline
git status --short
gh issue list --state all --limit 200
gh pr list --state all --limit 200
gh run list --limit 12
cd backend && uv run pytest tests/ -q --tb=short
cd backend && uv run ruff check app/
cd backend && uv run mypy app/
```

La salida debe adjuntarse con fecha y SHA, limpiando rutas o valores que puedan contener datos
sensibles.

---

**Próxima revisión:** al formalizar autoridad de cambios y la primera baseline prospectiva
