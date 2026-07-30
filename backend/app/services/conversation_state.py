"""Dominio y registro efímero de conversación multi-turno (issue #192).

La activación permanece protegida por ``USE_CONVERSATION_STATE=false``.
Este módulo no persiste datos ni modifica el pipeline. El registro conserva
solo ``phone_hash`` y objetos :class:`Conversation` en memoria; nunca recibe
consultas, respuestas, teléfonos planos ni timestamps de reloj civil.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import Enum
from threading import Lock

from app.core.phone_hash import validate_phone_hash

logger = logging.getLogger(__name__)

MonotonicClock = Callable[[], float]
_MAX_COMPREHENSION_FAILURES = 3


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
    ConversationState.ESPERANDO_CONSULTA: frozenset(
        {
            ConversationState.CONSULTA_RECIBIDA,
        }
    ),
    ConversationState.CONSULTA_RECIBIDA: frozenset(
        {
            ConversationState.BUSCANDO_DATOS,
            ConversationState.ACLARANDO,
            ConversationState.DERIVADO,
        }
    ),
    ConversationState.BUSCANDO_DATOS: frozenset(
        {
            ConversationState.RESPONDIENDO,
            ConversationState.ACLARANDO,
        }
    ),
    ConversationState.ACLARANDO: frozenset(
        {
            ConversationState.ESPERANDO_CONSULTA,
            ConversationState.CERRADO,
            ConversationState.DERIVADO,
        }
    ),
    ConversationState.RESPONDIENDO: frozenset(
        {
            ConversationState.ESPERANDO_CONSULTA,
            ConversationState.CERRADO,
            ConversationState.DERIVADO,
        }
    ),
    ConversationState.CERRADO: frozenset(),
    ConversationState.DERIVADO: frozenset(),
}

# Estados terminales (no permiten más transiciones).
_TERMINAL_STATES: frozenset[ConversationState] = frozenset(
    {
        ConversationState.CERRADO,
        ConversationState.DERIVADO,
    }
)


def _read_clock(clock: MonotonicClock | None) -> float:
    """Lee el reloj monotónico real o el inyectado por el llamador."""
    return time.monotonic() if clock is None else clock()


def _timeout_seconds(timeout_minutes: int) -> int:
    """Convierte un timeout válido en minutos a segundos."""
    if timeout_minutes < 1:
        raise ValueError("timeout_minutes debe ser mayor o igual a 1")
    return timeout_minutes * 60


@dataclass(frozen=True)
class Conversation:
    """Conversación con estado y timeout.

    Inmutable: cada transición retorna una NUEVA instancia.
    """

    state: ConversationState = ConversationState.ESPERANDO_CONSULTA
    last_activity: float = field(default_factory=time.monotonic)
    turn_count: int = 0
    comprehension_failures: int = 0

    def can_transition_to(self, target: ConversationState) -> bool:
        """Verifica si la transición al estado objetivo es válida."""
        if self.state in _TERMINAL_STATES:
            return False
        return target in _VALID_TRANSITIONS.get(self.state, frozenset())

    def transition_to(
        self,
        target: ConversationState,
        *,
        clock: MonotonicClock | None = None,
    ) -> Conversation:
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
            last_activity=_read_clock(clock),
            turn_count=self.turn_count + (1 if target == ConversationState.CONSULTA_RECIBIDA else 0),
            comprehension_failures=self.comprehension_failures,
        )

    def timeout_reached(
        self,
        timeout_minutes: int = 30,
        *,
        clock: MonotonicClock | None = None,
    ) -> bool:
        """Verifica si la conversación alcanzó el timeout de inactividad.

        Args:
            timeout_minutes: Minutos de inactividad permitidos.
            clock: Reloj monotónico inyectable para ejecución determinista.

        Returns:
            True si la conversación debe cerrarse por inactividad.
        """
        elapsed = _read_clock(clock) - self.last_activity
        return elapsed >= _timeout_seconds(timeout_minutes)

    def close_if_timed_out(
        self,
        timeout_minutes: int = 30,
        *,
        clock: MonotonicClock | None = None,
    ) -> Conversation:
        """Cierra una conversación activa que alcanzó su inactividad máxima.

        Los estados terminales se conservan sin cambios. El cierre por timeout
        es una regla de ciclo de vida y puede ocurrir desde cualquier estado
        activo, independiente de las transiciones funcionales normales.
        """
        if not self.is_active:
            return self

        now = _read_clock(clock)
        if now - self.last_activity < _timeout_seconds(timeout_minutes):
            return self

        return Conversation(
            state=ConversationState.CERRADO,
            last_activity=now,
            turn_count=self.turn_count,
            comprehension_failures=0,
        )

    @property
    def is_active(self) -> bool:
        """True si la conversación está en un estado no terminal."""
        return self.state not in _TERMINAL_STATES


class TransitionStatus(Enum):
    """Resultado explícito de una transición solicitada al registro."""

    APLICADA = "aplicada"
    INVALIDA = "invalida"
    OCUPADA = "ocupada"


@dataclass(frozen=True)
class TransitionResult:
    """Resultado inmutable de una transición registrada."""

    status: TransitionStatus
    conversation: Conversation


@dataclass(frozen=True)
class ConversationClaim:
    """Resultado de intentar reservar un turno para un productor."""

    status: TransitionStatus
    conversation: Conversation
    lease: ConversationLease | None = None


class ConversationRegistry:
    """Registro efímero y thread-safe de conversaciones por ``phone_hash``.

    El registro no acepta contenido conversacional. Cada acceso poda sesiones
    vencidas o terminales para acotar el uso de memoria. Una consulta nueva
    mientras otra sigue activa retorna ``TransitionStatus.OCUPADA``.
    """

    def __init__(
        self,
        timeout_minutes: int,
        *,
        clock: MonotonicClock | None = None,
    ) -> None:
        """Inicializa el registro con timeout y reloj monotónico inyectables."""
        _timeout_seconds(timeout_minutes)
        self._timeout_minutes = timeout_minutes
        self._clock = time.monotonic if clock is None else clock
        self._conversations: dict[str, Conversation] = {}
        self._owners: dict[str, object] = {}
        self._lock = Lock()

    def claim(self, phone_hash: str) -> ConversationClaim:
        """Reserva atómicamente una consulta y retorna una lease propietaria."""
        key = self._validate_key(phone_hash)
        with self._lock:
            now = self._clock()
            self._prune_stale_locked(now)
            current = self._get_or_create_locked(key, now)

            if key in self._owners or current.state != ConversationState.ESPERANDO_CONSULTA:
                logger.warning(
                    "Consulta concurrente rechazada — estado=%s",
                    current.state.value,
                )
                return ConversationClaim(TransitionStatus.OCUPADA, current)

            token = object()
            updated = current.transition_to(
                ConversationState.CONSULTA_RECIBIDA,
                clock=_fixed_clock(now),
            )
            self._conversations[key] = updated
            self._owners[key] = token
            return ConversationClaim(
                TransitionStatus.APLICADA,
                updated,
                ConversationLease(self, key, token),
            )

    def get_or_create(self, phone_hash: str) -> Conversation:
        """Crea o reanuda una conversación vigente para un hash válido."""
        key = self._validate_key(phone_hash)
        with self._lock:
            now = self._clock()
            self._prune_stale_locked(now)
            return self._get_or_create_locked(key, now)

    def transition(
        self,
        phone_hash: str,
        target: ConversationState,
    ) -> TransitionResult:
        """Aplica una transición sin permitir dos consultas simultáneas."""
        key = self._validate_key(phone_hash)
        with self._lock:
            now = self._clock()
            self._prune_stale_locked(now)
            current = self._get_or_create_locked(key, now)

            if key in self._owners:
                logger.warning(
                    "Transición sin lease rechazada — estado=%s",
                    current.state.value,
                )
                return TransitionResult(TransitionStatus.OCUPADA, current)

            if target == ConversationState.CONSULTA_RECIBIDA and current.state != ConversationState.ESPERANDO_CONSULTA:
                logger.warning(
                    "Consulta concurrente rechazada — estado=%s",
                    current.state.value,
                )
                return TransitionResult(TransitionStatus.OCUPADA, current)

            if not current.can_transition_to(target):
                logger.warning(
                    "Transición de registro inválida — %s → %s",
                    current.state.value,
                    target.value,
                )
                return TransitionResult(TransitionStatus.INVALIDA, current)

            updated = current.transition_to(target, clock=_fixed_clock(now))
            self._conversations[key] = updated
            return TransitionResult(TransitionStatus.APLICADA, updated)

    def snapshot(self, phone_hash: str) -> Conversation | None:
        """Retorna solo la conversación inmutable, sin exponer el registro."""
        key = self._validate_key(phone_hash)
        with self._lock:
            now = self._clock()
            self._prune_stale_locked(now)
            return self._conversations.get(key)

    def prune_expired(self) -> int:
        """Cierra y elimina conversaciones vencidas o terminales."""
        with self._lock:
            return self._prune_stale_locked(self._clock())

    def clear(self) -> int:
        """Vacía el registro para tests o apagado y retorna cuántas eliminó."""
        with self._lock:
            removed = len(self._conversations)
            self._conversations.clear()
            self._owners.clear()
            return removed

    @staticmethod
    def _validate_key(phone_hash: str) -> str:
        """Acepta exclusivamente un HMAC-SHA256 hexadecimal minúsculo."""
        if not validate_phone_hash(phone_hash):
            raise ValueError("phone_hash debe ser un HMAC-SHA256 válido")
        return phone_hash

    def _get_or_create_locked(
        self,
        phone_hash: str,
        now: float,
    ) -> Conversation:
        """Obtiene o crea una conversación con el lock ya adquirido."""
        current = self._conversations.get(phone_hash)
        if current is not None:
            return current

        created = Conversation(last_activity=now)
        self._conversations[phone_hash] = created
        return created

    def _prune_stale_locked(self, now: float) -> int:
        """Cierra y elimina estados vencidos o terminales bajo el lock."""
        clock = _fixed_clock(now)
        stale_keys: list[str] = []
        for key, conversation in self._conversations.items():
            if not conversation.is_active:
                stale_keys.append(key)
                continue

            closed = conversation.close_if_timed_out(
                self._timeout_minutes,
                clock=clock,
            )
            if not closed.is_active:
                stale_keys.append(key)

        for key in stale_keys:
            del self._conversations[key]
            self._owners.pop(key, None)

        if stale_keys:
            logger.debug(
                "Conversaciones efímeras podadas — cantidad=%d",
                len(stale_keys),
            )
        return len(stale_keys)

    def _transition_owned(
        self,
        phone_hash: str,
        token: object,
        target: ConversationState,
    ) -> bool:
        """Aplica una transición solo si la lease aún posee la sesión."""
        with self._lock:
            now = self._clock()
            self._prune_stale_locked(now)
            current = self._conversations.get(phone_hash)
            if self._owners.get(phone_hash) is not token or current is None:
                return False
            if not current.can_transition_to(target):
                logger.warning(
                    "Transición de lease inválida — %s → %s",
                    current.state.value,
                    target.value,
                )
                return False

            self._conversations[phone_hash] = current.transition_to(
                target,
                clock=_fixed_clock(now),
            )
            return True

    def _register_comprehension_failure_owned(
        self,
        phone_hash: str,
        token: object,
    ) -> int | None:
        """Incrementa de forma atómica el nivel de aclaración de la lease."""
        with self._lock:
            now = self._clock()
            self._prune_stale_locked(now)
            current = self._conversations.get(phone_hash)
            if self._owners.get(phone_hash) is not token or current is None:
                return None

            failure_count = min(
                current.comprehension_failures + 1,
                _MAX_COMPREHENSION_FAILURES,
            )
            self._conversations[phone_hash] = replace(
                current,
                comprehension_failures=failure_count,
            )
            return failure_count

    def _reset_comprehension_failures_owned(
        self,
        phone_hash: str,
        token: object,
    ) -> bool:
        """Reinicia la escalada solo si la lease aún posee la sesión."""
        with self._lock:
            now = self._clock()
            self._prune_stale_locked(now)
            current = self._conversations.get(phone_hash)
            if self._owners.get(phone_hash) is not token or current is None:
                return False

            if current.comprehension_failures:
                self._conversations[phone_hash] = replace(
                    current,
                    comprehension_failures=0,
                )
            return True

    def _release_owned(
        self,
        phone_hash: str,
        token: object,
        *,
        discard: bool,
    ) -> bool:
        """Libera exclusivamente la sesión que aún pertenece a la lease."""
        with self._lock:
            if self._owners.get(phone_hash) is not token:
                return False

            current = self._conversations.get(phone_hash)
            if not discard and (current is None or current.state != ConversationState.ESPERANDO_CONSULTA):
                return False

            self._owners.pop(phone_hash, None)
            if discard:
                self._conversations.pop(phone_hash, None)
            return True


@dataclass(frozen=True)
class ConversationLease:
    """Propiedad opaca de un turno que evita limpiar sesiones ajenas."""

    _registry: ConversationRegistry = field(repr=False, compare=False)
    _phone_hash: str = field(repr=False)
    _token: object = field(repr=False, compare=False)

    def transition(self, target: ConversationState) -> bool:
        """Transiciona el turno si esta lease todavía es propietaria."""
        return self._registry._transition_owned(
            self._phone_hash,
            self._token,
            target,
        )

    def register_comprehension_failure(self) -> int | None:
        """Registra un fallo sin guardar texto, audio ni identidad adicional."""
        return self._registry._register_comprehension_failure_owned(
            self._phone_hash,
            self._token,
        )

    def reset_comprehension_failures(self) -> bool:
        """Reinicia la escalada después de una consulta comprendida."""
        return self._registry._reset_comprehension_failures_owned(
            self._phone_hash,
            self._token,
        )

    def finish(self) -> bool:
        """Libera un turno completado y conserva su estado de espera."""
        return self._registry._release_owned(
            self._phone_hash,
            self._token,
            discard=False,
        )

    def abort(self) -> bool:
        """Retira un turno fallido sin afectar una lease posterior."""
        return self._registry._release_owned(
            self._phone_hash,
            self._token,
            discard=True,
        )


def _fixed_clock(now: float) -> MonotonicClock:
    """Construye un reloj constante para una operación atómica."""

    def read() -> float:
        return now

    return read
