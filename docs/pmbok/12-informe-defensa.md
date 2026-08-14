# Informe de estado y preparación de defensa

**Proyecto:** AgroVoz  
**Contexto:** Desafío Crea INACAP 2026 — INACAP Temuco  
**Estado del documento:** borrador de defensa y matriz de evidencia pendiente  
**Fecha de corte:** 29 de julio de 2026  
**Condición:** este documento no es un acta de cierre

> **Actualización 2026-08-14:** el proyecto no continuó en el Desafío Crea INACAP 2026. El piloto
> de Traiguén, el contacto institucional con PRODESAL/INDAP y su kit documental (`docs/piloto/`)
> ya no existen. AgroVoz es mantenido por una sola persona (Sebastián), verificable con
> `git shortlog -sne --all`. Este informe se reescribe para que cada sección refleje esa realidad
> en vez de tratar el piloto como un próximo hito pendiente.

## 1. Resumen ejecutivo

AgroVoz es un asistente de voz y texto sobre WhatsApp para consultar precios agrícolas de ODEPA y
clima de OpenMeteo. Está orientado a pequeños agricultores chilenos que necesitan acceso simple a
información oficial sin instalar una aplicación.

La base técnica está implementada en el código: entrada por audio o texto, transcripción local,
consulta de datos mediante herramientas permitidas, respuesta hablada o escrita, alertas y
administración. Sin infraestructura propia (VPS/NAS), el pipeline de voz completo y WhatsApp solo
son demostrables en local/Docker. El único deploy público vigente es una demo web slim en Vercel
(fast-path determinista + OpenRouter, sin modelos locales) — ver `docs/ARCHITECTURE.md` decisión 34.

Por lo tanto, la defensa debe demostrar dos cosas distintas:

1. **Producto técnico existente:** se prueba con una versión identificada, demo y controles
   reproducibles.
2. **Validación de campo nunca ejecutada:** el piloto planeado no se realizó y no hay respaldo
   institucional para retomarlo; no se presenta como resultado ni como plan en curso.

## 2. Estado real del proyecto

| Área | Estado defendible | Evidencia a mostrar | Lo que no se puede afirmar |
|---|---|---|---|
| Pipeline WhatsApp de audio | Implementado en código; solo demostrable en local/Docker | Demo controlada y logs saneados con IDs/tiempos | Disponibilidad perfecta, operación pública o latencia garantizada sin prueba |
| Entrada y respuesta por texto | Implementada; demo pública en Vercel | Demo web en vivo | Que reemplaza la validación del canal de voz |
| Precios ODEPA | Implementado en código para el catálogo descrito; revalidación pendiente | Consulta, fecha de dato y herramienta ejecutada | Cobertura de cualquier producto o mercado fuera del catálogo |
| Clima OpenMeteo | Implementado en código por comuna configurada | Consulta con fuente y momento | Pronóstico infalible o recomendación agronómica |
| Tool Calling | Whitelist de 19 herramientas en código; 7 quedan fuera del prompt por feature gate apagado | Registro estructurado o prueba automatizada | Que el modelo puede ejecutar herramientas arbitrarias |
| Alertas | Implementadas en código; sin usuarios reales que las reciban | Configuración y ejemplo seguro | Efectividad en terreno no medida |
| Dashboard admin | Implementado en código; solo local | Vistas y métricas con datos de demostración identificados | Que sus métricas equivalen a impacto de campo |
| Privacidad e historial | Controles técnicos implementados, con opt-in explícito | Flujo de consentimiento/revocación y datos redactados | Cumplimiento jurídico certificado |
| Piloto de campo | No ejecutado, sin respaldo institucional | — | Participación, satisfacción, impacto o resultados de ningún tipo |
| WER rural chileno | No medido; sin muestra consentida disponible | Protocolo documentado, sin ejecución | Un WER alcanzado o validado |
| Relación PRODESAL/INDAP | No establecida; el proyecto no continuó en Crea INACAP | — | Alianza, aval o validación institucional |

## 3. Problema y propuesta de valor

El relato de problema plantea una asimetría de información entre productores e intermediarios. Las
cifras de población y diferencia de precio usadas en materiales internos deben acompañarse de su
fuente primaria o secundaria antes de presentarse como dato verificado.

La propuesta de valor defendible es:

> Consultar por WhatsApp, hablando o escribiendo, precios agrícolas y clima provenientes de fuentes
> identificadas, sin instalar una aplicación. Las recomendaciones agronómicas se limitan a reglas
> determinísticas con fuente INIA/INDAP citada; sin fuente vigente, el sistema informa la falta de dato.

No se debe prometer aumento de ingresos, reducción de pérdidas o adopción masiva: nunca hubo
medición de campo, ni la habrá sin retomar un piloto que hoy no está planificado.

## 4. Solución demostrable

### Flujo de audio (local/Docker)

```text
WhatsApp → Open-WA → FastAPI → ffmpeg → Whisper
         → clasificación / herramientas ODEPA u OpenMeteo
         → respuesta → Piper → WhatsApp
```

### Flujo de texto (demo pública en Vercel)

```text
Landing → FastAPI slim
        → fast-path determinista (ODEPA/OpenMeteo) u OpenRouter
        → respuesta escrita
```

### Límites que deben decirse en voz alta

- Stack local y abierto, pero con dependencias externas de datos y canal.
- Sin app nativa, IoT, pagos ni dashboard para agricultores.
- Solo español chileno en el alcance actual.
- SQLite y procesamiento síncrono por decisión de simplicidad y costo.
- Recomendaciones agronómicas solo por regla citada con fuente INIA/INDAP vigente (actualmente apagadas por feature gate).
- La auditoría formal por Ley 21.719 nunca se activó: no aplica sin piloto ni datos personales de terceros en operación.
- El peor caso de 1 vCPU / 4 GB RAM debe respaldarse con una ejecución actual, no solo con diseño.
- No hubo ni hay piloto de campo: cero validación con productores reales.

## 5. Contribuciones

AgroVoz es mantenido por una sola persona.

| Integrante | Contribución que puede presentarse | Evidencia apropiada |
|---|---|---|
| Sebastián Bravo | Backend, LLM, Tool Calling, Open-WA, arquitectura, producto e investigación | Historial Git (`git shortlog -sne --all`), PRs, demo técnica y documentación |

## 6. Guion modular propuesto

El tiempo oficial de exposición no está confirmado en este documento.

| Módulo | Mensaje central | Evidencia visual |
|---|---|---|
| Apertura | Quién es el productor y qué decisión intenta tomar | Historia breve sin identificar a una persona real |
| Problema | La información mayorista no llega de forma accesible al momento de negociar | Fuente de cifras y mapa/contexto |
| Propuesta | WhatsApp es la interfaz; voz y texto son canales de primera clase | Diagrama simple |
| Demo de texto | Consulta rápida con dato y fuente, en vivo desde la demo pública | Vercel o entorno controlado |
| Demo de audio | Audio, transcripción, herramienta y respuesta hablada (local/Docker) | Flujo E2E con fallback preparado |
| Arquitectura | Stack local abierto, whitelist y restricciones | Diagrama técnico |
| Calidad y evidencia | Pruebas, trazabilidad y fuentes | CI, checklist y referencias |
| Gestión | Estado ejecutado versus piloto nunca realizado | Matriz de estado |
| Privacidad y límites | Minimización, sin consejo agronómico libre, sin datos de terceros en producción | Flujo de datos mínimo |
| Cierre | Qué está construido, qué falta validar y qué apoyo se solicita | Tres afirmaciones verificables |

## 7. Plan de demo

### Preparación

1. Fijar el commit o versión que se demostrará.
2. Ejecutar smoke test y pruebas relevantes.
3. Confirmar actualización de ODEPA y acceso a OpenMeteo.
4. Preparar un número/chat de demostración sin conversaciones reales (para el flujo local/Docker).
5. Verificar audio, volumen, red y tiempo disponible.
6. Tener capturas o video de respaldo de la misma versión.
7. Limpiar cualquier PII de dashboard, terminal y logs.

### Recorrido principal

1. Enviar una consulta escrita de precio desde la demo pública en Vercel.
2. Mostrar respuesta, mercado, unidad, fecha y fuente.
3. Si hay entorno local disponible: consulta de voz sobre clima, mostrando transcripción y respuesta hablada.
4. Explicar qué corre público (demo slim) versus qué solo corre local (pipeline de voz completo, WhatsApp).

### Fallback honesto

Si falla la demo, la red o una fuente externa:

- declarar qué componente falló;
- usar una captura o grabación de la misma versión;
- mostrar pruebas o logs saneados;
- no ejecutar datos fabricados como si fueran en vivo;
- no ocultar el incidente de la conclusión.

## 8. Matriz de afirmaciones y evidencia

| Afirmación posible | Evidencia mínima antes de decirla | Estado |
|---|---|---|
| "El producto procesa audio de punta a punta" | Demo local/Docker identificada | Solo demostrable localmente, no en el deploy público |
| "También responde texto" | Demo pública en Vercel | Verificable en vivo |
| "Define 19 herramientas permitidas; 7 apagadas por feature gate" | Lista en código/configuración y tests | Verificable en repositorio |
| "Cubre el catálogo ODEPA declarado" | Consulta automatizada con fecha y resultado | Revalidar antes de defensa |
| "Funciona bajo hardware degradado" | Perfil de 1 vCPU / 4 GB con comandos y latencia | Pendiente de capturar; issue #215 |
| "Whisper entiende habla rural con WER objetivo" | Dataset consentido, transcripciones de referencia y cálculo | **Nunca se ejecutó; sin piloto no hay vía para hacerlo** |
| "Los productores lo validaron" | Piloto terminado, muestra, método y resultados | **No disponible; el piloto no se ejecutó** |
| "PRODESAL/INDAP lo validó" | Documento o comunicación inequívoca | **No disponible; sin contacto institucional** |
| "Cumple la Ley 21.719" | Revisión jurídica con alcance explícito | **No certificado; no aplica sin datos de terceros en producción** |
| "Reduce pérdidas o mejora ingresos" | Diseño y medición causal/observacional suficiente | **No medido, no medible sin piloto** |

## 9. Evidencia pendiente para la carpeta de defensa

La carpeta no está cerrada ni cuenta con un checklist ejecutado asociado a una versión.

### Técnica

- [ ] Commit o versión de demo.
- [ ] Resultado de tests y CI asociado a esa versión.
- [ ] Smoke test de audio y texto (local) y de la demo pública (Vercel).
- [ ] Evidencia de catálogo ODEPA y fecha de sincronización.
- [ ] Ejemplo de OpenMeteo con fecha/hora.
- [ ] Prueba de hardware degradado.
- [ ] Medición reproducible de latencia, separada por etapa.
- [ ] Captura de logs saneados sin contenido libre ni hashes.
- [ ] Plan B de demo.

### Institucional y legal

- [ ] Pauta y horario oficial de defensa.
- [ ] Registro de riesgos actualizado.

No hay checklist de campo: el piloto no se ejecutó y no está planificado.

## 10. Preguntas difíciles y respuesta honesta

### "¿Ya lo usan agricultores reales?"

No. El producto está implementado pero nunca hubo piloto de campo ni respaldo institucional para
uno. Es un proyecto de portfolio con demo pública funcional, no un producto validado en terreno.

### "¿Qué precisión tiene Whisper con acento rural chileno?"

No se midió. Requeriría una muestra consentida y un piloto que no existe ni está planificado.

### "¿INDAP o PRODESAL respaldan el proyecto?"

No. Nunca hubo contacto formal ni acuerdo.

### "¿Cumple la nueva ley de datos?"

Hay controles técnicos de minimización, retención y consentimiento en el código. No aplica una
certificación porque hoy no se procesan datos personales de terceros: la demo pública no persiste
consultas y no hay WhatsApp en operación.

### "¿Por qué no usar una API comercial?"

El alcance exige stack abierto y ejecución local para controlar costo, privacidad y dependencia.
Esa decisión también impone límites de rendimiento que deben medirse en el hardware objetivo.

### "¿Por qué WhatsApp?"

Evita instalar una aplicación adicional y permite usar voz o texto en un canal conocido. Esa
hipótesis de usabilidad nunca se validó con usuarios reales.

### "¿El sistema recomienda cuándo vender o qué hacer?"

No. Entrega datos de precio y clima con fuente; la decisión pertenece al productor.

### "¿Qué pasa si no entiende tres veces?"

Primero pide repetir más despacio, luego ofrece escribir por texto y finalmente informa que el chat
no tiene atención humana. No promete que alguien llamará ni crea una cola humana inexistente.

## 11. Criterios para declarar cierre futuro

AgroVoz no debe presentarse como proyecto cerrado hasta que, como mínimo:

1. la evidencia de latencia en hardware degradado esté actualizada;
2. los riesgos de privacidad tengan resolución explícita si en algún momento se procesan datos reales;
3. INACAP haya recibido y evaluado los entregables requeridos;
4. exista una decisión sobre continuar, pivotar o considerar cerrado el desarrollo activo.

## 12. Conclusión para la defensa

La conclusión recomendada no es "validamos el producto con productores", sino:

> Construimos la base funcional de AgroVoz y podemos demostrar su recorrido técnico, incluyendo una
> demo pública gratuita funcionando hoy. Nunca ejecutamos un piloto de campo: el respaldo
> institucional que lo hacía posible (Desafío Crea INACAP) no continuó. Este es un proyecto de
> portfolio con evidencia técnica real, no un producto validado con agricultores reales.

## 13. Fuentes

- [`AGENTS.md`](../../AGENTS.md): estado operativo, alcance, restricciones y deuda conocida.
- [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) decisión 34: deploy público vigente.
- [`docs/pmbok/README.md`](README.md): método de defensa basado en evidencia y varianza honesta.
- [`docs/negocio/01-resumen-ejecutivo.md`](../negocio/01-resumen-ejecutivo.md).
- [`docs/negocio/10-equipo.md`](../negocio/10-equipo.md).

**Control de afirmaciones:** toda cifra o resultado añadido después de esta versión debe incluir
fuente, fecha, método y alcance.
