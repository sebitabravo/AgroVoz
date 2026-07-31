# Acta de constitución del proyecto AgroVoz

## Estado del documento

Esta acta reconstruye la definición vigente del proyecto a partir de evidencia
trazable. No reemplaza una aprobación académica o institucional firmada: el
repositorio no registra patrocinador, fecha de autorización, presupuesto
aprobado ni firmas. Esos datos quedan explícitamente pendientes.

El estado funcional se toma de [AGENTS.md](../../AGENTS.md), fuente normativa
del proyecto. La [Discussion #137][d137] se conserva como antecedente, pero su
descripción de AgroVoz como producto temprano está desactualizada: las fases
00–06 ya fueron ejecutadas y el producto se encuentra implementado y
desplegado.

## Identificación

| Campo | Definición verificable |
|---|---|
| Nombre | AgroVoz |
| Propósito | Reducir la asimetría de información que enfrentan pequeños agricultores chilenos al negociar, entregando datos oficiales por voz o texto mediante WhatsApp |
| Contexto académico | Proyecto de Ingeniería en Informática de INACAP Temuco y participante de Desafío Crea INACAP 2026 |
| Estado del producto | Implementado y en operación; en endurecimiento operativo previo al piloto |
| Próximo hito | Piloto planificado con 3–5 productores de Traiguén durante cuatro semanas |
| Patrocinador formal | No consta en la evidencia revisada |
| Autorización institucional del piloto | Pendiente; no se presume compromiso de INDAP o PRODESAL |
| Presupuesto aprobado | No consta; existen restricciones de costo técnico, no una línea base presupuestaria aprobada |

## Justificación

La propuesta atiende a agricultores que pueden enfrentar barreras de
alfabetización digital, conectividad y acceso oportuno a información de
mercado. WhatsApp actúa como interfaz existente: el usuario no instala una
aplicación ni adquiere sensores. El sistema recibe audio o texto y responde en
el mismo medio con datos de ODEPA y OpenMeteo.

El beneficio esperado es que el productor disponga de una referencia oficial
cuando consulta precios, clima o cálculos derivados. AgroVoz informa; no
recomienda qué sembrar, cuándo vender ni qué tratamiento aplicar.

## Objetivos y criterios de éxito

| Objetivo | Criterio verificable | Estado |
|---|---|---|
| Entregar consultas por WhatsApp | Audio → transcripción → datos → respuesta hablada; texto → datos → respuesta escrita | Ejecutado en el producto |
| Cubrir datos agrícolas oficiales | Catálogo ODEPA de 79 productos y 15 mercados, más clima actual e histórico | Ejecutado según la fuente normativa |
| Mantener latencia útil | Menos de 15 segundos end-to-end, incluido el piso de 1 vCPU y 6 GB RAM | Pendiente de validación reproducible en [#215][i215] |
| Validar reconocimiento rural | WER menor a 15% en una muestra del piloto de Traiguén | Pendiente; requiere audios consentidos del piloto |
| Validar uso real | Piloto de 3–5 productores durante cuatro semanas | Planificado, no se registra como ejecutado |
| Proteger datos personales | Audio temporal eliminado antes de 24 horas, transcripciones minimizadas/seudonimizadas y consentimiento explícito donde corresponda | Controles técnicos implementados; auditoría formal pre-escalamiento pendiente |
| Verificar el canal real | Prueba E2E con Open-WA autenticado y envío/recepción por WhatsApp | Nueva validación pendiente en [#214][i214] |

## Alcance de alto nivel

### Incluido

- Entrada de voz y texto mediante WhatsApp.
- Transcripción local con Whisper y síntesis local con Piper.
- LLM local cuantizado con herramientas permitidas explícitamente.
- Precios actuales e históricos de ODEPA, cálculos determinísticos y clima de
  OpenMeteo.
- Alertas proactivas de precio y clima con consentimiento y límites de envío.
- Persistencia SQLite, preferencias por número de WhatsApp e historial con
  opt-in.
- Landing, demo interactiva y dashboard administrativo.
- Despliegue en VPS mediante Docker Compose y Dokploy.
- Instrumentación, pruebas automatizadas y kit documental para el piloto.

### Excluido

- Recomendaciones o diagnósticos agronómicos.
- Aplicación móvil nativa y dashboard para agricultores.
- Sensores o dispositivos IoT.
- Pagos integrados.
- Idiomas distintos del español chileno.
- Sustitución de WhatsApp como canal principal.
- Servidor de base de datos separado de SQLite.

El detalle y los criterios de aceptación se encuentran en
[Alcance y EDT](./03-alcance-edt.md).

## Entregables principales

| Entregable | Estado comprobable |
|---|---|
| Pipeline de consultas de voz y texto | Ejecutado |
| Integraciones ODEPA, OpenMeteo y Open-WA | Ejecutadas; validación E2E autenticada pendiente |
| Catálogo y herramientas de consulta | Ejecutado |
| Alertas, historial consentido y estado conversacional | Implementados localmente; historial y estado permanecen apagados por defecto |
| Landing, demo y dashboard admin | Ejecutados |
| Infraestructura reproducible de desarrollo y producción | Ejecutada |
| Kit del piloto | Preparado; uso en terreno pendiente |
| Evaluación WER rural | Pendiente del piloto |
| Validación de rendimiento en hardware mínimo | Pendiente |
| Documentación PMBOK de integración y alcance | Cubierta por este conjunto documental |

## Equipo y responsabilidad conocida

| Integrante | Responsabilidad registrada |
|---|---|
| Sebastián Bravo | Liderazgo técnico: backend, LLM, Tool Calling, Open-WA y arquitectura |
| Francisco Fernández | Product Owner: investigación, pitch y enlace con productores de Traiguén |
| Matías Atuán | Apoyo técnico, testing, documentación y validación de fuentes |

La tabla describe responsabilidades declaradas en `AGENTS.md`; no atribuye
horas, aprobaciones ni contribuciones adicionales.

## Interesados identificados

| Interesado | Relación comprobable | Compromiso |
|---|---|---|
| Equipo AgroVoz | Construcción y validación del producto | En ejecución |
| INACAP Temuco | Contexto académico y de competencia | Registrado; aprobación formal de esta acta no consta |
| Productores de Traiguén | Usuarios objetivo del piloto | Participación planificada; no se presume reclutamiento completado |
| INDAP / PRODESAL Araucanía | Fuentes de contexto y potencial contraparte institucional | Contacto y validación formal pendientes |
| ODEPA | Fuente pública de precios | Integración técnica ejecutada; no implica alianza |
| OpenMeteo | Fuente externa de clima | Integración técnica ejecutada; no implica alianza |

## Restricciones y supuestos

- Stack 100% open-source y sin APIs pagas para el flujo principal.
- Ejecución síncrona, SQLite y ausencia de Celery/Redis.
- Piso operativo obligatorio de 1 vCPU y 6 GB RAM.
- VPS objetivo Hetzner CX43; su precio indicado en `AGENTS.md` es una
  restricción técnica de referencia, no un presupuesto PMBOK aprobado.
- Número de WhatsApp como identidad; no existe autenticación de agricultores.
- Disponibilidad de WhatsApp/Open-WA, ODEPA y OpenMeteo como dependencias
  operativas.
- El piloto debe aportar la evidencia rural que hoy no puede sustituirse con
  tests automatizados.

## Riesgos iniciales

| Riesgo | Respuesta vigente |
|---|---|
| Desconexión o cambio de Open-WA | Monitoreo, fallback operacional evaluado y validación real pendiente |
| Fallo o cambio de ODEPA | Cache SQLite, sincronización diaria, fallback de descarga y alerta de datos obsoletos |
| WER rural superior al objetivo | Prompt de dominio, dataset consentido y evaluación durante el piloto |
| Latencia o falta de memoria en hardware mínimo | Fast paths, modelo cuantizado y prueba obligatoria en [#215][i215] |
| Fallo nativo del runtime LLM | Aislamiento del proceso de inferencia y fallback determinístico |
| Tratamiento inadecuado de datos personales | Opt-in, minimización/seudonimización, retención limitada y auditoría legal pre-escalamiento |
| Interpretación como recomendación | Respuestas limitadas a datos y exclusión expresa de consejo agronómico |

## Autoridad y control de cambios

Esta acta no otorga autoridad contractual ni financiera. Los cambios se
proponen mediante issues, se implementan en branches y se integran mediante
pull requests revisables. Toda modificación de stack, restricción o
arquitectura debe reconciliar `AGENTS.md` y
[ARCHITECTURE.md](../ARCHITECTURE.md). El mecanismo se desarrolla en el
[Plan de dirección](./02-plan-direccion.md).

## Formalización pendiente

Antes de presentar esta acta como documento aprobado deben completarse, sin
inferirlos:

- patrocinador y autoridad de aprobación;
- fecha de emisión y versión aprobada;
- firmas o mecanismo equivalente de aceptación;
- presupuesto y tolerancias autorizadas, si la evaluación los exige;
- autorización y participantes efectivos del piloto;
- criterios académicos exigidos por INACAP para el cierre.

## Evidencia reproducible

```bash
# Estado normativo y estructura del producto
sed -n '1,260p' AGENTS.md
git ls-files 'backend/app/**/*.py'
git ls-files 'backend/tests/test_*.py'

# Estado de las iniciativas y validaciones abiertas
gh issue view 214 --repo sebitabravo/AgroVoz
gh issue view 215 --repo sebitabravo/AgroVoz

# Antecedente histórico y auditoría posterior
gh api graphql -f owner=sebitabravo -f name=AgroVoz \
  -F number=137 \
  -f query='query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){discussion(number:$number){title body comments(first:100){nodes{body}}}}}'
```

[d137]: https://github.com/sebitabravo/AgroVoz/discussions/137
[i214]: https://github.com/sebitabravo/AgroVoz/issues/214
[i215]: https://github.com/sebitabravo/AgroVoz/issues/215
