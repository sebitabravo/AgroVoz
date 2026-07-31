# Matriz de trazabilidad de requisitos de AgroVoz

## Uso de la matriz

La matriz conecta necesidad, fuente, entregable, verificación y estado. Se
aplican estas categorías:

- **Implementado:** existe evidencia técnica integrada.
- **Implementado; validar:** la capacidad existe, pero falta comprobarla en el
  entorno o población objetivo.
- **Planificado:** se definió el resultado, sin evidencia de ejecución completa.
- **En curso / reconciliar:** código, documentación y estado GitHub aún no
  ofrecen un cierre único.
- **Descartado:** se decidió excluir con motivo trazable.

Una discussion no equivale a requisito aprobado. Sus ideas se vuelven alcance
solo cuando respetan [AGENTS.md](../../AGENTS.md) y quedan formalizadas en
issues o en la línea base.

## Matriz de requisitos

| ID | Requisito | Fuente | Entregable / evidencia | Verificación | Estado |
|---|---|---|---|---|---|
| RQ-F01 | Recibir consultas de audio por WhatsApp y responder con audio | Línea base del producto | Pipeline, Open-WA, Whisper y Piper | Tests del pipeline + E2E [#214][i214] | Implementado; validar |
| RQ-F02 | Recibir texto como entrada de primera clase y responder por escrito | `AGENTS.md`, [PR #203][pr203] | Ruta de texto del webhook | Tests de webhook/pipeline | Implementado |
| RQ-F03 | Consultar precio actual para catálogo ODEPA completo | `AGENTS.md` | `get_price`, 79 productos y 15 mercados | Tests de catálogo y precios | Implementado |
| RQ-F04 | Consultar historia y comparar mercados | [Discussion #34][d34], [#85][i85], [#171][i171] | `get_price_history`, `get_price_spread` | Tests de herramientas | Implementado |
| RQ-F05 | Calcular valor de venta sin delegar aritmética al LLM | [Discussion #48][d48], [#104][i104] | `calculate_sale_value` con cálculo determinístico | Casos de unidad, cantidad y error | Implementado |
| RQ-F06 | Calcular margen de venta | [Discussion #34][d34], [#91][i91] | `calculate_margin` | Tests de extracción y cálculo | Implementado |
| RQ-F07 | Entregar clima actual e histórico por comuna | [Discussion #50][d50], [#124][i124] | OpenMeteo y preferencias de comuna | Tests weather + cache | Implementado |
| RQ-F08 | Buscar contenido oficial sin consejo agronómico | [Discussion #35][d35], [#100][i100] | `search_corpus` sobre corpus permitido | Tests RAG y citas | Implementado |
| RQ-F09 | Enviar alertas informativas de precio y clima | Discussions [#34][d34], [#35][d35] y [#50][d50] | Alertas, deduplicación, consentimiento y rate limit | Tests de alertas | Implementado |
| RQ-F10 | Personalizar por identidad WhatsApp y comuna | [#86][i86], [#89][i89] | `user_prefs`, onboarding y mercado cercano | Tests de preferencias | Implementado |
| RQ-F11 | Mantener historial solo con opt-in y permitir eliminación | [#195][i195], [#201][i201] | Historial consentido, redacción y auditoría | Tests de consentimiento/borrado | En curso / reconciliar |
| RQ-F12 | Gestionar transiciones y timeout de conversación | [Discussion #135][d135], [#192][i192] | Máquina de estados | Tests de transiciones | En curso / reconciliar |
| RQ-F13 | Ofrecer landing, demo y panel administrativo | `AGENTS.md`, [#119][i119] | Astro, demo, Jinja2/HTMX y PWA admin | Build + tests admin/demo | Implementado |
| RQ-F14 | Exponer gestión complementaria mediante MCP sin reemplazar el admin | [Discussion #135][d135], [#193][i193], [#200][i200] | MCP con controles de seguridad | Tests + gate de producción | En curso / no activar aún |
| RQ-F15 | Incorporar soporte grupal PRODESAL | [Discussion #36][d36], [#173][i173] | Identidad grupal acotada | Tests y cierre del issue | En curso / reconciliar |
| RQ-F16 | Derivar a información oficial de crédito INDAP | [Discussion #36][d36], [#174][i174] | Contenido y respuesta informativa | Tests y cierre del issue | En curso / reconciliar |
| RQ-F17 | Registrar gastos y usarlos en el margen con retención acotada | [Discussion #34][d34], [#170][i170] | Persistencia gobernada, extracción y cálculo integrado | Tests de TTL/borrado, margen y precisión >90% | **Incompleto; feature gate apagado** |
| RQ-NF01 | Mantener menos de 15 s E2E | Restricción de `AGENTS.md` | Fast paths, benchmark y métricas por etapa | Smoke en piso [#215][i215] | Implementado; validar |
| RQ-NF02 | Funcionar en 1 vCPU / 6 GB | Restricción de `AGENTS.md` | Modelo cuantizado y despliegue de piso | [#215][i215] | Planificado para validación |
| RQ-NF03 | Evitar que un crash nativo del LLM mate FastAPI | Auditoría [#207][d207], [#213][i213] | Inferencia aislada y reiniciable | Tests de crash/timeout/no bloqueo | Implementado localmente; integrar |
| RQ-NF04 | Continuar con datos útiles ante fallos externos | [Discussion #47][d47], [#120][i120], [#121][i121], [#175][i175], [#176][i176] | Cache, fallback y alertas de stale | Tests de degradación | Implementado |
| RQ-NF05 | Mantener SQLite y procesamiento síncrono | Restricción de `AGENTS.md` | Configuración y persistencia | Inspección + tests | Implementado |
| RQ-NF06 | Mantener stack principal open-source y sin API paga | Restricción de `AGENTS.md` | Whisper, LLM local, Piper y Open-WA | Revisión de dependencias/config | Implementado |
| RQ-Q01 | Código Python tipado y validado | Convenciones de `AGENTS.md` | pytest, Ruff y mypy estricto | Comandos de calidad | Implementado por cambio |
| RQ-Q02 | WER rural menor a 15% | Objetivo del proyecto | Dataset consentido y `eval_wer.py` | Muestra piloto | Planificado |
| RQ-L01 | Eliminar audio temporal antes de 24 h y minimizar/seudonimizar transcripciones | Restricción legal | Servicios de retención y dataset | Tests + observación operativa | Implementado; auditar |
| RQ-L02 | Auditar formalmente Ley 21.719 antes de escalar | Restricción legal | Auditoría y controles de consentimiento | Revisión formal | Planificado |
| RQ-V01 | Ejecutar piloto con 3–5 productores durante cuatro semanas | Próximo hito de `AGENTS.md`, [#98][i98] | Kit de piloto y ejecución en terreno | Registros y métricas reales | Kit implementado; ejecución pendiente |

## Trazabilidad de las discussions

Esta tabla verifica las diez discussions visibles en GitHub. “Cubierta” puede
significar implementación, descarte razonado o traslado explícito a backlog;
no significa que todas las ideas hayan sido aceptadas.

| Discussion | Resultado trazable | Evaluación |
|---|---|---|
| [#34 — agrovoz.app][d34] | Multi-producto/mercado, historial, onboarding, resumen, alertas, margen, export y PWA admin existen; el registro de gastos [#170][i170] no cumple persistencia/retención/integración y quedó apagado | Parcial; #170 debe reabrirse |
| [#35 — agrivoz.ai][d35] | Citas, dataset, métricas, piloto y corpus se implementaron; diagnóstico, PWA agricultor y tiendas se descartaron por restricciones | Cubierta; no se copió alcance incompatible |
| [#36 — competencia global][d36] | IVR local, PRODESAL y derivación INDAP tienen implementación y tests; faltan PSTN, validación grupal, revisión de contenido e institucionalización B2G | Parcial; dependencias externas explícitas |
| [#47 — resiliencia ODEPA][d47] | Cache, detección, cron, health, fallback CKAN y alerta stale están trazados; [#175][i175] y [#176][i176] están cerrados | Cubierta |
| [#48 — valor de venta][d48] | Se eligió cálculo determinístico y [#104][i104] está cerrado | Cubierta |
| [#50 — aprendizajes Wenuke][d50] | Ideas compatibles se implementaron mediante [#119–#129][i119]; recomendaciones, planes, plugin público y DB alterna se cerraron fuera de alcance | Cubierta con implementación y descartes |
| [#135 — aprendizajes Nexor][d135] | Prompt, extracción, state machine, MCP e historial existen; las tres últimas capacidades conservan gates apagados por operación/privacidad | Parcial; integrar y validar [#192][i192], [#193][i193] y [#195][i195] |
| [#136 — humanización][d136] | Reglas, escalamiento, indicadores y worker reiniciable están implementados; VAD/streaming/barge-in se declararon no aplicables a notas grabadas | Parcial; #214, benchmark de modelos y validación con productores pendientes |
| [#137 — gap PMBOK][d137] | Se redactaron 11 documentos de gestión y un informe de defensa; no hay aprobación académica, activos finales de defensa ni piloto | Parcial; documentación local completada, aceptación externa pendiente |
| [#207 — auditoría][d207] | Endurecimientos, consulta compuesta [#212][i212] y aislamiento [#213][i213] están implementados; WhatsApp [#214][i214] y hardware mínimo [#215][i215] solo tienen protocolos | En curso por validaciones externas |

## Decisiones negativas trazables

| Idea | Decisión | Evidencia |
|---|---|---|
| Recomendaciones o diagnóstico agronómico | Excluido por restricción de producto | [#101][i101], [#130][i130], [#177][i177], [#178][i178] |
| PWA/app para agricultores | Excluida; WhatsApp es la aplicación | [#102][i102] |
| Tiendas cercanas con API paga | Excluida por costo y alcance | [#103][i103] |
| Planes free/premium | Excluidos del alcance actual | [#131][i131] |
| Base de datos Turso | Excluida; SQLite es restricción | [#133][i133] |
| Canal IVR | Backlog, no parte de la línea base | [#172][i172] |

## Brechas que impiden declarar cumplimiento total

1. No hay evidencia registrada de que el piloto haya comenzado o terminado.
2. No existe aún medición WER con la muestra rural objetivo.
3. La validación E2E con una sesión Open-WA autenticada sigue abierta.
4. El smoke de 1 vCPU / 6 GB sigue abierto.
5. La naturalidad y cualquier cambio de Whisper/TTS de #136 carecen de
   benchmark en hardware mínimo y validación con productores.
6. El registro de gastos #170 está incompleto y permanece apagado.
7. Hay issues abiertos cuya capacidad puede existir en el checkout de trabajo;
   deben integrarse, validarse y cerrarse antes de contarlos como aceptados.
8. No consta aprobación académica de esta documentación ni exigencia confirmada
   de producir los 27 documentos propuestos históricamente en #137.

## Procedimiento de actualización

Al cerrar un requisito:

1. ejecutar su verificación;
2. enlazar issue y pull request;
3. actualizar estado y evidencia en esta matriz;
4. revisar impacto en [Acta](./01-acta-constitucion.md),
   [Plan de dirección](./02-plan-direccion.md) y
   [Alcance/EDT](./03-alcance-edt.md);
5. no borrar decisiones descartadas: conservarlas como control de alcance.

## Evidencia reproducible

```bash
# Listar las discussions y su estado actual
gh api graphql -f owner=sebitabravo -f name=AgroVoz \
  -f query='query($owner:String!,$name:String!){repository(owner:$owner,name:$name){discussions(first:100){nodes{number title updatedAt closedAt url}}}}'

# Recalcular issues abiertos y cierres
gh issue list --repo sebitabravo/AgroVoz --state open --limit 200
gh issue list --repo sebitabravo/AgroVoz --state closed --limit 200

# Validación técnica del checkout candidato
cd backend
uv run pytest tests/ -v --tb=short
uv run ruff check app/
uv run mypy app/
```

[d34]: https://github.com/sebitabravo/AgroVoz/discussions/34
[d35]: https://github.com/sebitabravo/AgroVoz/discussions/35
[d36]: https://github.com/sebitabravo/AgroVoz/discussions/36
[d47]: https://github.com/sebitabravo/AgroVoz/discussions/47
[d48]: https://github.com/sebitabravo/AgroVoz/discussions/48
[d50]: https://github.com/sebitabravo/AgroVoz/discussions/50
[d135]: https://github.com/sebitabravo/AgroVoz/discussions/135
[d136]: https://github.com/sebitabravo/AgroVoz/discussions/136
[d137]: https://github.com/sebitabravo/AgroVoz/discussions/137
[d207]: https://github.com/sebitabravo/AgroVoz/discussions/207
[i85]: https://github.com/sebitabravo/AgroVoz/issues/85
[i86]: https://github.com/sebitabravo/AgroVoz/issues/86
[i89]: https://github.com/sebitabravo/AgroVoz/issues/89
[i91]: https://github.com/sebitabravo/AgroVoz/issues/91
[i98]: https://github.com/sebitabravo/AgroVoz/issues/98
[i100]: https://github.com/sebitabravo/AgroVoz/issues/100
[i101]: https://github.com/sebitabravo/AgroVoz/issues/101
[i102]: https://github.com/sebitabravo/AgroVoz/issues/102
[i103]: https://github.com/sebitabravo/AgroVoz/issues/103
[i104]: https://github.com/sebitabravo/AgroVoz/issues/104
[i119]: https://github.com/sebitabravo/AgroVoz/issues/119
[i120]: https://github.com/sebitabravo/AgroVoz/issues/120
[i121]: https://github.com/sebitabravo/AgroVoz/issues/121
[i124]: https://github.com/sebitabravo/AgroVoz/issues/124
[i130]: https://github.com/sebitabravo/AgroVoz/issues/130
[i131]: https://github.com/sebitabravo/AgroVoz/issues/131
[i133]: https://github.com/sebitabravo/AgroVoz/issues/133
[i170]: https://github.com/sebitabravo/AgroVoz/issues/170
[i171]: https://github.com/sebitabravo/AgroVoz/issues/171
[i172]: https://github.com/sebitabravo/AgroVoz/issues/172
[i173]: https://github.com/sebitabravo/AgroVoz/issues/173
[i174]: https://github.com/sebitabravo/AgroVoz/issues/174
[i175]: https://github.com/sebitabravo/AgroVoz/issues/175
[i176]: https://github.com/sebitabravo/AgroVoz/issues/176
[i177]: https://github.com/sebitabravo/AgroVoz/issues/177
[i178]: https://github.com/sebitabravo/AgroVoz/issues/178
[i192]: https://github.com/sebitabravo/AgroVoz/issues/192
[i193]: https://github.com/sebitabravo/AgroVoz/issues/193
[i195]: https://github.com/sebitabravo/AgroVoz/issues/195
[i200]: https://github.com/sebitabravo/AgroVoz/issues/200
[i201]: https://github.com/sebitabravo/AgroVoz/issues/201
[i212]: https://github.com/sebitabravo/AgroVoz/issues/212
[i213]: https://github.com/sebitabravo/AgroVoz/issues/213
[i214]: https://github.com/sebitabravo/AgroVoz/issues/214
[i215]: https://github.com/sebitabravo/AgroVoz/issues/215
[pr203]: https://github.com/sebitabravo/AgroVoz/pull/203
