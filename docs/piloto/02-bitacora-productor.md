# Bitácora del productor — AgroVoz Piloto Traiguén

> Esta bitácora la completa el productor con apoyo del equipo AgroVoz.  
> Sirve para capturar la utilidad percibida de cada consulta. Complementa los registros automáticos del sistema (#97).

> **Estado:** formulario manual para cuatro semanas previstas. Sus anotaciones
> no se ingieren automáticamente ni demuestran resultados del piloto. Usar un
> código de participante y minimizar los datos identificatorios.

---

## Datos del productor

| Campo | Información |
|---|---|
| Código de participante | ______________________________ |
| Comuna | ______________________________ |
| Últimos 4 dígitos del WhatsApp (solo si es necesario) | __________ |
| Fecha inicio piloto | ____ / ____ / ______ |
| Fecha término piloto | ____ / ____ / ______ |

---

## Instrucciones de uso

1. Durante las cuatro semanas previstas, cada vez que use AgroVoz anote la fecha, la hora y lo que preguntó.
2. Cuando llegue la respuesta, escriba con sus palabras qué le dijo el sistema.
3. Marque si la respuesta le sirvió o no.
4. Si tuvo algún problema (no llegó la respuesta, no se entendió, tardó mucho), escríbalo en “Notas”.
5. Al final de cada semana, el equipo AgroVoz le pedirá revisar esta bitácora en el check-in.

---

## Registro de consultas

### Semana 1

| Día | Fecha | Hora | ¿Qué preguntó? | ¿Qué le respondió? | ¿Le sirvió? | Notas |
|---|---|---|---|---|---|---|
| Lun | | | | | Sí / No | |
| Mar | | | | | Sí / No | |
| Mié | | | | | Sí / No | |
| Jue | | | | | Sí / No | |
| Vie | | | | | Sí / No | |
| Sáb | | | | | Sí / No | |
| Dom | | | | | Sí / No | |

### Semana 2

| Día | Fecha | Hora | ¿Qué preguntó? | ¿Qué le respondió? | ¿Le sirvió? | Notas |
|---|---|---|---|---|---|---|
| Lun | | | | | Sí / No | |
| Mar | | | | | Sí / No | |
| Mié | | | | | Sí / No | |
| Jue | | | | | Sí / No | |
| Vie | | | | | Sí / No | |
| Sáb | | | | | Sí / No | |
| Dom | | | | | Sí / No | |

### Semana 3

| Día | Fecha | Hora | ¿Qué preguntó? | ¿Qué le respondió? | ¿Le sirvió? | Notas |
|---|---|---|---|---|---|---|
| Lun | | | | | Sí / No | |
| Mar | | | | | Sí / No | |
| Mié | | | | | Sí / No | |
| Jue | | | | | Sí / No | |
| Vie | | | | | Sí / No | |
| Sáb | | | | | Sí / No | |
| Dom | | | | | Sí / No | |

### Semana 4

| Día | Fecha | Hora | ¿Qué preguntó? | ¿Qué le respondió? | ¿Le sirvió? | Notas |
|---|---|---|---|---|---|---|
| Lun | | | | | Sí / No | |
| Mar | | | | | Sí / No | |
| Mié | | | | | Sí / No | |
| Jue | | | | | Sí / No | |
| Vie | | | | | Sí / No | |
| Sáb | | | | | Sí / No | |
| Dom | | | | | Sí / No | |

---

## Utilidad percibida

Al final de cada semana, el productor responde con una nota del 1 al 5:

1 = No me sirvió en nada.  
5 = Me sirvió mucho.

Esta nota es una medida manual de utilidad percibida. No equivale
automáticamente a la métrica del dashboard `feedback=util/no_util` ni se debe
convertir en un porcentaje >80% sin definir antes el denominador, la unidad de
análisis y el procedimiento de digitación. Reportar ambas medidas por separado.

| Semana | Nota 1–5 | ¿Por qué? |
|---|---|---|
| 1 | | |
| 2 | | |
| 3 | | |
| 4 | | |

---

## Comentarios libres

> ¿Qué le gustó? ¿Qué cambiaría? ¿Qué le falta?

________________________________________________________________________________

________________________________________________________________________________

________________________________________________________________________________

---

## Para uso del equipo AgroVoz

Esta bitácora se revisa en el check-in semanal (`docs/piloto/03-checkin-semanal.md`) y se contrasta con las métricas automáticas del dashboard admin (#97), siempre usando las fechas de inicio y término y el conjunto de participantes del piloto.

**Automático (dashboard):** Cuando existe una consulta registrada, el sistema puede aportar consulta, latencia, intents detectados, productos consultados y feedback inicial; esto no prueba por sí solo que el registro pertenezca al piloto.

**Manual (esta bitácora):** El productor completa a mano su experiencia: frases exactas, si le sirvió, problemas encontrados, y su nota de utilidad percibida (1-5). Esta información NO la recoge el sistema automáticamente.

**Cómo se usan juntas:** Por defecto se comparan conteos, fechas, latencia y
estado de entrega dentro de la ventana del piloto; la bitácora no alimenta la
fórmula automática. El texto libre de una consulta solo puede contrastarse si
existe `history_consent` independiente, acceso autorizado y una copia conservada
antes de la redacción del staging; de lo contrario se registra la limitación y
no se afirma que hubo coincidencia textual.

## Custodia de la bitácora

El equipo debe designar un custodio y suplente antes de entregar la hoja. La
bitácora se guarda en un lugar cerrado, con acceso limitado y registro de quién
la revisó; no se fotografía ni se copia a dispositivos personales. El plazo de
retención, devolución o destrucción segura al cierre y el canal para ejercer
derechos deben quedar definidos y registrados. La bitácora física no queda
protegida por los TTL del backend.
