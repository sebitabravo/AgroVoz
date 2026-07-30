"""Modelo SQLAlchemy para consultas de agricultores.

Registra cada interacción del pipeline de voz para métricas anonimizadas.
El número de teléfono se anonimiza con HMAC-SHA256 + pepper key.
Ver ``app/core/phone_hash.py`` para la función de hashing.

``query_text`` y ``response_text`` son staging transitorio: quedan vacíos por
defecto y solo reciben contenido cuando el historial está habilitado y existe
opt-in específico. Tras la entrega, el contenido se copia al historial
consentido y se redacta; una entrega fallida se redacta en la misma
transacción. Un job horario elimina cualquier staging con más de 24 horas.
"""

import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Consultation(Base):
    """Registro anonimizado de una consulta procesada por el pipeline."""

    __tablename__ = "consultations"
    __table_args__ = (
        CheckConstraint(
            "delivery_status IN ('pending', 'delivered', 'failed')",
            name="ck_consultations_delivery_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # HMAC-SHA256 hex digest (64 caracteres en minúscula).
    # Hasheado con settings.phone_hash_pepper vía app.core.phone_hash.
    # Sin la pepper key, el hash no es reversible ni vulnerable a
    # rainbow tables de números chilenos (~10^8 combinaciones).
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Clasificacion de la consulta. Valores definidos en app.core.constants.Intent.
    # String(50) permite intents compuestos post-MVP sin migracion.
    intent: Mapped[str] = mapped_column(String(50), nullable=False, default="desconocido")
    # Producto detectado en la consulta (ej: "papa", "tomate"). Nullable
    # porque no toda consulta tiene producto (clima, resumen, desconocido).
    # Se usa para estadisticas del agricultor (comando "resumen").
    producto: Mapped[str | None] = mapped_column(String(100), nullable=True, default=None)
    # Staging transitorio opcional. Puede contener PII incidental; nunca se
    # llena sin opt-in de historial y se redacta después de la entrega.
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    audio_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Estado real del envío al agricultor. ``pending`` también representa
    # registros históricos, cuya entrega no puede comprobarse retroactivamente.
    delivery_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    delivered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Código estable y no sensible para agrupar fallos del gateway.
    delivery_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
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
    revisado_por: Mapped[str | None] = mapped_column(String(100), nullable=True)
    nota_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    resuelto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    # Feedback del agricultor. Valores definidos en app.core.constants.FeedbackAgricultor.
    # Se detecta como intent especial en el pipeline: el agricultor responde
    # "me sirvio" / "no me sirvio" y se asocia a la consulta ANTERIOR.
    feedback: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Marcado manual por el equipo como "caso de decisión productiva".
    # Criterio de éxito del piloto (sección 7.3 del paper).
    decision_productiva: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Marca filas generadas por pytest/smoke tests (no productores reales).
    # Se excluyen de las métricas del dashboard admin para no sesgar
    # success_rate ni percentiles de latencia con datos sintéticos.
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0", index=True)

    def __repr__(self) -> str:
        """Representa métricas operativas sin exponer sujeto ni contenido."""
        return (
            f"<Consultation(id={self.id}, intent='{self.intent}', "
            f"latency_ms={self.latency_ms}, delivery_status='{self.delivery_status}')>"
        )
