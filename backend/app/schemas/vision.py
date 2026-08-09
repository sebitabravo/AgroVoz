"""DTOs de la identificación visual para el panel PWA."""

from pydantic import BaseModel, ConfigDict, Field


class VisionIdentifyResponse(BaseModel):
    """Resultado preliminar de una imagen, con cita INIA cuando existe."""

    enfermedad: str = Field(description="Clase visual entregada por el modelo local.")
    cultivo: str = Field(default="", description="Cultivo asociado a la etiqueta del modelo.")
    confianza: float = Field(ge=0.0, le=1.0, description="Confianza del modelo entre 0 y 1.")
    identificada: bool = Field(description="Indica si superó el umbral configurado.")
    fuente_inia: str | None = Field(default=None, description="Regla INIA vigente, si existe.")
    fuente_url: str | None = Field(default=None, description="URL citada por la regla INIA.")
    fecha_fuente: str | None = Field(default=None, description="Fecha de verificación de la fuente.")
    imagen_anotada: str = Field(description="Imagen JPEG anotada como data URL.")
    mensaje: str = Field(description="Respuesta honesta para mostrar al productor.")

    model_config = ConfigDict(extra="forbid")
