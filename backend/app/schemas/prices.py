"""Schemas Pydantic para el endpoint de precios ODEPA.

Issue #16: Tool consulta precios ODEPA.
"""

from pydantic import BaseModel, Field


class PriceResponse(BaseModel):
    """Respuesta de precio para un producto+mercado específico.

    Incluye el precio numérico en la unidad de venta ODEPA (precio_kg),
    el precio calculado por kilo (precio_por_kilo) si es convertible,
    y una versión en texto natural lista para TTS.

    precio_kg: precio crudo en la unidad de venta ODEPA (puede no ser kilo).
    precio_por_kilo: precio calculado por kilo real, o None si no es convertible
                     (ej: docena de atados no se puede convertir a kilo).
    texto: versión hablada del precio, ya contiene la conversión si aplica.
    """

    producto: str
    mercado: str
    precio_kg: float
    unidad: str
    precio_por_kilo: float | None = Field(
        None,
        description="Precio calculado por kilo. None si la unidad no es convertible (ej: docena de atados)."
    )
    fecha: str = Field(description="Fecha del precio en formato YYYY-MM-DD")
    texto: str = Field(description="Texto natural en español chileno para TTS")

    model_config = {"from_attributes": True}


class PriceListResponse(BaseModel):
    """Lista de precios para un producto en todos los mercados disponibles.

    Se usa cuando el endpoint se llama sin el query param ?mercado=.
    """

    producto: str
    total_mercados: int
    precios: list[PriceResponse]
