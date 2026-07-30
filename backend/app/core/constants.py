"""Constantes tipadas del dominio AgroVoz con Literal types.

Define los valores validos del dominio como type aliases (Literal[str]).
Esto permite que mypy strict valide las firmas y comparaciones en tiempo
de chequeo estatico, sin cambiar el runtime (siguen siendo strings).

Uso:
    from app.core.constants import Intent, TipoAlerta, CondicionPrecio

    def procesar(intent: Intent) -> str:
        if intent == "precio":  # mypy valida que "percio" es un typo
            ...

Los modelos SQLAlchemy mantienen Mapped[str] porque los valores vienen de
SQLite como strings planos. El type alias se usa en la capa de servicios.
"""

from decimal import Decimal
from typing import Literal

# ── Intenciones de consulta ─────────────────────────────────────────
# Clasificacion del pipeline de voz. Se persiste en consultations.intent.

type Intent = Literal[
    "precio",
    "clima",
    "credito",
    "corpus",
    "desconocido",
    "resumen",
    "saludo",
    "feedback",
    "alerta",
]
"""Intencion de la consulta del agricultor. Usada en pipeline y metricas."""


# ── Tipos de alerta ─────────────────────────────────────────────────
# Se persisten en alerts.tipo. Cada alerta es de precio o clima.

type TipoAlerta = Literal["precio", "clima"]
"""Tipo de alerta proactiva. 'precio' monitorea ODEPA, 'clima' monitorea OpenMeteo."""


# ── Condiciones de comparacion para alertas de precio ───────────────
# Operadores que el agricultor puede configurar en su alerta de precio.

type CondicionPrecio = Literal[">", "<", ">=", "<="]
"""Condicion de comparacion para el umbral de precio por kilogramo."""

# Conjunto precomputado para validacion O(1) en runtime (mismo uso que el set
# CONDICIONES_VALIDAS anterior).
CONDICIONES_VALIDAS: frozenset[str] = frozenset({">", "<", ">=", "<="})


# ── Umbrales climaticos ─────────────────────────────────────────────
# Se persisten en alerts.umbral_clima. Umbrales fijos del MVP.

type UmbralClima = Literal["helada", "lluvia_extrema"]
"""Umbral climatico fijo: helada (minima < 2 C) o lluvia extrema (> 50 mm/24h)."""

# Conjunto precomputado para validacion O(1) en runtime.
UMBRALES_CLIMA_VALIDOS: frozenset[str] = frozenset({"helada", "lluvia_extrema"})

# Valores numericos de los umbrales climaticos (Decimal para precision financiera).
HELADA_UMBRAL_C: Decimal = Decimal("2.0")  # Grados Celsius
LLUVIA_EXTREMA_UMBRAL_MM: Decimal = Decimal("50.0")  # Milimetros en 24 horas


# ── Feedback del agricultor ─────────────────────────────────────────
# Se persiste en consultations.feedback. El agricultor evalua la respuesta.

type FeedbackAgricultor = Literal["util", "no_util"]
"""Feedback del agricultor: 'util' (positivo) o 'no_util' (negativo)."""
