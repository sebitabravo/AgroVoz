"""Formato de cifras para respuestas habladas.

Fuente unica de verdad: antes habia dos implementaciones de ``_formatear_pesos``
—una en ``odepa_service`` y otra en ``alert_service``— y ninguna daba el
resultado correcto:

- La de ODEPA verbalizaba los decimales de la fuente: "14.232 coma 14 pesos".
- La de alertas truncaba con ``int()``: 14.999,9 quedaba en "14.999" en vez de
  "15.000".

El peso chileno no tiene centavos en circulacion. ODEPA publica precios con 4-6
decimales porque son promedios calculados (verificado en la base: 7132.3529,
9760.8695), no porque exista esa moneda. Decir "coma 14 pesos" por voz no
significa nada para el productor y suena a error del sistema.
"""

from decimal import ROUND_HALF_UP, Decimal


def formatear_pesos(valor: Decimal | float | int) -> str:
    """Formatea una cifra como pesos chilenos hablados.

    Redondea al peso entero (ROUND_HALF_UP) y usa punto como separador de
    miles, que es la convencion chilena. Dice "pesos" y no "$" para que el LLM
    no lo lea como dolares al reformular la respuesta antes del TTS.

    Args:
        valor: Monto en pesos. Acepta Decimal, float o int.

    Returns:
        Texto listo para voz. Ej: ``Decimal("14232.14")`` -> ``"14.232 pesos"``.

    Examples:
        >>> formatear_pesos(Decimal("1200"))
        '1.200 pesos'
        >>> formatear_pesos(Decimal("14232.14"))
        '14.232 pesos'
        >>> formatear_pesos(Decimal("14999.9"))
        '15.000 pesos'
    """
    if not isinstance(valor, Decimal):
        valor = Decimal(str(valor))
    entero = valor.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{int(entero):,}".replace(",", ".") + " pesos"
