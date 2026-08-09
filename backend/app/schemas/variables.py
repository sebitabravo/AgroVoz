"""Schema Pydantic para variables extraídas de la consulta del agricultor.

Define el contrato tipado de qué variables se extraen de cada consulta
antes de invocar las tools del LLM. Sirve como gate de validación temprana:
si la extracción falla o el tipo de consulta es ambiguo, se pide aclaración
sin gastar recursos del LLM en tools que no aplican.

Issue: #191 — Discussion #135 (adaptado de Nexor AI).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Literal de tipos de consulta que AgroVoz puede responder.
TipoConsulta = Literal["precio", "clima", "agronomica", "ambos", "desconocido"]


class ExtractedVariables(BaseModel):
    """Variables extraídas de la consulta del agricultor.

    Todos los campos son opcionales excepto consulta_tipo y ubicacion.
    Si un campo no pudo extraerse, queda como None; el pipeline
    decide si pide aclaración o usa defaults.
    """

    producto: str | None = Field(
        default=None,
        description="Producto agrícola mencionado (ej: 'papa', 'trigo').",
    )
    mercado: str | None = Field(
        default=None,
        description="Mercado o feria mencionada (ej: 'Santiago', 'Lo Valledor').",
    )
    ubicacion: str = Field(
        default="Traiguén",
        description="Ubicación para consulta de clima "
        "(default: Traiguén, coordenadas fijas del piloto).",
    )
    consulta_tipo: TipoConsulta = Field(
        default="desconocido",
        description="Tipo de consulta: precio, clima, agronómica, ambos, o desconocido.",
    )
    urgencia: str | None = Field(
        default=None,
        description="Urgencia expresada por el agricultor: 'hoy', 'mañana', o None.",
    )
