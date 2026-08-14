# Plan de Negocio — AgroVoz
## Tu voz tiene el precio justo

**Equipo:** Sebastián Bravo
**Institución:** INACAP Temuco, Ingeniería en Informática
**Origen:** Desafío Crea INACAP Estudiantes 2026
**Redacción original:** junio 2026
**Última revisión de fondo:** 26 de julio de 2026

> **Actualización 2026-08-14:** el proyecto no continuó en el Desafío Crea INACAP 2026 y es
> mantenido por una sola persona. Este plan de negocio queda como el registro de lo diseñado, no
> como un piloto, un equipo ni un financiamiento vigentes.

Documento segmentado por partes. Reemplaza al antiguo `AgroVoz_Informe_Completo.md`, que era un
archivo único de ~650 líneas en la raíz del repositorio.

## Por qué está segmentado así

Cada parte es un archivo independiente por dos razones:

1. **Se revisan y actualizan por separado.** La estructura de costos cambia cuando cambia el stack;
   el problema y el mercado no. En un archivo único, cualquier corrección obliga a leer todo.
2. **Alimenta directamente la documentación PMBOK** que pide INACAP. Cada parte declara a qué área
   PMBOK corresponde, así que armar el Acta de Constitución o la Línea Base de Costos es componer
   desde acá, no reescribir.

## Índice

| # | Documento | Contenido | Área PMBOK |
|---|---|---|---|
| 1 | [Resumen ejecutivo y objetivos](./01-resumen-ejecutivo.md) | Qué es, para quién, objetivos | Integración |
| 2 | [Problema y mercado](./02-problema-y-mercado.md) | Asimetría de información, 205.000 usuarios INDAP, cifras con fuente | Integración |
| 3 | [Propuesta de valor](./03-propuesta-de-valor.md) | Valor para el productor y para INDAP/PRODESAL, ODS | Alcance |
| 4 | [Solución y stack](./04-solucion-y-stack.md) | Arquitectura, tecnologías, diferenciales, competencia | Alcance |
| 5 | [Estado del proyecto](./05-estado-del-proyecto.md) | Qué está construido, qué cambió, qué falta | Integración / Cronograma |
| 6 | [Modelo de negocio](./06-modelo-de-negocio.md) | B2G, freemium, convenios privados | Costos |
| 7 | [Estructura de costos](./07-estructura-de-costos.md) | Costo real, capacidad, punto de equilibrio, riesgo Open-WA | Costos |
| 8 | [Escalamiento](./08-escalamiento.md) | Tres horizontes con condiciones habilitantes | Alcance / Riesgos |
| 9 | [Impacto esperado](./09-impacto.md) | Económico, social, territorial, ODS | Interesados |
| 10 | [Equipo](./10-equipo.md) | Integrantes, roles, dedicación | Recursos |
| 11 | [Políticas públicas](./11-politicas-publicas.md) | Alineación institucional | Interesados |
| — | [Referencias](./referencias.md) | Bibliografía y fuentes | Todas |

Documentos relacionados fuera de esta carpeta:

- [`docs/legal/`](../legal/) — política de privacidad y aviso de responsabilidad
- [`docs/pmbok/`](../pmbok/) — documentación de gestión de proyecto
- [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) — arquitectura técnica en detalle
- [`docs/historico/postulacion-crea-2026.md`](../historico/postulacion-crea-2026.md) — lo que se envió al Desafío Crea, congelado

## Advertencia sobre las cifras

Este plan mezcla tres tipos de números y **conviene no confundirlos**:

| Tipo | Ejemplo | Cómo tratarlo |
|---|---|---|
| **Verificado** | 1.197 tests, 79 productos ODEPA, costo fijo CLP 14.364/mes | Se puede afirmar |
| **De fuente oficial** | 205.000 usuarios INDAP, 40-60% del precio mayorista | Citar la fuente |
| **Supuesto sin validar** | 20 consultas/mes por agricultor, captura de 15-25% adicional | Decir que es hipótesis |

Los supuestos sin validar están marcados como tales en el texto. **El piloto de Traiguén existe
precisamente para convertirlos en medidos.** No presentarlos como hechos ante INDAP, un jurado o
un fondo: si piden el respaldo y no existe, se cae la credibilidad del resto.

Los dos supuestos críticos hoy:

1. **Consultas por agricultor al mes.** Todo el modelo financiero cuelga de ahí.
2. **Ingreso adicional que captura el productor.** Es la base de la propuesta de valor entera.
