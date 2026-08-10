# Informe de estado y preparación de defensa

**Proyecto:** AgroVoz  
**Contexto:** Desafío Crea INACAP 2026 — INACAP Temuco  
**Estado del documento:** borrador de defensa y matriz de evidencia pendiente  
**Fecha de corte:** 29 de julio de 2026  
**Condición:** este documento no es un acta de cierre

## 1. Resumen ejecutivo

AgroVoz es un asistente de voz y texto sobre WhatsApp para consultar precios agrícolas de ODEPA y
clima de OpenMeteo. Está orientado a pequeños agricultores chilenos que necesitan acceso simple a
información oficial sin instalar una aplicación.

La base técnica está implementada en el código y preparada para una demostración controlada: entrada
por audio o texto, transcripción local, consulta de datos mediante herramientas permitidas, respuesta
hablada o escrita, alertas y administración. La evidencia de una versión desplegada, smoke E2E y
rendimiento debe asociarse a una versión antes de presentarse como resultado. El siguiente hito no
está completado: ejecutar un piloto de 4 semanas con 3 a 5 productores de Traiguén y convertir su
uso en evidencia.

Por lo tanto, la defensa debe demostrar dos cosas distintas:

1. **Producto técnico existente:** se prueba con una versión identificada, demo y controles
   reproducibles.
2. **Validación todavía abierta:** se presenta como plan, protocolo y evidencia pendiente; no como
   resultado.

## 2. Estado real del proyecto

| Área | Estado defendible | Evidencia a mostrar | Lo que no se puede afirmar |
|---|---|---|---|
| Pipeline WhatsApp de audio | Implementado en código; demo E2E pendiente | Demo controlada y logs saneados con IDs/tiempos | Disponibilidad perfecta o latencia garantizada sin prueba |
| Entrada y respuesta por texto | Implementada en código; demo pendiente | Consulta escrita y respuesta correspondiente | Que reemplaza la validación del canal de voz |
| Precios ODEPA | Implementado en código para el catálogo descrito; revalidación pendiente | Consulta, fecha de dato y herramienta ejecutada | Cobertura de cualquier producto o mercado fuera del catálogo |
| Clima OpenMeteo | Implementado en código por comuna configurada; evidencia pendiente | Consulta con fuente y momento | Pronóstico infalible o recomendación agronómica |
| Tool Calling | Whitelist de 10 herramientas en código; `register_expense` permanece cerrada por feature gate | Registro estructurado o prueba automatizada | Que el modelo puede ejecutar herramientas arbitrarias o que el registro de gastos esté completo |
| Alertas | Implementadas en código; efecto en terreno pendiente | Configuración y ejemplo seguro | Efectividad en terreno no medida |
| Dashboard admin | Implementado en código; demo pendiente | Vistas y métricas con datos de demostración identificados | Que sus métricas equivalen a impacto del piloto |
| Privacidad e historial | Controles técnicos implementados, con opt-in explícito | Flujo de consentimiento/revocación y datos redactados | Cumplimiento jurídico certificado |
| Piloto Traiguén | Planificado | Kit, instrumentos y criterios | Participación, satisfacción, impacto o resultados |
| WER rural chileno | Por medir | Protocolo y comando cuando exista muestra consentida | Un WER alcanzado o validado |
| Relación PRODESAL/INDAP | Objetivo abierto | Solicitud y respuesta cuando existan | Alianza, aval o validación institucional |
| Cierre de proyecto | No corresponde todavía | Lista de pendientes y próximo hito | Que el proyecto está cerrado |

## 3. Problema y propuesta de valor

El relato de problema plantea una asimetría de información entre productores e intermediarios. Las
cifras de población y diferencia de precio usadas en materiales internos deben acompañarse de su
fuente primaria o secundaria antes de presentarse como dato verificado.

La propuesta de valor defendible es:

> Consultar por WhatsApp, hablando o escribiendo, precios agrícolas y clima provenientes de fuentes
> identificadas, sin instalar una aplicación. Las recomendaciones agronómicas se limitan a reglas
> determinísticas con fuente INIA/INDAP citada; sin fuente vigente, el sistema informa la falta de dato.

No se debe prometer aumento de ingresos, reducción de pérdidas o adopción masiva mientras no exista
una medición de campo con método y limitaciones.

## 4. Solución demostrable

### Flujo de audio

```text
WhatsApp → Open-WA → FastAPI → ffmpeg → Whisper
         → clasificación / herramientas ODEPA u OpenMeteo
         → respuesta → Piper → WhatsApp
```

### Flujo de texto

```text
WhatsApp → Open-WA → FastAPI
         → clasificación / herramientas ODEPA u OpenMeteo
         → respuesta escrita → WhatsApp
```

### Límites que deben decirse en voz alta

- Stack local y abierto, pero con dependencias externas de datos y canal.
- Sin app nativa, IoT, pagos ni dashboard para agricultores.
- Solo español chileno en el alcance actual.
- SQLite y procesamiento síncrono por decisión de simplicidad y costo.
- Recomendaciones agronómicas solo por regla citada con fuente INIA/INDAP vigente (actualmente apagadas por feature gate).
- La auditoría formal por Ley 21.719 está pendiente antes de escalar.
- El peor caso de 1 vCPU / 4 GB RAM debe respaldarse con una ejecución actual, no solo con diseño.

## 5. Contribuciones del equipo

| Integrante | Contribución que puede presentarse | Evidencia apropiada |
|---|---|---|
| Sebastián Bravo | Backend, LLM, Tool Calling, Open-WA y arquitectura | Historial Git, PRs, demo técnica y documentación |
| Francisco Fernández | Investigación, Product Ownership, pitch y enlace con productores | Material de negocio, pitch, plan y registros de coordinación |
| Matías Atuán | Apoyo técnico, testing, documentación y validación de fuentes | Revisiones, pruebas, documentos y matriz de fuentes |

No se debe afirmar que los tres escribieron código. El historial de Git determina la autoría técnica;
los aportes no técnicos requieren sus propios artefactos.

## 6. Guion modular propuesto

El tiempo oficial de exposición no está confirmado en este documento. El equipo debe ajustar los
módulos a la pauta recibida sin eliminar la sección de limitaciones.

| Módulo | Mensaje central | Responsable propuesto | Evidencia visual |
|---|---|---|---|
| Apertura | Quién es el productor y qué decisión intenta tomar | Francisco | Historia breve sin identificar a una persona real |
| Problema | La información mayorista no llega de forma accesible al momento de negociar | Francisco | Fuente de cifras y mapa/contexto |
| Propuesta | WhatsApp es la interfaz; voz y texto son canales de primera clase | Francisco | Diagrama simple |
| Demo de texto | Consulta rápida con dato y fuente | Sebastián | WhatsApp o entorno controlado |
| Demo de audio | Audio, transcripción, herramienta y respuesta hablada | Sebastián | Flujo E2E con fallback preparado |
| Arquitectura | Stack local abierto, whitelist y restricciones | Sebastián | Diagrama técnico |
| Calidad y evidencia | Pruebas, trazabilidad y fuentes | Matías | CI, checklist y referencias |
| Gestión | Estado ejecutado versus piloto pendiente | Matías | Hitos y matriz de estado |
| Privacidad y límites | Consentimiento, minimización, sin consejo y auditoría pendiente | Equipo | Flujo de datos mínimo |
| Próximo hito | Piloto de 3 a 5 productores durante 4 semanas | Francisco | Plan e instrumentos, no resultados |
| Cierre | Qué está construido, qué falta validar y qué apoyo se solicita | Francisco | Tres afirmaciones verificables |

Estas asignaciones son una propuesta para ensayar y confirmar; no acreditan quién ha realizado cada
documento histórico.

## 7. Plan de demo

### Preparación

1. Fijar el commit o versión que se demostrará.
2. Ejecutar smoke test y pruebas relevantes.
3. Confirmar actualización de ODEPA y acceso a OpenMeteo.
4. Preparar un número/chat de demostración sin conversaciones reales.
5. Verificar audio, volumen, red y tiempo disponible.
6. Tener capturas o video de respaldo de la misma versión.
7. Limpiar cualquier PII de dashboard, terminal y logs.

### Recorrido principal

1. Enviar una consulta escrita de precio.
2. Mostrar respuesta, mercado, unidad, fecha y fuente.
3. Enviar una consulta de voz sobre clima.
4. Mostrar que el flujo transcribe, consulta y responde por voz.
5. Abrir el dashboard solo para explicar operación, no para alegar impacto.

### Fallback honesto

Si falla WhatsApp, Open-WA, la red o una fuente externa:

- declarar qué componente falló;
- usar una captura o grabación de la misma versión;
- mostrar pruebas o logs saneados;
- no ejecutar datos fabricados como si fueran en vivo;
- no ocultar el incidente de la conclusión.

## 8. Matriz de afirmaciones y evidencia

| Afirmación posible | Evidencia mínima antes de decirla | Estado |
|---|---|---|
| “El producto procesa audio de punta a punta” | Demo o test E2E identificado | Preparar evidencia actual |
| “También responde texto” | Demo del camino sin Whisper/Piper | Preparar evidencia actual |
| “Define 10 herramientas permitidas; gastos sigue desactivada” | Lista en código/configuración, feature gate y tests | Verificable en repositorio |
| “Cubre el catálogo ODEPA declarado” | Consulta automatizada con fecha y resultado | Revalidar antes de defensa |
| “Funciona bajo hardware degradado” | Perfil de 1 vCPU / 4 GB con comandos y latencia | Pendiente de capturar |
| “La latencia cumple el objetivo” | Distribución de mediciones, no un caso aislado | Pendiente de evidencia actual |
| “Whisper entiende habla rural con WER objetivo” | Dataset consentido, transcripciones de referencia y cálculo | **Sin resultado todavía** |
| “Los productores lo validaron” | Piloto terminado, muestra, método y resultados | **No disponible** |
| “PRODESAL/INDAP lo validó” | Documento o comunicación inequívoca | **No disponible** |
| “Cumple la Ley 21.719” | Revisión jurídica con alcance explícito | **No certificado** |
| “Reduce pérdidas o mejora ingresos” | Diseño y medición causal/observacional suficiente | **No medido** |

## 9. Evidencia pendiente para la carpeta de defensa

La carpeta no está cerrada ni cuenta con un checklist ejecutado asociado a una versión. Todas las
casillas siguientes permanecen pendientes hasta reunir el artefacto, fecha, método y alcance
correspondientes; los tests locales no sustituyen una demo, piloto, medición de hardware o revisión
institucional/legal.

### Técnica

- [ ] Commit o versión de demo.
- [ ] Resultado de tests y CI asociado a esa versión.
- [ ] Smoke test de audio y texto.
- [ ] Evidencia de catálogo ODEPA y fecha de sincronización.
- [ ] Ejemplo de OpenMeteo con fecha/hora.
- [ ] Prueba de hardware degradado.
- [ ] Medición reproducible de latencia, separada por etapa.
- [ ] Captura de logs saneados sin contenido libre ni hashes.
- [ ] Plan B de demo.

### Campo

- [ ] Participantes confirmados sin exponer su identidad.
- [ ] Consentimientos versionados.
- [ ] Registro de onboarding.
- [ ] Check-ins semanales.
- [ ] Métricas pre/post definidas antes de observar resultados.
- [ ] Resultado WER calculado sobre muestra consentida.
- [ ] Análisis de uso, problemas y abandono.
- [ ] Limitaciones de muestra.

### Institucional y legal

- [ ] Pauta y horario oficial de defensa.
- [ ] Contacto formal con PRODESAL/INDAP y respuesta.
- [ ] Permisos para usar nombres o logos institucionales.
- [ ] Auditoría jurídica previa a escalamiento.
- [ ] Registro de riesgos actualizado.

Una casilla pendiente se presenta como pendiente; no se completa con una estimación ni con una
prueba local que no cubra el claim.

## 10. Preguntas difíciles y respuesta honesta

### “¿Ya lo usan agricultores reales?”

El producto está implementado y el piloto está diseñado, pero a la fecha de corte no se presentan
resultados de campo. El siguiente hito es ejecutar 4 semanas con 3 a 5 participantes consentidos.

### “¿Qué precisión tiene Whisper con acento rural chileno?”

Existe un objetivo de validación, no un resultado defendible. Se requiere una muestra consentida,
transcripción de referencia y cálculo reproducible de WER.

### “¿INDAP o PRODESAL respaldan el proyecto?”

No se acredita respaldo ni acuerdo. El contacto formal es un objetivo abierto.

### “¿Cumple la nueva ley de datos?”

Hay controles técnicos de minimización, retención y consentimiento. La auditoría jurídica formal
previa a escalar sigue pendiente, por lo que no corresponde afirmar cumplimiento certificado.

### “¿Por qué no usar una API comercial?”

El alcance exige stack abierto y ejecución local para controlar costo, privacidad y dependencia.
Esa decisión también impone límites de rendimiento que deben medirse en el hardware objetivo.

### “¿Por qué WhatsApp?”

Evita instalar una aplicación adicional y permite usar voz o texto en un canal conocido. La
afirmación de usabilidad debe validarse en el piloto, no inferirse solo de la interfaz.

### “¿El sistema recomienda cuándo vender o qué hacer?”

No. Entrega datos de precio y clima con fuente; la decisión pertenece al productor.

### “¿Qué pasa si no entiende tres veces?”

Primero pide repetir más despacio, luego ofrece escribir por texto y finalmente informa que el chat
no tiene atención humana, sugiriendo contactar directamente canales reales como PRODESAL o INDAP.
No promete que alguien llamará ni crea una cola humana inexistente.

## 11. Criterios para declarar cierre futuro

AgroVoz no debe presentarse como proyecto cerrado hasta que, como mínimo:

1. el piloto planificado haya terminado o exista una decisión documentada de cancelarlo;
2. sus resultados y limitaciones estén analizados;
3. la evaluación WER tenga método y resultado reproducibles, o se documente por qué no se ejecutó;
4. la evidencia de latencia en hardware degradado esté actualizada;
5. el contacto institucional tenga un resultado documentado, aunque no haya acuerdo;
6. los riesgos de privacidad y la revisión pre-escalamiento tengan resolución explícita;
7. INACAP haya recibido y evaluado los entregables requeridos;
8. exista una decisión sobre continuar, pivotar, pausar o cerrar.

## 12. Conclusión para la defensa

La conclusión recomendada no es “terminamos el proyecto”, sino:

> Construimos y desplegamos la base funcional de AgroVoz y podemos demostrar su recorrido técnico.
> La pregunta abierta es si esa solución funciona de forma comprensible, útil y sostenible para
> productores reales de Traiguén. El piloto, la medición WER, el contacto institucional y la revisión
> jurídica son la evidencia que todavía debemos producir.

## 13. Fuentes

- [`AGENTS.md`](../../AGENTS.md): estado operativo, alcance, equipo, restricciones y deuda conocida.
- [`docs/pmbok/README.md`](README.md): método de defensa basado en evidencia y varianza honesta.
- [`docs/negocio/01-resumen-ejecutivo.md`](../negocio/01-resumen-ejecutivo.md).
- [`docs/negocio/05-estado-del-proyecto.md`](../negocio/05-estado-del-proyecto.md).
- [`docs/negocio/10-equipo.md`](../negocio/10-equipo.md).
- [`docs/piloto/00-plan-de-pilotaje.md`](../piloto/00-plan-de-pilotaje.md).
- [`docs/piloto/04-metricas-pre-post.md`](../piloto/04-metricas-pre-post.md).
- [`docs/piloto/06-acuerdo-consentimiento.md`](../piloto/06-acuerdo-consentimiento.md).

**Control de afirmaciones:** toda cifra o resultado añadido después de esta versión debe incluir
fuente, fecha, método y alcance.
