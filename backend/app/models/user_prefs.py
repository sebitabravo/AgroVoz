"""Modelo SQLAlchemy para preferencias de una identidad de WhatsApp.

Una fila por ``phone_hash`` puede representar a un productor individual o a un
grupo PRODESAL. El hash sigue siendo la única identidad: las etiquetas grupales
son códigos operativos no sensibles y nunca se guardan teléfonos en texto plano
ni datos de integrantes.
"""

import datetime

from sqlalchemy import Boolean, CheckConstraint, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserPrefs(Base):
    """Preferencias de un contacto identificado únicamente por ``phone_hash``.

    ``identity_type`` distingue una identidad individual de un contacto
    compartido por un grupo PRODESAL. ``group_label`` es un código operativo:
    no debe contener nombres, teléfonos ni otros datos personales.

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

    El flag ``alert_consent`` es independiente de ``dataset_consent`` y controla
    el envío de alertas proactivas de precio y clima. Va aparte porque es un
    tratamiento distinto: una alerta es una comunicación que AgroVoz inicia sin
    que el productor pregunte, y por lo tanto necesita su propia base de licitud.
    Default ``False``: sin consentimiento registrado no se envía ninguna alerta.
    Se recoge en la sección 7.3 del Acuerdo de Uso (docs/piloto/06).

    ``history_consent`` controla exclusivamente la retención del historial de
    consultas. Es independiente de los consentimientos de dataset y alertas:
    autorizar uno de esos tratamientos nunca habilita los otros. Su valor por
    defecto es ``False`` para preservar privacidad ante toda fila nueva o legacy.

    ``expense_consent`` controla datos económicos declarados para #170. También
    es independiente: habilitar historial, dataset o alertas nunca autoriza
    guardar montos y conceptos de gastos.

    ``parcela_consent`` controla el registro de parcelas (cultivo, superficie,
    comuna) para el motor de reglas agronómicas y el clima por parcela (C5).
    Igual de independiente: ningún otro consentimiento habilita este.
    """

    __tablename__ = "user_prefs"
    __table_args__ = (
        CheckConstraint(
            "identity_type IN ('individual', 'prodesal_group')",
            name="ck_user_prefs_identity_type",
        ),
        CheckConstraint(
            """
            (identity_type = 'individual' AND group_label IS NULL)
            OR
            (
                identity_type = 'prodesal_group'
                AND group_label IS NOT NULL
                AND length(group_label) BETWEEN 1 AND 100
                AND group_label = trim(
                    group_label,
                    char(9) || char(10) || char(11) || char(12) || char(13) || ' '
                )
                AND length(group_label) >= 1
            )
            """,
            name="ck_user_prefs_group_identity",
        ),
        CheckConstraint(
            """
            localidad IS NULL
            OR (
                length(localidad) BETWEEN 1 AND 120
                AND localidad = trim(
                    localidad,
                    char(9) || char(10) || char(11) || char(12) || char(13) || ' '
                )
                AND length(localidad) >= 1
            )
            """,
            name="ck_user_prefs_localidad_length",
        ),
        CheckConstraint(
            "history_consent IN (0, 1)",
            name="ck_user_prefs_history_consent_bool",
        ),
        CheckConstraint(
            "expense_consent IN (0, 1)",
            name="ck_user_prefs_expense_consent_bool",
        ),
        CheckConstraint(
            "parcela_consent IN (0, 1)",
            name="ck_user_prefs_parcela_consent_bool",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # HMAC-SHA256 hex digest (64 caracteres). Unique: una fila por productor.
    # Sin unique, dos upserts simultaneos crearian duplicados.
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # Tipo de identidad asociada al contacto de WhatsApp.
    identity_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="individual",
        server_default="individual",
    )
    # Código operativo no sensible del grupo. Solo aplica a prodesal_group.
    group_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Sector o localidad opcional, sin dirección exacta ni datos personales.
    localidad: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Comuna del productor (ej: "Traiguén"). Nullable: el onboarding por voz
    # es stretch; en el piloto se setea via admin despues del primer contacto.
    comuna: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Consentimiento explicito para retener audio en el dataset de voz rural.
    # Default False: privacidad por defecto (#96).
    dataset_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Consentimiento explicito para recibir alertas proactivas de precio y clima.
    # Separado de dataset_consent: es otro tratamiento (comunicacion no solicitada).
    # Default False: sin opt-in registrado no sale ninguna alerta.
    alert_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Consentimiento separado para retener historial de consultas (#195, #201).
    # Tiene default de Python y de DB para cubrir ORM, SQL directo y filas legacy.
    history_consent: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )
    # Consentimiento separado para registrar gastos económicos (#170).
    expense_consent: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )
    # Consentimiento separado para registrar parcelas (cultivo, superficie, comuna) — C5.
    parcela_consent: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )
    # Cultivos de interés del productor, almacenados como JSON en TEXT.
    # Nullable: se capturan durante el onboarding o via admin (issue #125).
    # Ejemplo: '["papa", "trigo", "tomate"]'
    cultivos: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())

    def __repr__(self) -> str:
        """Representa solo consentimientos, sin exponer ninguna identidad."""
        dataset_consent = self.dataset_consent if self.dataset_consent is not None else False
        alert_consent = self.alert_consent if self.alert_consent is not None else False
        history_consent = self.history_consent if self.history_consent is not None else False
        expense_consent = self.expense_consent if self.expense_consent is not None else False
        parcela_consent = self.parcela_consent if self.parcela_consent is not None else False
        return (
            f"<UserPrefs(dataset_consent={dataset_consent}, "
            f"alert_consent={alert_consent}, history_consent={history_consent}, "
            f"expense_consent={expense_consent}, parcela_consent={parcela_consent})>"
        )
