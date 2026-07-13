# Guion de onboarding presencial — AgroVoz Piloto Traiguén

> Duración: 15–20 minutos por productor.  
> Objetivo: que el productor entienda qué es AgroVoz, confíe en el sistema, haga su primera consulta real y firme el consentimiento de datos.

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
- [ ] Número de WhatsApp de AgroVoz configurado y funcionando.
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
- Explicar que es una prueba piloto de 4 semanas, sin costo.

### 2. ¿Qué es AgroVoz? (3 min)

> “AgroVoz es un asistente de voz que funciona dentro de WhatsApp. Usted nos envía un audio con una pregunta, por ejemplo: ‘¿Cuánto está la papa hoy en Traiguén?’, y el sistema le responde con otro audio con el precio oficial de ODEPA o con el pronóstico del clima. No tiene que instalar nada nuevo, solo usar WhatsApp.”

Puntos clave:

- No necesita saber leer ni escribir: todo es por voz.
- No instala aplicaciones: usa WhatsApp que ya tiene.
- Los datos de precios vienen de ODEPA (oficial).
- El clima viene de un servicio meteorológico gratuito.
- **AgroVoz no le dice qué hacer en su predio**: solo entrega precios y clima.

### 3. Demo en vivo (5 min)

1. El encargado graba un audio en el celular del productor (o en el propio) dirigido al número de AgroVoz.
2. Ejemplo de audio:  
   > “Hola AgroVoz, ¿cuánto está la papa en Traiguén?”
3. Esperar la respuesta de audio junto al productor.
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

### 6. Acuerdo de Uso y Consentimiento de Datos (Ley 21.719) (3 min)

1. Entregar impreso el `06-acuerdo-consentimiento.md`.
2. Leerlo en voz alta si el productor lo solicita.
3. Explicar en simple:
   - Se graba el audio que usted envía.
   - El audio se borra en menos de 24 horas del servidor.
   - Se guarda una transcripción anónima (con un código, no su nombre ni número).
   - Esa transcripción sirve para mejorar el sistema en el futuro.
   - Puede retirar su consentimiento cuando quiera.
4. Si acepta, firmar el acuerdo.
5. Dejar una copia al productor.

### 7. Habilitar dataset_consent (#96) (1 min)

- [ ] Si el productor firma el acuerdo, marcar `dataset_consent = true` en el registro del sistema (feature #96).
- [ ] Si no firma, marcar `dataset_consent = false` y explicar que igual puede usar AgroVoz, pero sus audios no se usarán para entrenar el modelo.

### 8. Despedida y próximos pasos (1 min)

> “Don/ña __________, muchas gracias por participar. Durante las próximas 4 semanas usted puede enviar audios cuando quiera. Nosotros lo vamos a llamar una vez por semana para saber cómo le va. Cualquier problema, nos avisa por WhatsApp.”

- Entregar instructivo impreso (`05-instructivo-impreso.md`).
- Entregar bitácora del productor (`02-bitacora-productor.md`).
- Confirmar día y hora tentativa del primer check-in semanal.

---

## Checklist de cierre de la visita

- [ ] Demo realizada con éxito o falla documentada.
- [ ] Productor realizó su primera consulta.
- [ ] Comuna registrada (#89).
- [ ] Acuerdo firmado por el productor.
- [ ] Copia del acuerdo entregada al productor.
- [ ] `dataset_consent` registrado (#96).
- [ ] Instructivo y bitácora entregados.
- [ ] Próximo check-in agendado.

---

## Notas de la visita

Espacio para anotar observaciones, dudas del productor, problemas técnicos o comentarios que no caben en los formularios.

________________________________________________________________________________

________________________________________________________________________________

________________________________________________________________________________
