# Métricas pre/post piloto — AgroVoz Traiguén

> Cuestionarios de línea base (inicio) y cierre (fin) del piloto.  
> Los resultados se contrastan con las 5 métricas del dashboard admin (#97).

---

## Parte A — Cuestionario de línea base (pre-piloto)

Aplicar durante el onboarding, antes de que el productor use AgroVoz por primera vez.

### Datos de aplicación

| Campo | Información |
|---|---|
| Fecha | ____ / ____ / ______ |
| Encargado AgroVoz | ______________________________ |
| Nombre del productor | ______________________________ |
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
| Nombre del productor | ______________________________ |

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

Las respuestas del productor se contrastan con las métricas automáticas del sistema. Esto permite validar si lo que dice el productor se refleja en el uso real.

| Métrica del dashboard | ¿Qué mide? | Cómo se valida con el piloto |
|---|---|---|
| **Productores activos** | COUNT(DISTINCT phone_hash) con 3+ consultas en 4 semanas. Mide adopción real. | Está automático en el sistema. Se reporta al cierre (cuestionario post #2). |
| **Consultas promedio por productor** | AVG(consultas por phone_hash). Mide intensidad de uso. | Se compara con lo reportado en bitácora (02-bitacora: 4 semanas de registros). |
| **% consultas útiles** | COUNT(feedback="útil") / COUNT(feedback) * 100. Mide valor percibido. | Se recoge en 02-bitacora cada semana (escala 1-5 de utilidad) y en 04-metricas post #3. |
| **Latencia promedio** | AVG(tiempo respuesta) vs target <15 segundos. Mide velocidad del sistema. | Se valida en 03-checkin semanal ("¿Tardó mucho?") y en 02-bitacora (notas de problemas técnicos). |
| **Decisiones productivas** | COUNT(feedback="usé para negociar/vender/planificar"). Mide impacto real. | Se recoge en 04-metricas cuestionario post #2 ("¿Utilizó los datos?") y en 03-checkin semanal #4. |

---

## Consolidación rápida

| Indicador | Pre-piloto | Post-piloto | Diferencia |
|---|---|---|---|
| Confianza al negociar (1–5) | | | |
| Frecuencia de información de precios | | | |
| Utilidad percibida (1–5) | N/A | | |
| Recomendación (% sí) | N/A | | |

---

## Notas del encargado

________________________________________________________________________________

________________________________________________________________________________
