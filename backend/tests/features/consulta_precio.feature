# language: es
Característica: Consulta de precio ODEPA (golden path)
  Para negociar sin perder margen frente al intermediario
  Como agricultor
  Quiero preguntar el precio de referencia de mi producto

  Escenario: Precio de un producto con mercado explícito
    Dado que ODEPA reportó "papa" en "Lo Valledor" a "500" pesos el kilo el "2026-07-29"
    Cuando el agricultor pregunta el precio de "papa" en "Lo Valledor"
    Entonces la respuesta menciona el precio "500"

  Escenario: Sin mercado especificado, usa Lo Valledor como referencia nacional
    Dado que ODEPA reportó "papa" en "Lo Valledor" a "500" pesos el kilo el "2026-07-29"
    Cuando el agricultor pregunta el precio de "papa" sin nombrar mercado
    Entonces la respuesta menciona el precio "500"

  Escenario: Producto sin datos de precio no rompe la conversación
    Cuando el agricultor pregunta el precio de "quinoa" sin nombrar mercado
    Entonces la respuesta explica que no hay datos, sin lanzar error
