# Métricas pre/post piloto — AgroVoz Traiguén

> Cuestionarios de línea base (inicio) y cierre (fin) del piloto.  
> Son instrumentos en papel para consolidación manual. El dashboard admin (#97)
> no persiste estas respuestas ni las vincula automáticamente con sus métricas.

---

## Parte A — Cuestionario de línea base (pre-piloto)

Aplicar durante el onboarding, antes de que el productor use AgroVoz por primera vez.

### Datos de aplicación

| Campo | Información |
|---|---|
| Fecha | ____ / ____ / ______ |
| Encargado AgroVoz | ______________________________ |
| Código de participante | ______________________________ |
| Comuna | ______________________________ |

### 1. ¿Cómo se informa de precios hoy?

Marque todas las opciones que apliquen:

- [ ] Escucha precios en la feria/local.
- [ ] Le pregunta a otros productores.
- [ ] Le dice el intermediario/comprador.
- [ ] Ve precios en la tele/radio.
- [ ] Usa internet (páginas, redes sociales).
- [ ] No se informa.
- [ ] Otro: ______________________________

### 2. ¿Con qué frecuencia se informa de precios?

- [ ] Todos los días.
- [ ] Varias veces por semana.
- [ ] Una vez por semana.
- [ ] Una vez al mes.
- [ ] Casi nunca.

### 3. ¿Cuánta confianza tiene al negociar el precio de sus productos?

1 = Ninguna confianza.  
5 = Mucha confianza.

- [ ] 1
- [ ] 2
- [ ] 3
- [ ] 4
- [ ] 5

¿Por qué?

________________________________________________________________________________

### 4. ¿Usa WhatsApp?

- [ ] Sí, todos los días.
- [ ] Sí, varias veces por semana.
- [ ] Sí, de vez en cuando.
- [ ] No sabe usarlo bien.
- [ ] No tiene WhatsApp.

### 5. ¿Alguna vez envió un audio por WhatsApp?

- [ ] Sí, seguido.
- [ ] Sí, alguna vez.
- [ ] No, pero sabe cómo.
- [ ] No, necesita que le enseñen.

---

## Parte B — Cuestionario de cierre (post-piloto)

Aplicar en la última semana del piloto, después de 4 semanas de uso.

### Datos de aplicación

| Campo | Información |
|---|---|
| Fecha | ____ / ____ / ______ |
| Encargado AgroVoz | ______________________________ |
| Código de participante | ______________________________ |

### 1. ¿Negoció diferente gracias a AgroVoz?

- [ ] Sí, pude exigir un mejor precio.
- [ ] Sí, pero la diferencia fue pequeña.
- [ ] No, el comprador no aceptó.
- [ ] No, todavía no negocié con los datos.
- [ ] No sé / no recuerdo.

Explique brevemente:

________________________________________________________________________________

### 2. ¿Utilizó los datos de AgroVoz para decisiones concretas?

Marque todas las que apliquen:

- [ ] Para fijar precio de venta.
- [ ] Para decidir cuándo cosechar.
- [ ] Para decidir cuándo vender.
- [ ] Para planificar labores según el clima.
- [ ] Para conversar con otros productores.
- [ ] No los usé para nada concreto.
- [ ] Otro: ______________________________

### 3. ¿Qué tan útil fue AgroVoz en su trabajo diario?

1 = Nada útil.  
5 = Muy útil.

- [ ] 1
- [ ] 2
- [ ] 3
- [ ] 4
- [ ] 5

### 4. ¿Recomendaría AgroVoz a otro productor?

- [ ] Sí, definitivamente.
- [ ] Sí, probablemente.
- [ ] No sé todavía.
- [ ] Probablemente no.
- [ ] Definitivamente no.

¿Por qué?

________________________________________________________________________________

### 5. ¿Qué información le gustaría que AgroVoz respondiera en el futuro?

________________________________________________________________________________

________________________________________________________________________________

---

## Vínculo con las 5 métricas del dashboard admin (#97)

El código de participante se copia en los formularios pre y post para permitir una
consolidación manual. No anotar nombre ni número de WhatsApp en esta planilla de
métricas. La tabla siguiente separa la definición deseada de lo que hoy entrega el
dashboard: las respuestas pre/post **no se guardan en el sistema ni se contrastan
automáticamente** con las consultas.

Para el piloto, el equipo debe entregar al endpoint admin la fecha de inicio y la
fecha de cierre de la ventana de cuatro semanas (`pilot_started_at` inclusivo y
`pilot_ended_at` exclusivo, con zona horaria). El backend y las vistas/exportaciones
aplican ese filtro explícito; sin ambas fechas el cálculo queda fail-closed y no
lee consultas históricas. El vínculo automático con formularios pre/post, la
identificación verificable de participantes y la evidencia de operación real
siguen pendientes.

| Métrica del dashboard | Definición del piloto | Estado y validación honesta |
|---|---|---|
| **Productores activos** | COUNT(DISTINCT `phone_hash`) con 3+ consultas dentro de la ventana de cuatro semanas. | El endpoint admin y las vistas/exportaciones aplican `pilot_started_at`/`pilot_ended_at`; el resultado no acredita por sí solo participantes reales ni operación del piloto. |
| **Consultas promedio por productor** | Consultas de la ventana / productores de la ventana. | Se calcula sobre la ventana entregada; la comparación con la bitácora y los formularios de cuatro semanas es manual y no constituye linkage automático. |
| **% consultas útiles** | En dashboard: `feedback="util"` / feedback no nulo × 100. | Es un indicador binario de feedback, no equivalente a la escala 1–5 del cuestionario. Reportar ambos por separado; no inventar una conversión. |
| **Latencia promedio** | Promedio de `latency_ms` de entregas del piloto, comparado con target <15 segundos. | La ventana se filtra con `pilot_started_at`/`pilot_ended_at`, pero el cálculo no acredita operación real ni reemplaza la revisión de entregas exitosas y evidencia fechada. |
| **Decisiones productivas** | Conteo de consultas marcadas administrativamente como `decision_productiva=true`. | El toggle administrativo no se alimenta automáticamente de la respuesta post ni de la bitácora. El cuestionario se consolida como evidencia cualitativa separada. |

### Procedimiento manual y límites

1. Asignar un código de participante y repetirlo en los formularios pre/post y en la
   bitácora. La tabla de correspondencia con la identidad se guarda fuera del
   repositorio, con custodia todavía pendiente de aprobación en el acuerdo.
2. Registrar fechas de inicio y cierre, excluir consultas fuera de la ventana y
   anotar cuántos formularios tienen respuestas válidas para cada pregunta.
3. Consolidar por separado: métricas del dashboard, respuestas pre/post y bitácora.
   Entregar siempre `pilot_started_at` y `pilot_ended_at`; no reemplazar una
   ventana ausente por el histórico.
4. Este procedimiento describe una revisión manual; no demuestra que exista
   persistencia ni linkage automático de cuestionarios, ni evidencia que el
   piloto haya operado en terreno.

---

## Consolidación rápida

| Indicador | Pre-piloto | Post-piloto | Diferencia |
|---|---|---|---|
| Confianza al negociar (1–5) | | | |
| Frecuencia de información de precios | | | |
| Utilidad percibida (1–5) | N/A | | |
| Recomendación favorable (% respuestas "sí, definitivamente" o "sí, probablemente") | N/A | | |

**Denominadores:** cada porcentaje usa solo respuestas válidas a esa pregunta;
las respuestas en blanco, “no sé/no recuerdo” y formularios incompletos se informan
como faltantes y no se cuentan como “no”. Para recomendación favorable, el
denominador es el total de respuestas válidas de la pregunta 4.

**Interpretación:** estos formularios describen percepción y uso declarado; no
prueban por sí solos impacto causal ni reemplazan la medición técnica de entrega,
latencia o uso real.

---

## Notas del encargado

________________________________________________________________________________

________________________________________________________________________________
