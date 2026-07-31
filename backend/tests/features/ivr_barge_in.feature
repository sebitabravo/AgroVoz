# language: es
Característica: Interrupción por voz durante la locución del IVR (C10)
  Para no hacer esperar al productor a que termine de escuchar todo
  Como agricultor que llama al canal IVR de respaldo
  Quiero poder hablar encima de la locución y que se corte para escucharme

  Escenario: El productor interrumpe la locución hablando encima
    Dado que el IVR está reproduciendo la locución del precio de la papa
    Cuando el productor empieza a hablar
    Entonces el IVR corta la locución y empieza a grabar

  Escenario: El productor deja de hablar y el IVR procesa lo grabado
    Dado que el IVR está grabando lo que dice el productor
    Cuando el productor deja de hablar
    Entonces el IVR deja de grabar y procesa la transcripción

  Escenario: La respuesta generada se reproduce como una nueva locución
    Dado que el IVR está procesando la transcripción del productor
    Cuando la respuesta sobre el precio del trigo está lista
    Entonces el IVR reproduce la nueva locución

  Escenario: Hablar mientras el IVR ya está escuchando no repite la interrupción
    Dado que el IVR está grabando lo que dice el productor
    Cuando el productor sigue hablando
    Entonces el IVR no repite ninguna acción y sigue grabando

  Escenario: La locución termina sin interrupción y el IVR pasa a escuchar
    Dado que el IVR está reproduciendo la locución del precio de la papa
    Cuando la locución termina sin que el productor la interrumpa
    Entonces el IVR empieza a grabar la respuesta del productor
