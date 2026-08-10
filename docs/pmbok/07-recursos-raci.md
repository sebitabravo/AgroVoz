# Recursos y matriz RACI

**Proyecto:** AgroVoz  
**Estado del documento:** snapshot histórico de responsabilidades y propuesta futura no aprobada
**Fecha de corte (snapshot histórico):** 29 de julio de 2026
**Próximo hito:** piloto de validación con 3 a 5 productores de Traiguén durante 4 semanas

> **Alcance del snapshot:** las contribuciones, estados y asignaciones descritos al 29-07-2026 no
> constituyen una línea base vigente ni prueban capacidad operativa actual. Las responsabilidades
> futuras siguen **pendientes de confirmación formal**; no se agregan personas, dedicaciones ni
> aprobaciones que no estén respaldadas por un registro.

## 1. Propósito y criterio de honestidad

Este documento identifica los recursos necesarios y asigna responsabilidades sin reconstruir
retroactivamente una organización que no existió. Distingue:

- contribuciones descritas en la fuente de verdad del proyecto;
- responsabilidades propuestas para el piloto y la defensa;
- apoyos externos todavía pendientes de confirmar.

La matriz RACI es una herramienta de coordinación desde esa fecha de corte. No demuestra por sí
sola quién escribió código ni reemplaza la evidencia de commits, issues, pull requests, documentos
o registros de terreno. Los estados técnicos históricos y cualquier claim de operación actual quedan
pendientes hasta contar con una captura y evidencia fechadas.

## 2. Recursos humanos internos

| Integrante | Rol vigente | Contribución atribuible según la fuente de verdad | Responsabilidad próxima |
|---|---|---|---|
| Sebastián Bravo | Líder técnico | Backend, LLM, Tool Calling, integración Open-WA y arquitectura | Operación técnica del piloto, demo, monitoreo, corrección de incidentes y evidencia técnica |
| Francisco Fernández | Product Owner | Investigación, pitch y enlace con productores en Traiguén | Coordinación del piloto, relación con participantes, relato de problema/valor y contacto institucional |
| Matías Atuán | Desarrollo y apoyo técnico | Apoyo técnico, testing, documentación y validación de fuentes | Apoyo de QA, revisión documental, control de evidencia y validación de fuentes utilizadas |

### Regla de atribución

La descripción anterior no atribuye módulos, commits ni decisiones técnicas específicas a Francisco
o Matías. Cuando la defensa requiera demostrar autoría de software, se debe usar el historial real
del repositorio. El trabajo que no queda en Git —investigación, coordinación territorial, revisión
de fuentes o preparación de pitch— debe respaldarse con su artefacto correspondiente y no con una
afirmación genérica.

## 3. Recursos externos y condición de disponibilidad

| Recurso o actor | Uso previsto | Estado comprobable | Restricción |
|---|---|---|---|
| 3 a 5 productores de Traiguén | Participar voluntariamente en un piloto de 4 semanas | **Pendiente:** no se reportan selección, consentimiento ni resultados | Requiere invitación, información clara y consentimiento antes de recopilar datos |
| PRODESAL / extensionistas | Posible enlace territorial y orientación al productor | **Pendiente:** no existe acuerdo institucional acreditado en este documento | No presentarlos como socios, patrocinadores ni canal de soporte confirmado |
| INDAP Araucanía | Posible validación institucional y canal de escalamiento futuro | **Pendiente:** el contacto formal es un objetivo abierto | No usar logos, respaldo ni aprobación sin autorización verificable |
| Revisión jurídica especializada | Auditoría previa a escalar el tratamiento de datos | **Pendiente:** no hay validación legal formal declarada | El cumplimiento de la Ley 21.719 no debe darse por certificado |
| INACAP Temuco / Desafío Crea 2026 | Evaluación académica y de competencia | Contexto institucional del proyecto | Las reglas, fechas y formato de defensa deben confirmarse por el canal oficial |

## 4. Recursos técnicos

| Recurso | Función | Condición vigente |
|---|---|---|
| VPS Hetzner CX43, Ubuntu 24.04 | Ejecutar backend, modelos locales y gateway | Restricción de costo y capacidad definida en `AGENTS.md` |
| Hardware degradado de referencia: 1 vCPU / 4 GB RAM | Piso para validar funcionamiento y latencia | La evidencia de cada prueba debe adjuntarse; no basta ejecutar solo en el VPS |
| FastAPI, SQLite y Docker Compose | Backend, persistencia y despliegue | Stack vigente; no se contempla servidor de base de datos separado |
| Whisper, LLM cuantizado y Piper | Pipeline local de voz | Stack abierto; Whisper sigue siendo un cuello de botella conocido |
| Open-WA | Gateway de WhatsApp autohospedado | Dependencia operativa; no equivale a una API oficial contratada |
| ODEPA y OpenMeteo | Datos de precios y clima | Fuentes externas; no implican convenio institucional |
| GitHub y CI | Trazabilidad de código, cambios y calidad | La evidencia debe extraerse del repositorio al momento de la defensa |
| Kit documental del piloto | Onboarding, bitácora, check-in, métricas y consentimiento | Material preparado; su aplicación y resultados siguen pendientes |

No se planifican adquisiciones de APIs pagas. Cualquier cambio que incorpore un servicio pagado,
una base de datos separada o una nueva dependencia debe justificar costo, privacidad y efecto sobre
el piso de hardware.

## 5. Convenciones RACI

- **R — Responsable:** ejecuta el trabajo.
- **A — Aprobador:** responde por el resultado y toma la decisión final. Debe existir uno por fila.
- **C — Consultado:** aporta criterio antes de decidir o ejecutar.
- **I — Informado:** recibe el estado o el resultado.

Las asignaciones marcadas como **próximas** son un plan de trabajo, no evidencia de ejecución.

## 6. Matriz RACI

| Entregable o actividad | Sebastián | Francisco | Matías | Tercero externo | Momento / estado |
|---|---:|---:|---:|---:|---|
| Arquitectura, backend, LLM, Tool Calling y Open-WA | A/R | I | C | — | Histórico al corte; evidencia vigente pendiente |
| Integración técnica ODEPA y OpenMeteo | A/R | I | C | — | Histórico al corte; vigencia de datos pendiente |
| Infraestructura, despliegue y monitoreo | A/R | I | C | — | Declarado operativo al corte; operación actual no verificada |
| Testing automatizado y control de calidad técnico | A/R | I | C | — | Histórico al corte; evidencia vigente pendiente |
| Investigación del problema y propuesta de valor | C | A/R | C | — | Base del pitch; revisar fuentes antes de defender |
| Pitch y relato de producto | C | A/R | C | — | Próxima defensa |
| Validación de fuentes documentales | C | A | R | — | Continua; conservar referencia y fecha |
| Documentación técnica | A/R | I | C | — | Mantención continua |
| Documentación de negocio y gestión | C | A/R | R | — | Completar y revisar en equipo |
| Diseño operativo del piloto | C | A/R | C | Productores: C | Preparado; ejecución pendiente |
| Convocatoria y coordinación con participantes | I | A/R | C | Productores: C | Pendiente |
| Onboarding y consentimiento del piloto | C | A/R | C | Participante: C; decide libremente si consiente | Pendiente; decisión voluntaria individual |
| Soporte técnico durante el piloto | A/R | C | C | — | Pendiente |
| Check-in y recopilación de feedback | I | A/R | R | Productores: C | Pendiente; aplicar minimización de datos |
| Medición de WER y análisis técnico del piloto | A/R | I | C | Participantes: C | Pendiente; no existe resultado reportable |
| Análisis de resultados de campo | C | A/R | R | Participantes: C | Pendiente; no existe resultado reportable |
| Contacto formal con PRODESAL/INDAP | C | A/R | I | Institución: C; decide su participación | Pendiente; sin acuerdo vigente acreditado |
| Auditoría jurídica pre-escalamiento | C | A | C | Especialista: R | Pendiente; proveedor/persona no definido |
| Preparación y ejecución de la defensa | R técnico | A/R pitch | R evidencia | — | Próximo entregable |

## 7. Capacidad y reglas de asignación

No hay una dedicación horaria formal aprobada en las fuentes revisadas. Antes de iniciar el piloto,
el equipo debe confirmar por escrito:

1. disponibilidad semanal de cada integrante;
2. reemplazo operativo si el líder técnico no está disponible;
3. responsable de responder incidentes del piloto;
4. responsable de custodiar consentimientos y bitácoras;
5. responsable de consolidar la evidencia de defensa.

No se deben inventar porcentajes de dedicación ni horas ejecutadas. Si se incorporan, deben provenir
de una estimación aprobada o de un registro real.

## 8. Brechas de recursos

| Brecha | Impacto | Acción antes del siguiente hito | Responsable |
|---|---|---|---|
| Piloto aún no ejecutado | No hay evidencia de uso real ni resultados | Confirmar participantes, calendario y consentimiento | Francisco |
| WER rural aún no medido | No se puede afirmar precisión objetivo | Ejecutar protocolo sobre muestra consentida y conservar cálculo reproducible | Sebastián |
| Contacto institucional no acreditado | No existe validación externa | Realizar contacto formal y archivar respuesta, incluso si es negativa o no concluyente | Francisco |
| Auditoría jurídica pendiente | No se puede afirmar cumplimiento certificado | Obtener revisión antes de escalar | Francisco, con especialista externo |
| Concentración de conocimiento técnico | Riesgo de continuidad | Documentar operación y realizar una sesión de transferencia verificable | Sebastián |
| Evidencia no técnica dispersa | Dificulta demostrar contribuciones y decisiones | Asociar cada aporte a documento, minuta, material o registro fechado | Matías |

## 9. Criterio de aprobación

La matriz no se considera vigente para el piloto mientras los tres integrantes no confirmen las
responsabilidades próximas por escrito. La falta de confirmación no invalida las contribuciones
históricas descritas en `AGENTS.md`, pero obliga a presentar las asignaciones futuras como propuesta
pendiente y no como capacidad operativa disponible.

## 10. Fuentes y evidencia

- [`AGENTS.md`](../../AGENTS.md): equipo, roles, estado, stack, restricciones y próximo hito.
- [`docs/pmbok/README.md`](README.md): criterio de evidencia reproducible y advertencia sobre autoría.
- [`docs/negocio/10-equipo.md`](../negocio/10-equipo.md): fuente prevista para contrastar el equipo.
- [`docs/piloto/00-plan-de-pilotaje.md`](../piloto/00-plan-de-pilotaje.md): alcance operativo del piloto.
- Historial Git para autoría técnica: `git shortlog -sne --all`.

**Pendiente de validación interna:** contrastar esta RACI con los tres integrantes y registrar fecha
de aprobación o ajustes.
