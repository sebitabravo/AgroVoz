"""Modelo SQLAlchemy para preferencias de usuario (onboarding por voz, #86).

Almacena la comuna del productor asociada al phone_hash. La comuna se
registra presencialmente via admin durante el piloto de Traiguén
(el equipo visita al productor, guarda su número hasheado y su comuna).

Una fila por phone_hash (unique): un productor = un conjunto de prefs.

El número de teléfono NUNCA se almacena, solo su hash HMAC-SHA256.
Ver ``app_core/phone_hash.py`` para la función de hashing.
"""

import datetime

from sqlalchemy import Boolean, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserPrefs(Base):
    """Preferencias de un productor identificadas por phone_hash.

    La comuna permite resolver el mercado más cercano (#89) y
    personalizar respuestas. En el piloto se registra via admin
    (onboarding presencial); por voz es stretch goal post-MVP.

    El flag ``dataset_consent`` controla la retención selectiva de audio
    para construir el dataset de voz rural chilena (issue #96). Es opt-in
    explicito: default ``False``; solo se retiene audio cuando el productor
    firmó el Acuerdo de Uso y Consentimiento.

    ``cultivos`` almacena los cultivos de interés del productor como JSON
    (lista de strings, ej: ``["papa", "trigo", "tomate"]``). Se usa para
    personalizar el contexto del LLM cuando el agricultor no especifica
    producto en la consulta (issue #125).
    """

    __tablename__ = "user_prefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # HMAC-SHA256 hex digest (64 caracteres). Unique: una fila por productor.
    # Sin unique, dos upserts simultaneos crearian duplicados.
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # Comuna del productor (ej: "Traiguén"). Nullable: el onboarding por voz
    # es stretch; en el piloto se setea via admin despues del primer contacto.
    comuna: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Consentimiento explicito para retener audio en el dataset de voz rural.
    # Default False: privacidad por defecto (#96).
    dataset_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Cultivos de interés del productor, almacenados como JSON en TEXT.
    # Nullable: se capturan durante el onboarding o via admin (issue #125).
    # Ejemplo: '["papa", "trigo", "tomate"]'
    cultivos: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())

    def __repr__(self) -> str:
        comuna_str = self.comuna or "sin_comuna"
        # En instancias transient (sin flush) el valor puede ser None;
        # mostramos False para no leakear que el campo esta sin setear.
        consent = self.dataset_consent if self.dataset_consent is not None else False
        cultivos_str = self.cultivos or "sin_cultivos"
        return (
            f"<UserPrefs(phone_hash='{self.phone_hash[:8]}...', "
            f"comuna='{comuna_str}', dataset_consent={consent}, "
            f"cultivos='{cultivos_str}')>"
        )
