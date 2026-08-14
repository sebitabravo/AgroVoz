# Acta de constitución del proyecto AgroVoz

## Estado del documento

Esta acta reconstruye la definición vigente del proyecto a partir de evidencia
trazable. No reemplaza una aprobación académica o institucional firmada: el
repositorio no registra patrocinador, fecha de autorización, presupuesto
aprobado ni firmas. Esos datos quedan explícitamente pendientes.

El estado funcional se toma de [AGENTS.md](../../AGENTS.md), fuente normativa
del proyecto. La [Discussion #137][d137] se conserva como antecedente, pero su
descripción de AgroVoz como producto temprano está desactualizada: las fases
00–06 tienen implementaciones y documentación en el checkout.

**Actualización 2026-08-14:** el proyecto no continuó en el Desafío Crea INACAP 2026. El piloto
de Traiguén y el respaldo institucional que lo sostenía (INDAP, PRODESAL) ya no existen; su kit
documental (`docs/piloto/`) se retiró del repositorio. AgroVoz es mantenido por una sola persona
(Sebastián), verificable con `git shortlog -sne --all`. Esta acta se reescribe para que cada
sección refleje esa realidad de forma consistente, en vez de mezclar el plan original con el
estado actual.

## Identificación

| Campo | Definición verificable |
|---|---|
| Nombre | AgroVoz |
| Propósito | Reducir la asimetría de información que enfrentan pequeños agricultores chilenos al negociar, entregando datos oficiales por voz o texto mediante WhatsApp |
| Contexto académico | Proyecto de Ingeniería en Informática de INACAP Temuco. Participó en el Desafío Crea INACAP 2026, que no continuó |
| Estado del producto | Implementado en el checkout; único deploy público vigente es la demo slim en Vercel — ver `docs/ARCHITECTURE.md` decisión 34 |
| Próximo hito | No hay piloto de campo planificado; sin respaldo institucional para uno |
| Patrocinador formal | No consta en la evidencia revisada |
| Presupuesto aprobado | No consta; existen restricciones de costo técnico, no una línea base presupuestaria aprobada |

## Justificación

La propuesta atiende a agricultores que pueden enfrentar barreras de
alfabetización digital, conectividad y acceso oportuno a información de
mercado. WhatsApp actúa como interfaz existente: el usuario no instala una
aplicación ni adquiere sensores. El sistema recibe audio o texto y responde en
el mismo medio con datos de ODEPA y OpenMeteo.

El beneficio esperado es que el productor disponga de una referencia oficial
cuando consulta precios, clima o cálculos derivados. AgroVoz informa con datos
oficiales; no improvisa consejos agronómicos: si existe una regla determinística
con fuente INIA/INDAP vigente, la verbaliza citando fuente y fecha.

## Objetivos y criterios de éxito

| Objetivo | Criterio verificable | Estado |
|---|---|---|
| Entregar consultas por WhatsApp | Audio → transcripción → datos → respuesta hablada; texto → datos → respuesta escrita | Ejecutado en el producto (solo local/Docker; no en el deploy público) |
| Cubrir datos agrícolas oficiales | Catálogo ODEPA declarado de 79 productos y 15 mercados, más clima actual e histórico | Conteo reproducible sobre un snapshot pendiente; no verificable solo con la documentación |
| Mantener latencia útil | Menos de 15 segundos end-to-end en el piso de 1 vCPU y 4 GB RAM | Benchmark pendiente en [#215][i215]; no lo sustituye el smoke CI |
| Proteger datos personales | Media temporal (audio/imagen) eliminada antes de 24 horas, transcripciones minimizadas/seudonimizadas y consentimiento explícito donde corresponda | Controles técnicos implementados; auditoría formal pre-escalamiento pendiente. Hoy no se procesan datos personales de terceros: no hay piloto ni WhatsApp en operación pública |
| Verificar el canal real | Prueba E2E con Open-WA autenticado y envío/recepción por WhatsApp | No aplica sin infraestructura propia — ver [#214][i214] |

Validar reconocimiento rural (WER) y validar uso real con productores eran objetivos del piloto de
Traiguén. Sin ese piloto, ninguno de los dos tiene una vía de ejecución hoy.

## Alcance de alto nivel

### Incluido

- Entrada de voz y texto mediante WhatsApp (solo demostrable en local/Docker).
- Transcripción local con Whisper y síntesis local con Piper.
- LLM local cuantizado con herramientas permitidas explícitamente; en el deploy público, OpenRouter con fallback a texto seguro.
- Precios actuales e históricos de ODEPA, cálculos determinísticos y clima de OpenMeteo.
- Alertas proactivas de precio y clima con consentimiento y límites de envío.
- Persistencia SQLite, preferencias por número de WhatsApp e historial con opt-in.
- Landing, demo interactiva pública (Vercel) y dashboard administrativo (solo local).

### Excluido

- Recomendaciones o diagnósticos agronómicos sin fuente citada.
- Aplicación móvil nativa y dashboard para agricultores.
- Sensores o dispositivos IoT.
- Pagos integrados.
- Idiomas distintos del español chileno.
- Sustitución de WhatsApp como canal principal (para el flujo completo, que hoy solo corre local).
- Servidor de base de datos separado de SQLite.
- Piloto de campo con productores reales: sin respaldo institucional, no está planificado.

El detalle y los criterios de aceptación se encuentran en
[Alcance y EDT](./03-alcance-edt.md).

## Entregables principales

| Entregable | Estado comprobable |
|---|---|
| Pipeline de consultas de voz y texto | Ejecutado; local/Docker |
| Integraciones ODEPA, OpenMeteo | Ejecutadas |
| Deploy público slim (Vercel) | Ejecutado — ver `docs/ARCHITECTURE.md` decisión 34 |
| Catálogo y herramientas de consulta | Whitelist implementada; cardinalidad del catálogo pendiente de conteo reproducible |
| Alertas, historial consentido y estado conversacional | Implementados localmente; historial y estado permanecen apagados por defecto |
| Landing, demo pública y dashboard admin | Ejecutados (dashboard admin solo local) |
| Infraestructura reproducible de desarrollo | Configurada/documentada |
| Documentación PMBOK de integración y alcance | Cubierta por este conjunto documental |

## Equipo y responsabilidad conocida

AgroVoz es mantenido por una sola persona.

| Integrante | Responsabilidad registrada |
|---|---|
| Sebastián Bravo | Diseño, backend, LLM, Tool Calling, arquitectura y producto |

La tabla describe responsabilidades declaradas en `AGENTS.md`; no atribuye
horas, aprobaciones ni contribuciones adicionales.

## Interesados identificados

| Interesado | Relación comprobable | Compromiso |
|---|---|---|
| Sebastián (único mantenedor) | Construcción y mantención del producto | En ejecución |
| INACAP Temuco | Contexto académico | Registrado; aprobación formal de esta acta no consta |
| ODEPA | Fuente pública de precios | Integración técnica ejecutada; no implica alianza |
| OpenMeteo | Fuente externa de clima | Integración técnica ejecutada; no implica alianza |
| Vercel / OpenRouter | Infraestructura del deploy público | Free tier; sin SLA ni contrato |

Productores de Traiguén, INDAP y PRODESAL eran interesados del piloto planeado; se retiraron de
este registro junto con `docs/piloto/` porque ese piloto no tiene respaldo institucional vigente.

## Restricciones y supuestos

- Stack 100% open-source y sin APIs pagas obligatorias para el flujo principal.
- Ejecución síncrona, SQLite y ausencia de Celery/Redis.
- Piso operativo de referencia: 1 vCPU y 4 GB RAM, no medido con benchmark reproducible.
- Número de WhatsApp como identidad; no existe autenticación de agricultores.
- Sin infraestructura propia (VPS/NAS): WhatsApp/Open-WA y el pipeline de voz completo solo
  corren en local/Docker, no en el deploy público.

## Riesgos iniciales

| Riesgo | Respuesta vigente |
|---|---|
| Desconexión o cambio de Open-WA | No aplica hoy: sin infraestructura propia, no está en operación |
| Fallo o cambio de ODEPA | Cache SQLite, sincronización diaria, fallback de descarga y alerta de datos obsoletos |
| Latencia o falta de memoria en hardware mínimo | Fast paths, modelo cuantizado y prueba obligatoria en [#215][i215] |
| Fallo nativo del runtime LLM | Aislamiento del proceso de inferencia y fallback determinístico (texto seguro, sin datos inventados) |
| Tratamiento inadecuado de datos personales | Opt-in, minimización/seudonimización y retención limitada. Hoy no aplica en el deploy público: no persiste consultas |
| Interpretación como recomendación sin fuente | Respuestas basadas en reglas determinísticas con fuente citada o datos crudos; prohibición de consejo improvisado |
| Concentración total de conocimiento en un mantenedor | Riesgo de continuidad; documentación mantenida para que sea retomable |

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
- criterios académicos exigidos por INACAP para el cierre.

## Evidencia reproducible

```bash
# Estado normativo y estructura del producto
sed -n '1,260p' AGENTS.md
git ls-files 'backend/app/**/*.py'
git ls-files 'backend/tests/test_*.py'

# Autoría técnica
git shortlog -sne --all

# Estado de las iniciativas y validaciones abiertas
gh issue view 214 --repo sebitabravo/AgroVoz
gh issue view 215 --repo sebitabravo/AgroVoz

# Antecedente histórico
gh api graphql -f owner=sebitabravo -f name=AgroVoz \
  -F number=137 \
  -f query='query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){discussion(number:$number){title body comments(first:100){nodes{body}}}}}'
```

[d137]: https://github.com/sebitabravo/AgroVoz/discussions/137
[i214]: https://github.com/sebitabravo/AgroVoz/issues/214
[i215]: https://github.com/sebitabravo/AgroVoz/issues/215
