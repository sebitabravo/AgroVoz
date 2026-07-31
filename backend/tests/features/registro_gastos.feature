# language: es
Característica: Registro de gastos por voz (#170)
  Para saber si una venta dejó margen real
  Como agricultor con consentimiento otorgado
  Quiero registrar un gasto por voz y que se descuente del margen

  Antecedentes:
    Dado que el registro de gastos está habilitado
    Y el productor "traiguen-01" tiene comuna registrada

  Escenario: El productor sin consentimiento no ve su gasto guardado
    Dado que "traiguen-01" no ha dado consentimiento de gastos
    Cuando "traiguen-01" pide registrar un gasto de "50000" en "semilla" para "papa"
    Entonces el sistema responde que no guardó el gasto
    Y no queda ningún gasto persistido para "traiguen-01"

  Escenario: El productor con consentimiento registra un gasto válido
    Dado que "traiguen-01" dio consentimiento de gastos
    Cuando "traiguen-01" pide registrar un gasto de "50000" en "semilla" para "papa"
    Entonces el gasto queda persistido con monto "50000" para "papa"
    Y el gasto vence según los días de retención configurados

  Escenario: Revocar el consentimiento borra los gastos ya guardados
    Dado que "traiguen-01" dio consentimiento de gastos
    Y "traiguen-01" registró un gasto de "30000" en "fertilizante" para "trigo"
    Cuando "traiguen-01" revoca el consentimiento de gastos
    Entonces no queda ningún gasto persistido para "traiguen-01"

  Esquema del escenario: Montos hablados sin normalizar se interpretan igual que el numérico
    Dado que "traiguen-01" dio consentimiento de gastos
    Cuando "traiguen-01" pide registrar un gasto de "<monto_hablado>" en "flete" para "avena"
    Entonces el gasto queda persistido con monto "<monto_esperado>" para "avena"

    Ejemplos:
      | monto_hablado          | monto_esperado |
      | treinta mil            | 30000          |
      | veinte lucas           | 20000          |
      | veinte mil quinientos  | 20500          |
