"""State machine de conversación multi-turno (issue #192).

Feature-gated: solo se activa si AGROVOZ_CONVERSATION_STATE=true.
Por defecto desactivado — el pipeline opera en modo stateless (actual).

Post-MVP: solo tiene sentido cuando se habilite conversación multi-turno
con historial (que requiere consentimiento Ley 21.719).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ConversationState(Enum):
    """Estados posibles de una conversación."""

    ESPERANDO_CONSULTA = "esperando_consulta"
    CONSULTA_RECIBIDA = "consulta_recibida"
    BUSCANDO_DATOS = "buscando_datos"
    ACLARANDO = "aclarando"
    RESPONDIENDO = "respondiendo"
    CERRADO = "cerrado"
    DERIVADO = "derivado"


# Transiciones válidas entre estados.
_VALID_TRANSITIONS: dict[ConversationState, frozenset[ConversationState]] = {
    ConversationState.ESPERANDO_CONSULTA: frozenset({
        ConversationState.CONSULTA_RECIBIDA,
    }),
    ConversationState.CONSULTA_RECIBIDA: frozenset({
        ConversationState.BUSCANDO_DATOS,
        ConversationState.ACLARANDO,
    }),
    ConversationState.BUSCANDO_DATOS: frozenset({
        ConversationState.RESPONDIENDO,
        ConversationState.ACLARANDO,
    }),
    ConversationState.ACLARANDO: frozenset({
        ConversationState.ESPERANDO_CONSULTA,
        ConversationState.CERRADO,
    }),
    ConversationState.RESPONDIENDO: frozenset({
        ConversationState.ESPERANDO_CONSULTA,
        ConversationState.CERRADO,
    }),
    ConversationState.CERRADO: frozenset(),
    ConversationState.DERIVADO: frozenset(),
}

# Estados terminales (no permiten más transiciones).
_TERMINAL_STATES: frozenset[ConversationState] = frozenset({
    ConversationState.CERRADO,
    ConversationState.DERIVADO,
})


@dataclass(frozen=True)
class Conversation:
    """Conversación con estado y timeout.

    Inmutable: cada transición retorna una NUEVA instancia.
    """

    state: ConversationState = ConversationState.ESPERANDO_CONSULTA
    last_activity: float = field(default_factory=time.monotonic)
    turn_count: int = 0

    def can_transition_to(self, target: ConversationState) -> bool:
        """Verifica si la transición al estado objetivo es válida."""
        if self.state in _TERMINAL_STATES:
            return False
        return target in _VALID_TRANSITIONS.get(
            self.state, frozenset()
        )

    def transition_to(self, target: ConversationState) -> Conversation:
        """Transiciona a un nuevo estado. Retorna nueva instancia.

        Si la transición es inválida, loguea warning y retorna self.
        """
        if not self.can_transition_to(target):
            logger.warning(
                "Transición inválida: %s → %s (turn_count=%d)",
                self.state.value,
                target.value,
                self.turn_count,
            )
            return self
        return Conversation(
            state=target,
            last_activity=time.monotonic(),
            turn_count=self.turn_count + (1 if target == ConversationState.CONSULTA_RECIBIDA else 0),
        )

    def timeout_reached(self, timeout_minutes: int = 30) -> bool:
        """Verifica si la conversación excedió el timeout de inactividad.

        Args:
            timeout_minutes: Minutos de inactividad permitidos.

        Returns:
            True si la conversación debe cerrarse por inactividad.
        """
        elapsed = time.monotonic() - self.last_activity
        return elapsed > (timeout_minutes * 60)

    @property
    def is_active(self) -> bool:
        """True si la conversación está en un estado no terminal."""
        return self.state not in _TERMINAL_STATES
