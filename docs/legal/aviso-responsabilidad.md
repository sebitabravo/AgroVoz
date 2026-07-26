# Aviso de responsabilidad — AgroVoz

> Estado: **implementado en el producto** desde el primer contacto por WhatsApp.
> Aplica a los dos canales de entrada: audio y texto.

## Por qué existe

AgroVoz entrega precios de ODEPA y clima de Open-Meteo. Un productor puede tomar una decisión de
venta apoyándose en ese dato y perder plata si el dato estaba desactualizado, si el sistema entendió
mal la pregunta, o —el caso más probable— si confundió el **precio mayorista** con el precio que le
van a pagar en su predio.

Sin un descargo explícito y visible, esa pérdida es atribuible al equipo. Y mientras no haya sociedad
constituida, **la responsabilidad recae sobre las personas naturales del equipo**, no sobre un
patrimonio separado.

Tres factores agravan el punto:

1. **El equipo no tiene formación agronómica.** Por eso el system prompt le prohíbe al LLM emitir
   recomendaciones agrícolas. El aviso hace explícita esa misma frontera hacia el productor.
2. **La brecha mayorista/predio es real y grande.** ODEPA publica terminales como Lo Valledor. En
   Traiguén el intermediario ofrece bastante menos, por transporte e intermediación. Si el productor
   no sabe esto, va a concluir que AgroVoz le miente.
3. **El perfil del usuario.** Persona mayor, baja alfabetización digital, que no va a leer términos
   y condiciones en un sitio web.

## Dónde aparece

| Lugar | Formato | Cuándo |
|---|---|---|
| Primer mensaje de WhatsApp | Texto | Primer contacto de cada número, en ambos canales |
| Acuerdo de consentimiento del piloto | Sección 6 | Onboarding presencial, leído en voz alta |
| Guion de onboarding | Verbal | Onboarding presencial |

**Va como texto y no como audio a propósito.** Un descargo legal leído por voz sintética es inusable:
dura demasiado, no se puede releer y el productor lo va a saltar. En texto queda en el chat, se puede
releer y se le puede mostrar a un familiar.

## El texto

Es la constante `WELCOME_DISCLAIMER_TEXT` en `backend/app/services/pipeline_service.py`. **Esa es la
fuente de verdad**; lo que sigue es una copia para referencia.

```
⚠️ Antes de empezar, algo importante:

• AgroVoz te entrega precios de ODEPA y clima de Open-Meteo. Son datos oficiales,
  pero son informacion, NO una recomendacion.
• Los precios de ODEPA son de mercados mayoristas (como Lo Valledor en Santiago).
  En tu predio te van a ofrecer menos, porque incluye transporte e intermediacion.
  El dato te sirve para saber cual es el piso del mercado al negociar.
• El dato puede tener horas de antiguedad. Verifica siempre con tu comprador.
• AgroVoz no se hace responsable de las decisiones de venta que tomes.

Para asesoria tecnica habla con tu extensionista de PRODESAL o con INDAP.
```

## Cómo está implementado

- `WELCOME_DISCLAIMER_TEXT` — `backend/app/services/pipeline_service.py`
- `AudioService._enviar_aviso_responsabilidad()` — `backend/app/services/audio_service.py`
- `AudioResponse.es_primer_contacto` — `backend/app/schemas/pipeline.py`
- Tests — `backend/tests/test_aviso_responsabilidad.py`

Dos decisiones de implementación que conviene no revertir sin pensarlo:

1. **La detección de primer contacto no depende de `generar_audio`.** Antes sí, y el efecto era que
   quien escribía nunca era detectado como primer contacto y por lo tanto nunca recibía el aviso.
2. **El aviso se envía aunque falle el audio de bienvenida.** Están en bloques separados. Si el envío
   del aviso falla, se registra un warning y la consulta sigue: el aviso importa, pero no puede dejar
   al productor sin respuesta.

## Atribución de fuentes

**Open-Meteo se publica bajo CC BY 4.0, que exige atribución.** El aviso la cumple al nombrar la
fuente. Verificar que se mantenga si el texto se reescribe.

Para **ODEPA** no se encontró un documento de licencia publicado. Son datos públicos bajo la Ley
20.285 de Transparencia, pero eso **no equivale a autorización expresa de uso comercial ni de
redistribución**. Pendiente antes de cobrarle a cualquier institución: consultar formalmente a ODEPA
si el uso comercial y la redistribución requieren licencia o atribución específica.

## Pendiente

| Tema | Estado |
|---|---|
| Consultar a ODEPA por licencia de uso comercial | Abierto — antes de la primera venta |
| Constituir sociedad para separar el patrimonio del equipo | Abierto — decisión del equipo |
| Publicar el aviso también en la landing | Abierto |
| Campo `alert_consent` en `user_prefs` | Abierto — bloquea el envío de alertas proactivas |
