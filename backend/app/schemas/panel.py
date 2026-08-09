"""Schemas públicos del panel PWA del agricultor."""

from pydantic import BaseModel, Field


class PanelPricePointResponse(BaseModel):
    """Precio ODEPA sin interpretación, asociado a una fecha."""

    fecha: str = Field(description="Fecha del precio en formato YYYY-MM-DD")
    precio: float = Field(description="Precio crudo en la unidad ODEPA")


class PanelPriceSeriesResponse(BaseModel):
    """Serie de un cultivo en un mercado y unidad de venta constantes."""

    cultivo: str
    mercado: str
    unidad: str
    fuente: str = "ODEPA"
    precios: list[PanelPricePointResponse]


class PanelPricesResponse(BaseModel):
    """Historial de 28 días para los cultivos registrados del productor."""

    desde: str = Field(description="Inicio inclusivo de la ventana YYYY-MM-DD")
    hasta: str = Field(description="Fin inclusivo de la ventana YYYY-MM-DD")
    dias: int = Field(default=28, description="Cantidad de días incluidos")
    cultivos: list[PanelPriceSeriesResponse]
