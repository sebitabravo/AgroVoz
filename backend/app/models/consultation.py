"""Modelo SQLAlchemy para consultas de agricultores.

Registra cada interacción del pipeline de voz para métricas anonimizadas.
El número de teléfono se anonimiza con HMAC-SHA256 + pepper key.
Ver ``app/core/phone_hash.py`` para la función de hashing.

query_text almacena la transcripción literal del audio. Puede contener
PII incidental (nombre del agricultor, referencias a ubicación, etc.).
Para el piloto MVP (3-5 agricultores con consentimiento informado), se
retiene para depuración del pipeline de voz. El cron de limpieza de audio
(audio_retention_hours) debe borrar también query_text asociado.
Pre-producción: encriptar query_text en reposo (AES-256-GCM).
"""

import datetime

from sqlalchemy import Boolean, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Consultation(Base):
    """Registro anonimizado de una consulta procesada por el pipeline."""

    __tablename__ = "consultations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # HMAC-SHA256 hex digest (64 caracteres en minúscula).
    # Hasheado con settings.phone_hash_pepper vía app.core.phone_hash.
    # Sin la pepper key, el hash no es reversible ni vulnerable a
    # rainbow tables de números chilenos (~10^8 combinaciones).
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Clasificación de la consulta: "precio", "clima", "desconocido".
    # String(50) permite intents compuestos post-MVP sin migración
    # (ej: "precio_historico", "clima_semanal").
    intent: Mapped[str] = mapped_column(String(50), nullable=False, default="desconocido")
    # Producto detectado en la consulta (ej: "papa", "tomate"). Nullable
    # porque no toda consulta tiene producto (clima, resumen, desconocido).
    # Se usa para estadisticas del agricultor (comando "resumen").
    producto: Mapped[str | None] = mapped_column(String(100), nullable=True, default=None)
    # Transcripción literal del audio. ATENCIÓN: puede contener PII
    # incidental (nombre, ubicación). Ver docstring del módulo para
    # política de retención y plan de encriptación pre-producción.
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    audio_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Desglose de latencia por etapa del pipeline (ms). Se persisten para
    # metricas historicas (dashboard admin). default=0 para no romper
    # registros creados antes de esta migracion ni consultas donde la
    # etapa fallo (ej: tts_ms=0 si TTS no corrio).
    whisper_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tts_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # ── Cola de revisión humana (issue #99) ─────────────────────────
    # Se marca automáticamente cuando el pipeline produce fallback,
    # intent desconocido, o alguna etapa falló.
    requires_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0", index=True
    )
    revisado_por: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    nota_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    resuelto: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    # Feedback del agricultor tras la respuesta. Nullable para no romper
    # registros existentes. Valores: "util", "no_util", None (sin feedback).
    # Se detecta como intent especial en el pipeline: el agricultor responde
    # "me sirvió" / "no me sirvió" y se asocia a la consulta ANTERIOR.
    feedback: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Marcado manual por el equipo como "caso de decisión productiva".
    # Criterio de éxito del piloto (sección 7.3 del paper).
    decision_productiva: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # Marca filas generadas por pytest/smoke tests (no productores reales).
    # Se excluyen de las métricas del dashboard admin para no sesgar
    # success_rate ni percentiles de latencia con datos sintéticos.
    is_test: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0", index=True
    )

    def __repr__(self) -> str:
        return (
            f"<Consultation(phone_hash='{self.phone_hash[:8]}...', "
            f"intent='{self.intent}', latency_ms={self.latency_ms}, "
            f"feedback={self.feedback})>"
        )
