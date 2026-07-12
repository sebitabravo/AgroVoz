"""Modelo SQLAlchemy para preferencias de usuario (onboarding por voz, #86).

Almacena la comuna del productor asociada al phone_hash. La comuna se
registra presencialmente via admin durante el piloto de Traiguén
(el equipo visita al productor, guarda su número hasheado y su comuna).

Una fila por phone_hash (unique): un productor = un conjunto de prefs.

El número de teléfono NUNCA se almacena, solo su hash HMAC-SHA256.
Ver ``app_core/phone_hash.py`` para la función de hashing.
"""

import datetime

from sqlalchemy import Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserPrefs(Base):
    """Preferencias de un productor identificadas por phone_hash.

    La comuna permite resolver el mercado más cercano (#89) y
    personalizar respuestas. En el piloto se registra via admin
    (onboarding presencial); por voz es stretch goal post-MVP.
    """

    __tablename__ = "user_prefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # HMAC-SHA256 hex digest (64 caracteres). Unique: una fila por productor.
    # Sin unique, dos upserts simultaneos crearian duplicados.
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # Comuna del productor (ej: "Traiguén"). Nullable: el onboarding por voz
    # es stretch; en el piloto se setea via admin despues del primer contacto.
    comuna: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())

    def __repr__(self) -> str:
        comuna_str = self.comuna or "sin_comuna"
        return f"<UserPrefs(phone_hash='{self.phone_hash[:8]}...', comuna='{comuna_str}')>"
