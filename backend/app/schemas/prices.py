"""Schemas Pydantic para el endpoint de precios ODEPA.

Issue #16: Tool consulta precios ODEPA.
"""

from pydantic import BaseModel, Field


class PriceResponse(BaseModel):
    """Respuesta de precio para un producto+mercado específico.

    Incluye el precio numérico y una versión en texto natural
    lista para ser leída por Piper TTS en la respuesta de voz.
    """

    producto: str
    mercado: str
    precio_kg: float
    unidad: str
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
