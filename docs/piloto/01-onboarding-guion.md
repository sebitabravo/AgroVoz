# Guion de onboarding presencial — AgroVoz Piloto Traiguén

> Duración: 15–20 minutos por productor.  
> Objetivo: que el productor entienda qué es AgroVoz, confíe en el sistema, haga su primera consulta real y firme el consentimiento de datos.

> **Estado del guion:** protocolo propuesto para un piloto de cuatro semanas.
> La visita, la demo, la sesión de Open-WA y la entrega de mensajes deben
> quedar respaldadas por evidencia fechada; este formulario no prueba que hayan
> ocurrido.

---

## Datos de la visita

| Campo | Información |
|---|---|
| Fecha | ____ / ____ / ______ |
| Hora inicio | ______ : ______ |
| Hora término | ______ : ______ |
| Nombre del productor | ______________________________ |
| Lugar (predio/sector) | ______________________________ |
| Nombre del encargado AgroVoz | ______________________________ |
| Número de WhatsApp registrado | +56 9 _____________ |
| Comuna registrada (#89) | Traiguén |

---

## Checklist de materiales

Antes de salir al terreno, marcar cada ítem:

- [ ] Celular del encargado con batería suficiente.
- [ ] Número de WhatsApp de AgroVoz disponible y verificado para la demo (registrar fecha/hora y resultado).
- [ ] Instructivo impreso (`docs/piloto/05-instructivo-impreso.md`) para entregar al productor.
- [ ] Acuerdo de Uso y Consentimiento impreso (`docs/piloto/06-acuerdo-consentimiento.md`).
- [ ] Bitácora del productor (`docs/piloto/02-bitacora-productor.md`).
- [ ] Lapicero para firmas.
- [ ] Conectividad móvil estable para la demo.

---

## Guion paso a paso

### 1. Saludo y presentación (2 min)

> “Buenos días, don/ña __________. Soy __________ del equipo AgroVoz, un proyecto de INACAP Temuco para el Desafío Crea INACAP 2026. Estamos acá para mostrarle una herramienta que le puede servir para conocer precios de la papa y el clima, solo con un audio de WhatsApp.”

- Confirmar identidad del productor.
- Agradecer por recibirnos.
- Explicar que es una prueba piloto propuesta de 4 semanas, sin costo para el productor, sujeta a confirmación de fechas.

### 2. ¿Qué es AgroVoz? (3 min)

> “AgroVoz es un asistente de voz que funciona dentro de WhatsApp. Usted nos envía un audio con una pregunta, por ejemplo: ‘¿Cuánto está la papa hoy en Traiguén?’, y el sistema le responde con otro audio con el precio oficial de ODEPA o con el pronóstico del clima. No tiene que instalar nada nuevo, solo usar WhatsApp.”

Puntos clave:

- No necesita saber leer ni escribir: todo es por voz.
- No instala aplicaciones: usa WhatsApp que ya tiene.
- Los datos de precios vienen de ODEPA (oficial).
- El clima viene de un servicio meteorológico gratuito.
- **AgroVoz no le dice qué hacer en su predio**: solo entrega precios y clima.

### 3. Demo en vivo (5 min)

1. Si el canal fue verificado, el encargado graba un audio en el celular del productor (o en el propio) dirigido al número de AgroVoz.
2. Ejemplo de audio:  
   > “Hola AgroVoz, ¿cuánto está la papa en Traiguén?”
3. Esperar la respuesta de audio junto al productor y registrar si llegó, falló o no fue posible ejecutar la prueba.
4. Reproducir la respuesta en altavoz.
5. Preguntar:  
   > “¿Se entendió bien la respuesta? ¿El audio se escuchó claro?”
6. Anotar impresión inicial en la bitácora.

### 4. Primera consulta guiada del productor (5 min)

1. Pedirle al productor que grabe su primer audio.
2. Sugerir una de estas frases:
   - “¿Cuánto está la papa?”
   - “¿Cómo va a estar el clima mañana?”
   - “Dame el precio de la papa en Traiguén.”
3. Esperar la respuesta.
4. Si falla (no entiende, tarda mucho, error), no preocupar al productor. Decir:  
   > “Eso nos ayuda a mejorar el sistema. Vamos a anotarlo.”
5. Registrar el intento en la bitácora.

### 5. Registro de comuna (#89) (2 min)

> “Para que AgroVoz sepa de qué mercado darle el precio, vamos a registrar su comuna. En este piloto trabajamos con Traiguén.”

Acciones:

- [ ] Confirmar que el productor es de Traiguén o comuna cercana.
- [ ] Registrar comuna en el sistema (feature #89).
- [ ] Explicar que, durante el piloto, los precios serán de Traiguén.

| Comuna registrada | Mercado por defecto |
|---|---|
| Traiguén | Traiguén |

### 6. Acuerdo de Uso y Consentimiento de Datos (3 min)

1. Entregar impreso el `06-acuerdo-consentimiento.md`.
2. Leerlo en voz alta si el productor lo solicita.
3. Explicar en simple:
   - Se graba el audio que usted envía.
   - El diseño técnico prevé borrar el audio operativo en menos de 24 horas del servidor; el equipo debe verificar y registrar ese resultado.
   - Si existe `dataset_consent`, puede conservarse una transcripción seudonimizada mediante código/hash; no es anónima y puede contener datos personales incidentales.
   - Esa transcripción sirve para mejorar el sistema en el futuro.
   - Puede solicitar retirar su consentimiento; el equipo registra la solicitud y explica su alcance. El borrado retroactivo del dataset queda pendiente de un procedimiento verificable.
4. Si acepta, completar el registro de consentimiento con la versión del texto, fecha/hora, modalidad y operador que recibió la firma.
5. Entregar una copia al productor y anotar cómo y dónde queda bajo custodia el original.

La transcripción y cualquier audio retenido para dataset se describen como
**seudonimizados** mediante un código/hash, no como anónimos. Una transcripción
puede contener datos personales incidentales. El audio operativo se elimina del
VPS en menos de 24 horas según el diseño técnico; el formulario no debe prometer
que la revocación borra automáticamente muestras de dataset ya retenidas si esa
operación no tiene evidencia de implementación y auditoría.

### 7. Registrar consentimientos separados (#96) (2 min)

- [ ] Explicar y registrar `dataset_consent` como opt-in específico para copiar audio/transcripción al dataset de mejora.
- [ ] Si no acepta, registrar `dataset_consent = false`; puede usar AgroVoz y el audio operativo sigue sujeto a su eliminación temporal.
- [ ] Explicar y registrar `alert_consent` de forma independiente: autoriza avisos proactivos, no el dataset.
- [ ] Si no acepta alertas o revoca ese opt-in, registrar `alert_consent = false` y verificarlo antes de cualquier envío.
- [ ] Registrar el resultado de ambos flags, la fecha/hora y el operador; un cambio de flag no reemplaza la custodia del consentimiento firmado.

### 8. Despedida y próximos pasos (1 min)

> “Don/ña __________, muchas gracias por participar. Durante las próximas 4 semanas previstas usted puede enviar audios cuando quiera, si confirmamos el canal. Nosotros lo vamos a llamar una vez por semana para saber cómo le va. Cualquier problema, nos avisa por el canal acordado.”

- Entregar instructivo impreso (`05-instructivo-impreso.md`).
- Entregar bitácora del productor (`02-bitacora-productor.md`).
- Confirmar día y hora tentativa del primer check-in semanal.

---

## Checklist de cierre de la visita

- [ ] Demo realizada con éxito o falla documentada.
- [ ] Fecha/hora, versión del sistema/canal y resultado de la demo registrados; una demo no se marca como exitosa por defecto.
- [ ] Productor realizó su primera consulta.
- [ ] Comuna registrada (#89).
- [ ] Acuerdo firmado por el productor.
- [ ] Copia del acuerdo entregada al productor.
- [ ] Versión, fecha/hora, modalidad, operador receptor y custodia del acuerdo registrados.
- [ ] `dataset_consent` registrado de forma independiente (#96).
- [ ] `alert_consent` explicado, registrado y verificado de forma independiente.
- [ ] Instructivo y bitácora entregados.
- [ ] Próximo check-in agendado.

---

## Notas de la visita

Espacio para anotar observaciones, dudas del productor, problemas técnicos o comentarios que no caben en los formularios.

________________________________________________________________________________

## Custodia y minimización de la hoja

El responsable de la visita entrega el original al custodio designado y registra
esa entrega. El custodio conserva la hoja en lugar cerrado, limita el acceso al
equipo autorizado y mantiene un registro de devolución o destrucción segura al
vencer el plazo aprobado. Usar código de participante y los mínimos datos de
contacto necesarios; no dejar copias en teléfonos personales ni afirmar que la
hoja está anonimizada. El plazo, custodio, suplente y canal para ejercer derechos
deben quedar definidos antes de iniciar el piloto.

________________________________________________________________________________

________________________________________________________________________________
