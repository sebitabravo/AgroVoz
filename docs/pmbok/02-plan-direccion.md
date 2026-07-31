# Plan para la dirección del proyecto AgroVoz

## Propósito y estado

Este documento integra la forma en que AgroVoz define, ejecuta, controla y
cierra trabajo. Describe prácticas comprobables del repositorio y separa los
controles ya usados de aquellos que aún requieren formalización.

La línea base técnica es [AGENTS.md](../../AGENTS.md), complementada por
[ARCHITECTURE.md](../ARCHITECTURE.md). Las discussions son insumos de
investigación; solo pasan a la línea base cuando una decisión respeta las
restricciones y queda trazada a código, documentación, un issue cerrado o una
aceptación explícita.

## Principios de integración

1. **Una fuente normativa:** stack, restricciones y estado de producto viven en
   `AGENTS.md`.
2. **Arquitectura explícita:** decisiones y flujos técnicos viven en
   `docs/ARCHITECTURE.md`.
3. **Trabajo trazable:** cada cambio tiene un objetivo acotado y evidencia en
   issue, código, tests y pull request.
4. **Estado verificable:** “discutido”, “planificado”, “implementado”,
   “validado” y “aceptado” no son sinónimos.
5. **Cambios reversibles:** se favorecen implementaciones pequeñas, sin ampliar
   el alcance por inferencia.
6. **Restricciones primero:** una idea que contradice el producto se descarta o
   queda condicionada, aunque sea técnicamente posible.

## Jerarquía de evidencia

| Prioridad | Fuente | Uso |
|---|---|---|
| 1 | `AGENTS.md` | Alcance, stack, restricciones, equipo y estado vigente |
| 2 | `docs/ARCHITECTURE.md` | Arquitectura, esquema y decisiones técnicas |
| 3 | Código, migraciones y configuración | Evidencia de implementación |
| 4 | Tests y comandos de validación | Evidencia de comportamiento comprobado |
| 5 | Issues y pull requests | Decisión, cambio y cierre trazables |
| 6 | Discussions | Investigación, alternativas e ideas; no constituyen aceptación por sí solas |

Si dos fuentes discrepan, no se oculta el conflicto. Por ejemplo, la
[Discussion #137][d137] describe un proyecto en fase temprana, mientras la
fuente normativa y los pull requests posteriores registran las fases 00–06
completadas. La discusión queda como antecedente y la línea base vigente
prevalece.

## Ciclo integrado de trabajo

| Paso | Control | Evidencia mínima |
|---|---|---|
| Identificar necesidad | Describir problema y resultado esperado | Discussion o hallazgo, cuando corresponda |
| Autorizar alcance | Issue acotado y compatible con restricciones | Issue con objetivo y criterio de aceptación |
| Diseñar | Consultar arquitectura y skills aplicables | Decisión documentada si cambia el diseño global |
| Implementar | Cambio pequeño, tipado y sin secretos | Diff revisable |
| Verificar | Tests, Ruff y mypy; prueba específica según riesgo | Salida reproducible de comandos |
| Integrar | Pull request vinculado al objetivo | PR revisado y merge por squash |
| Actualizar estado | Cerrar issue y alinear documentación | Código, docs y GitHub sin contradicciones |
| Aceptar o medir | Validación técnica, operativa o de usuario | Resultado observado, no estimación |

No se fija una ceremonia ni frecuencia de reunión porque la evidencia revisada
no contiene una cadencia aprobada.

## Líneas base

### Alcance

La línea base está en [Alcance y EDT](./03-alcance-edt.md). Incluye voz y texto
por WhatsApp, datos oficiales, herramientas determinísticas, alertas,
administración, despliegue y validación. Excluye recomendaciones agronómicas,
IoT, app nativa, pagos y base de datos separada.

### Cronograma

- Las fases históricas 00–06 están completadas.
- El proyecto ya no se dirige por fases; opera como mantención y evolución.
- El siguiente hito es el piloto de Traiguén, planificado para cuatro semanas.
- No consta una fecha aprobada de inicio o término del piloto.
- Las validaciones de WhatsApp real y hardware mínimo permanecen abiertas en
  [#214][i214] y [#215][i215].

No se crea aquí un Gantt retrospectivo ni se asignan fechas que el repositorio
no demuestre.

### Costos

Los valores de infraestructura y costo por agricultor definidos en
`AGENTS.md` son restricciones de diseño. No constituyen presupuesto aprobado,
flujo de caja ni costo real acumulado. Hasta que exista evidencia financiera,
el control se limita a:

- evitar APIs pagas;
- mantener el despliegue dentro del VPS objetivo;
- impedir que una dependencia nueva aumente recursos sin justificación;
- validar consumo en el piso de 1 vCPU y 6 GB.

### Calidad

| Dimensión | Criterio |
|---|---|
| Corrección | Tests de regresión y respuestas basadas en datos oficiales |
| Código | Ruff limpio, mypy estricto y type hints |
| Rendimiento | Menos de 15 segundos E2E en el hardware mínimo |
| Voz | WER menor a 15% en muestra rural del piloto |
| Seguridad | Secretos fuera de Git, auth admin diferenciada y logs sin PII |
| Privacidad | Opt-in, minimización/seudonimización, retención temporal y derecho de eliminación |
| Accesibilidad | WhatsApp como canal principal, voz y texto como entradas de primera clase |

Los tests automatizados comprueban software; no sustituyen la prueba con
Open-WA autenticado ni la validación en terreno.

## Planes de gestión por área

| Área | Enfoque vigente | Estado |
|---|---|---|
| Integración | Fuente normativa, issue-first, PR y alineación de docs | Ejecutado; este documento lo formaliza |
| Alcance | EDT, exclusiones y matriz de trazabilidad | Formalizado en este conjunto |
| Cronograma | Hitos por issues y piloto como próximo hito | Parcial; sin calendario aprobado |
| Costos | Restricciones técnicas, sin API paga | Ejecutado como constraint; presupuesto pendiente |
| Calidad | pytest, Ruff, mypy, smoke y métricas | Ejecutado; pruebas de terreno pendientes |
| Recursos | Tres roles definidos, sin asignaciones horarias | Parcial |
| Comunicaciones | Issues, PRs y discussions como registro | Ejecutado; cadencia formal no registrada |
| Riesgos | Degradación, fallbacks, monitoreo y backlog de endurecimiento | Ejecutado y abierto a revisión |
| Adquisiciones | Servicios externos gratuitos y VPS; sin plan contractual registrado | Pendiente si la evaluación lo exige |
| Interesados | Equipo, usuarios piloto, INACAP y actores institucionales | Identificados; compromisos externos pendientes |

## Gestión del alcance y cambios

Una solicitud cambia la línea base solo si:

1. no contradice las restricciones;
2. tiene resultado y aceptación verificables;
3. identifica impacto en código, tests, privacidad, latencia y documentación;
4. se integra mediante el flujo de GitHub;
5. actualiza la [Matriz de trazabilidad](./04-trazabilidad.md).

Ideas incompatibles, como diagnóstico agronómico, app offline para agricultores,
planes pagados o una base Turso, fueron cerradas o rechazadas en GitHub. Su
cierre también es trazabilidad: evita que reaparezcan como alcance implícito.

## Gestión de configuración

- Variables y secretos se mantienen fuera de Git.
- SQLite, modelos y audio temporal no se versionan.
- Cambios de esquema requieren migración Alembic y documentación.
- Cambios de arquitectura requieren actualizar `docs/ARCHITECTURE.md`.
- Los artefactos de desarrollo y producción se mantienen separados.
- No se reescribe historia remota ni se omiten verificaciones del repositorio.

## Seguimiento y control

| Señal | Fuente | Decisión que habilita |
|---|---|---|
| Tests, Ruff y mypy | CI o ejecución local | Integración técnica |
| Latencia por etapa | Pipeline y benchmark | Ajustes para el piso operativo |
| Salud de ODEPA/OpenMeteo/Open-WA | Monitor y logs sanitizados | Degradación o intervención |
| Estado de entrega | Métricas del backend | Distinguir respuesta generada de entrega efectiva |
| WER y éxito de tareas | Piloto consentido | Validar voz rural y utilidad |
| Issues abiertas | GitHub | Trabajo pendiente o cierre administrativo |
| Discrepancias docs/código | Revisión de alineación | Corrección antes de cierre |

El estado se comunica con estas categorías:

- **Planificado:** resultado decidido, aún sin evidencia de ejecución.
- **En curso:** existe trabajo no integrado o validación incompleta.
- **Implementado:** artefacto presente y cubierto por prueba proporcional.
- **Validado:** observado en el entorno o usuarios objetivo.
- **Descartado:** fuera de alcance con motivo registrado.
- **Pendiente de aceptación:** ejecutado, pero sin aprobación formal.

## Gestión de riesgos y escalamiento

Un fallo se atiende primero con mecanismos dentro del alcance: cache, fallback
determinístico, timeout, reinicio, retry limitado y monitoreo. Se escala al
equipo cuando afecta datos, privacidad, autenticación, migraciones, webhooks o
despliegue. Una ampliación material —por ejemplo, API paga, nueva base de datos
o recomendación agronómica— requiere decisión explícita y cambio de las fuentes
normativas.

## Cierre del proyecto o hito

Un hito puede cerrarse cuando:

- sus entregables y exclusiones coinciden con la línea base;
- tests y controles aplicables pasan;
- riesgos residuales están documentados;
- documentación y GitHub reflejan el mismo estado;
- la aceptación externa necesaria está registrada.

El producto implementado no equivale al cierre del piloto. El piloto requiere
participación real, métricas y consentimiento; no se declara completado hasta
contar con esa evidencia.

## Evidencia reproducible

```bash
# Calidad técnica
cd backend
uv run pytest tests/ -v --tb=short
uv run ruff check app/
uv run mypy app/

# Frontend
cd ../landing
bun run build

# Estado de trabajo e integración
cd ..
git status --short
gh issue list --repo sebitabravo/AgroVoz --state open
gh pr list --repo sebitabravo/AgroVoz --state merged --limit 20
```

[d137]: https://github.com/sebitabravo/AgroVoz/discussions/137
[i214]: https://github.com/sebitabravo/AgroVoz/issues/214
[i215]: https://github.com/sebitabravo/AgroVoz/issues/215
