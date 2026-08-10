# Check-in semanal — AgroVoz Piloto Traiguén

> Pauta para un check-in semanal de 10 minutos por productor. La modalidad
> (llamada telefónica, llamada de WhatsApp, mensaje de voz o presencial) se
> acuerda y registra antes de comenzar; si no responde, registrar intento y
> reprogramación, sin inventar asistencia.
> Objetivo: detectar problemas tempranos, medir comprensión y recopilar mejoras.

> **Estado:** formulario operativo propuesto para cuatro semanas. La pauta no
> prueba que el check-in se haya realizado ni que sus resultados alimenten
> automáticamente el dashboard.

---

## Datos del check-in

| Campo | Información |
|---|---|
| Número de semana | 1 / 2 / 3 / 4 |
| Fecha | ____ / ____ / ______ |
| Hora inicio | ______ : ______ |
| Hora término | ______ : ______ |
| Encargado AgroVoz | ______________________________ |
| Código de participante | ______________________________ |
| Últimos 4 dígitos del WhatsApp (solo si es necesario) | __________ |
| Duración real | ______ min |
| Modalidad y canal acordado | ______________________________ |
| Resultado | Realizado / No respondió / Reprogramado |

---

## Apertura (1 min)

> “Hola don/ña __________, ¿cómo está? Soy __________ de AgroVoz. Le llamamos para saber cómo le ha ido con el asistente de voz esta semana. ¿Tiene 10 minutos?”

Registrar por separado, sin inferir el estado desde la conversación:

- `dataset_consent`: [ ] activo [ ] no activo [ ] revocado [ ] no verificado
- `alert_consent`: [ ] activo [ ] no activo [ ] revocado [ ] no verificado
- `history_consent`: [ ] activo [ ] no activo [ ] revocado [ ] no verificado

Estos son opt-ins independientes. `alert_consent` autoriza avisos proactivos y
no autoriza dataset ni historial; si aparece como no verificado, no se debe
enviar un aviso por asumir que existe consentimiento.

---

## Preguntas clave

Las escalas de claridad y recomendación son respuestas manuales del check-in.
No son la métrica automática de `feedback=util/no_util`; reportarlas por
separado dentro de la ventana de cuatro semanas y con el código del participante.

### 1. ¿Qué entendió bien? (2 min)

Preguntar:
> “Cuando AgroVoz le respondió, ¿entendió la información? ¿Le pareció clara?”

Respuesta / ejemplo del productor:

________________________________________________________________________________

Marcar según escala:

- [ ] 5 — Todo muy claro.
- [ ] 4 — Casi todo claro.
- [ ] 3 — Algo claro, algo confuso.
- [ ] 2 — Muy confuso.
- [ ] 1 — No entendió nada.

### 2. ¿Qué no le quedó claro? (2 min)

Preguntar:
> “¿Hubo alguna respuesta que no entendiera? ¿O alguna palabra que no le sonó?”

Respuesta:

________________________________________________________________________________

________________________________________________________________________________

### 3. ¿Qué quiso preguntar y no pudo? (2 min)

Preguntar:
> “Esta semana, ¿intentó preguntar algo y AgroVoz no supo responderle? ¿O le respondió otra cosa?”

Respuesta:

________________________________________________________________________________

________________________________________________________________________________

Marcar si ocurrió:

- [ ] El sistema no entendió el audio.
- [ ] Respondió información incorrecta.
- [ ] Respondió algo que no tenía relación con la pregunta.
- [ ] El productor no supo cómo formular la pregunta.
- [ ] Otra: ______________________________

### 4. ¿Usó los datos para algo concreto? (2 min)

Preguntar:
> “¿Usó la información de precios o clima para conversar con un comprador, decidir cuándo cosechar o planificar algo?”

Respuesta:

________________________________________________________________________________

- [ ] Sí, para negociar precio.
- [ ] Sí, para decidir cuándo vender/cosechar.
- [ ] Sí, para planificar riego/fumigación por el clima.
- [ ] No, solo por curiosidad.
- [ ] No, no confía en la información.
- [ ] Otra: ______________________________

### 5. ¿Recomendaría AgroVoz? (1 min)

Preguntar:
> “Si un vecino suyo le preguntara, ¿le recomendaría usar AgroVoz?”

- [ ] Sí, sin dudar.
- [ ] Sí, con algunas observaciones.
- [ ] No sé todavía.
- [ ] No.

Observaciones:

________________________________________________________________________________

---

## Registro de problemas técnicos

| Fecha/hora aprox. | Problema | Impacto | ¿Resuelto? | Acción de seguimiento |
|---|---|---|---|---|
| | No llegó respuesta | Alto / Medio / Bajo | Sí / No | |
| | Tardó mucho (>15 seg) | Alto / Medio / Bajo | Sí / No | |
| | Audio de respuesta inaudible | Alto / Medio / Bajo | Sí / No | |
| | Transcripción incorrecta | Alto / Medio / Bajo | Sí / No | |
| | Error de conexión | Alto / Medio / Bajo | Sí / No | |
| | Otro: | Alto / Medio / Bajo | Sí / No | |

---

## Acciones de seguimiento

| Acción | Responsable y suplente | Fecha límite | Estado |
|---|---|---|---|
| | | | Pendiente / En progreso / Listo |
| | | | Pendiente / En progreso / Listo |
| | | | Pendiente / En progreso / Listo |

---

## Cierre

> “Muchas gracias don/ña __________. Si la próxima semana tiene algún problema,
> nos avisa por el canal acordado. Que le vaya bien.”

---

## Notas adicionales

________________________________________________________________________________

## Custodia y cierre

El responsable entrega el formulario al custodio designado y registra la
revisión. Las hojas se mantienen en un lugar cerrado, con acceso solo para el
equipo autorizado, durante el plazo aprobado; al cierre se devuelven o destruyen
de forma segura y se registra la acción. No incluir nombres, números completos
ni notas sensibles si no son necesarios. El custodio, suplente, plazo y canal
para ejercer derechos son campos obligatorios del procedimiento y quedan
pendientes de asignación antes del piloto.

________________________________________________________________________________
