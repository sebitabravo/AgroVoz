"""Tests para state machine de conversación (issue #192)."""

import time

from app.services.conversation_state import (
    Conversation,
    ConversationState,
)


class TestTransicionesValidas:
    """Verifica que las transiciones válidas funcionen."""

    def test_esperando_a_consulta_recibida(self) -> None:
        conv = Conversation(state=ConversationState.ESPERANDO_CONSULTA)
        assert conv.can_transition_to(ConversationState.CONSULTA_RECIBIDA)

    def test_consulta_recibida_a_buscando_datos(self) -> None:
        conv = Conversation(state=ConversationState.CONSULTA_RECIBIDA)
        assert conv.can_transition_to(ConversationState.BUSCANDO_DATOS)

    def test_consulta_recibida_a_aclarando(self) -> None:
        conv = Conversation(state=ConversationState.CONSULTA_RECIBIDA)
        assert conv.can_transition_to(ConversationState.ACLARANDO)

    def test_buscando_a_respondiendo(self) -> None:
        conv = Conversation(state=ConversationState.BUSCANDO_DATOS)
        assert conv.can_transition_to(ConversationState.RESPONDIENDO)

    def test_respondiendo_a_esperando(self) -> None:
        conv = Conversation(state=ConversationState.RESPONDIENDO)
        assert conv.can_transition_to(ConversationState.ESPERANDO_CONSULTA)

    def test_aclarando_a_derivado(self) -> None:
        conv = Conversation(state=ConversationState.ACLARANDO)
        assert conv.can_transition_to(ConversationState.DERIVADO)


class TestTransicionesInvalidas:
    """Verifica que las transiciones inválidas sean rechazadas."""

    def test_esperando_a_buscando_directo(self) -> None:
        """No se puede saltar de esperando a buscando sin pasar por consulta_recibida."""
        conv = Conversation(state=ConversationState.ESPERANDO_CONSULTA)
        assert not conv.can_transition_to(ConversationState.BUSCANDO_DATOS)

    def test_cerrado_no_transiciona(self) -> None:
        """Estado terminal CERRADO no permite más transiciones."""
        conv = Conversation(state=ConversationState.CERRADO)
        assert not conv.can_transition_to(ConversationState.ESPERANDO_CONSULTA)

    def test_derivado_no_transiciona(self) -> None:
        """Estado terminal DERIVADO no permite más transiciones."""
        conv = Conversation(state=ConversationState.DERIVADO)
        assert not conv.can_transition_to(ConversationState.ESPERANDO_CONSULTA)

    def test_respondiendo_a_buscando_directo(self) -> None:
        conv = Conversation(state=ConversationState.RESPONDIENDO)
        assert not conv.can_transition_to(ConversationState.BUSCANDO_DATOS)


class TestTransitionTo:
    """Verifica que transition_to retorne nueva instancia inmutable."""

    def test_transicion_valida_retorna_nueva_instancia(self) -> None:
        conv = Conversation(state=ConversationState.ESPERANDO_CONSULTA)
        nueva = conv.transition_to(ConversationState.CONSULTA_RECIBIDA)
        assert nueva is not conv
        assert nueva.state == ConversationState.CONSULTA_RECIBIDA

    def test_transicion_invalida_retorna_self(self) -> None:
        conv = Conversation(state=ConversationState.CERRADO)
        misma = conv.transition_to(ConversationState.ESPERANDO_CONSULTA)
        assert misma is conv  # misma instancia, sin modificar
        assert misma.state == ConversationState.CERRADO

    def test_turn_count_incrementa_en_consulta(self) -> None:
        conv = Conversation()
        conv = conv.transition_to(ConversationState.CONSULTA_RECIBIDA)
        assert conv.turn_count == 1


class TestTimeout:
    """Verifica el timeout de inactividad."""

    def test_timeout_no_alcanzado(self) -> None:
        conv = Conversation(
            state=ConversationState.RESPONDIENDO,
            last_activity=time.monotonic(),
        )
        assert not conv.timeout_reached(timeout_minutes=30)

    def test_timeout_alcanzado(self) -> None:
        conv = Conversation(
            state=ConversationState.RESPONDIENDO,
            last_activity=time.monotonic() - (31 * 60),  # 31 min atrás
        )
        assert conv.timeout_reached(timeout_minutes=30)

    def test_timeout_no_alcanzado_bajo_limite(self) -> None:
        conv = Conversation(
            state=ConversationState.RESPONDIENDO,
            last_activity=time.monotonic() - (29 * 60),  # 29 min atrás
        )
        assert not conv.timeout_reached(timeout_minutes=30)


class TestIsActive:
    """Verifica is_active según el estado."""

    def test_activo_en_esperando(self) -> None:
        assert Conversation(state=ConversationState.ESPERANDO_CONSULTA).is_active

    def test_inactivo_en_cerrado(self) -> None:
        assert not Conversation(state=ConversationState.CERRADO).is_active

    def test_inactivo_en_derivado(self) -> None:
        assert not Conversation(state=ConversationState.DERIVADO).is_active
