"""Modelo SQLAlchemy para alertas proactivas de precio y clima.

Issue #88: alertas configurables por voz que convierten a AgroVoz en
vigia de mercado y riesgo agroclimatico. Cada alerta se identifica por
phone_hash (HMAC-SHA256) y guarda el chat_id de WhatsApp necesario para
enviar avisos proactivos.

El numero de telefono NUNCA se almacena en texto plano salvo el chat_id
minimo requerido para el envio proactivo por Open-WA. El chat_id se
obtiene del mensaje entrante en el momento de crear la alerta y se usa
solo para responder a ese mismo chat.
"""

import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Alert(Base):
    """Alerta proactiva de precio o clima asociada a un productor."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # HMAC-SHA256 hex digest (64 caracteres) del numero de telefono.
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Chat ID de WhatsApp para enviar el aviso proactivo (ej: "56912345678@c.us").
    # Se requiere porque el envio proactivo no puede derivarse del hash.
    # Nullable: una alerta creada por admin sin chat_id no se enviara.
    wa_chat_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Tipo de alerta. Precio/clima son públicas; variacion_precio es un cursor
    # interno inactivo para destino e idempotencia del cron ODEPA.
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    # Producto a monitorear (solo alertas de precio).
    producto: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Condicion de comparacion para precio. Valores en app.core.constants.CondicionPrecio.
    condicion: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # Umbral de precio por kilogramo (pesos chilenos), para alertas de precio.
    umbral: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Umbral climático fijo. En el registro interno tipo="variacion_precio"
    # (activa=False) conserva el digest opaco del último conjunto entregado;
    # nunca guarda el texto ni los precios del mensaje.
    umbral_clima: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Estado de la alerta.
    activa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Timestamp del ultimo disparo. Controla el limite de 1 alerta/dia/numero.
    last_triggered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())

    def __repr__(self) -> str:
        """Representa estado operativo sin exponer sujeto ni preferencias."""
        return f"<Alert(id={self.id}, tipo='{self.tipo}', activa={self.activa})>"
