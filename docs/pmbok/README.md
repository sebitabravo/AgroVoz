# Documentación PMBOK — AgroVoz

Documentación de gestión de proyecto para la evaluación de INACAP.

**Estado al 29/07/2026: borrador completo, pendiente de revisión y aprobación.**
Existen los 11 documentos de gestión y el informe de defensa enumerados abajo.

## El recorte: 11 documentos, no 27

La referencia es ManttoAI, otro proyecto INACAP ya aprobado, que tiene **27 documentos PMBOK**
cubriendo 9 de las 10 áreas. Replicar los 27 es innecesario:

| Decisión | Razón |
|---|---|
| **Fusionar** Plan de Alcance + Enunciado + EDT en uno | La EDT *es* el alcance. Tres documentos para lo mismo es relleno |
| **Fusionar** Plan de Dirección con las líneas base | Un solo documento que se lee de corrido |
| **Omitir** Plan de Adquisiciones | El stack es 100% open-source. No hay proveedores ni contratos que gestionar. Una tabla de tres filas en el Plan de Dirección lo cubre |
| **Omitir** Acta de Cierre | Duplica el Resumen Ejecutivo y el Informe Final |

Quedan **11 documentos + 1 informe de defensa**.

## La ventaja: derivarlos del repositorio

El proyecto se construyó entre el **16/06/2026 y el 24/07/2026** dejando rastro completo:

```
112 commits · 100 issues (86 cerrados, 14 abiertos) ·
103 pull requests (89 integrados, 4 abiertos, 10 cerrados sin merge)
```

Buena parte de la evidencia PMBOK **se extrae, no se inventa**:

| Documento | Se deriva de | Comando |
|---|---|---|
| Lista de actividades e hitos | Historial de commits | `git log --format="%ad %s" --date=short --reverse` |
| Cronograma ejecutado | Fechas de primer y último commit | `git log --format=%ad --date=short \| sort -u` |
| Matriz de trazabilidad | Issues y PRs | `gh issue list --state closed --limit 200 --json number,title,labels` |
| Control de cambios | PRs integrados | `gh pr list --state merged --limit 200 --json number,title,mergedAt` |
| Matriz RACI (contraste real) | Autoría de commits | `git shortlog -sne --all` |
| Plan de calidad | Tests y CI | `uv run pytest tests/ --cov=app` |

Poner el comando exacto como nota al pie de cada tabla la vuelve **auditable y reproducible**, que
es exactamente lo que distingue un documento PMBOK serio de uno decorativo.

## Honestidad sobre planificado vs ejecutado

Hay una tensión que resolver de frente, porque el evaluador la va a ver:

**La postulación de junio planificaba 6 semanas de MVP. El desarrollo real tomó 5,4 semanas y ya
terminó.** Escribir hoy un "plan" hacia atrás y presentarlo como si hubiera guiado la ejecución es
ficción retroactiva.

Lo que corresponde:

1. **Línea base planificada** = lo que decía la postulación del 08/06/2026, que está congelada en
   [`docs/historico/postulacion-crea-2026.md`](../historico/postulacion-crea-2026.md).
2. **Línea base ejecutada** = lo que muestra el repositorio.
3. **Análisis de varianza** = por qué difieren, con las razones reales (conocimiento previo del
   stack, alcance cerrado sin scope creep, testing incremental) y también las incómodas (un solo
   desarrollador, sin las ceremonias que un plan formal habría impuesto).

Un proyecto que documenta su varianza con honestidad se defiende mejor que uno que finge haber
seguido un plan que nunca existió.

## Los documentos

| # | Documento | Archivo | Fuente principal | Estado |
|---|---|---|---|---|
| 1 | Acta de Constitución | `01-acta-constitucion.md` | `negocio/01`, `negocio/02` | Redactado |
| 2 | Plan de Dirección del Proyecto | `02-plan-direccion.md` | Git log + `negocio/05` | Redactado |
| 3 | Enunciado del Alcance + EDT | `03-alcance-edt.md` | `negocio/03`, `negocio/04` | Redactado |
| 4 | Matriz de Trazabilidad | `04-trazabilidad.md` | Issues y PRs | Redactado |
| 5 | Cronograma e Hitos | `05-cronograma.md` | Git log | Redactado |
| 6 | Plan de Calidad (ISO 25010) | `06-calidad.md` | Tests, ruff, mypy, CI | Redactado |
| 7 | Recursos y Matriz RACI | `07-recursos-raci.md` | `negocio/10` + entrevistas al equipo | Redactado |
| 8 | Registro de Riesgos + Matriz P/I | `08-riesgos.md` | `negocio/07`, `SECURITY.md`, `docs/legal/` | Redactado |
| 9 | Plan de Comunicaciones | `09-comunicaciones.md` | `negocio/11` | Redactado |
| 10 | Registro de Interesados | `10-interesados.md` | `negocio/09`, `negocio/11` | Redactado |
| 11 | Control de Cambios | `11-control-cambios.md` | PRs integrados | Redactado |
| 12 | Informe Final de Defensa | `12-informe-defensa.md` | Todos los anteriores | Redactado |

La estimación inicial de este recorte era 19–22 horas. La Discussion #137
estimaba 60–88 horas bajo el supuesto, hoy obsoleto, de que parte importante
del código aún estaba por escribirse. El borrador no equivale a aprobación
académica ni a cierre del proyecto.

## Riesgos conocidos de esta documentación

| Riesgo | Mitigación |
|---|---|
| El historial de git muestra un solo autor en el 100% de los commits | No maquillarlo. Documentar la contribución real de cada integrante, incluyendo la que no queda en git (terreno, validación de fuentes, documentación), y que lo que quede por hacer sí tenga autoría distribuida y verificable |
| Citar issues o cifras que no existen | Todo número va con su comando o su fuente. Si no se puede verificar, se escribe "pendiente de verificar" |
| Reportar como medido lo que aún no se mide | El piloto no ha ocurrido. Marcar cada métrica como **medida** o **a medir** |
