"""Tests para state machine de conversación (issue #192)."""

import logging
from dataclasses import FrozenInstanceError
from threading import Barrier, Lock, Thread

import pytest

from app.services.conversation_state import (
    Conversation,
    ConversationRegistry,
    ConversationState,
    TransitionStatus,
)


class RelojControlado:
    """Reloj monotónico mutable para pruebas deterministas."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        """Retorna el instante configurado."""
        return self.now


def _registrar_fallo_de_comprension(
    registry: ConversationRegistry,
    phone_hash: str,
) -> int:
    """Completa un turno fallido y retorna su nivel de escalada."""
    claim = registry.claim(phone_hash)
    assert claim.lease is not None
    assert claim.lease.transition(ConversationState.BUSCANDO_DATOS)
    failure_count = claim.lease.register_comprehension_failure()
    assert failure_count is not None
    assert claim.lease.transition(ConversationState.ACLARANDO)
    assert claim.lease.transition(ConversationState.ESPERANDO_CONSULTA)
    assert claim.lease.finish()
    return failure_count


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

    def test_transicion_usa_reloj_inyectado_sin_mutar_original(self) -> None:
        """La nueva actividad usa el reloj controlado y conserva el origen."""
        reloj = RelojControlado(now=250.0)
        original = Conversation(last_activity=100.0)

        nueva = original.transition_to(
            ConversationState.CONSULTA_RECIBIDA,
            clock=reloj,
        )

        assert original.last_activity == 100.0
        assert original.state == ConversationState.ESPERANDO_CONSULTA
        assert nueva is not original
        assert nueva.last_activity == 250.0


class TestTimeout:
    """Verifica el timeout de inactividad."""

    @pytest.mark.parametrize(
        ("elapsed_seconds", "expected"),
        [
            (1799.999, False),
            (1800.0, True),
            (1800.001, True),
        ],
    )
    def test_timeout_antes_igual_y_despues_del_limite(
        self,
        elapsed_seconds: float,
        expected: bool,
    ) -> None:
        """El límite exacto ya cuenta como timeout."""
        conv = Conversation(
            state=ConversationState.RESPONDIENDO,
            last_activity=100.0,
        )
        reloj = RelojControlado(now=100.0 + elapsed_seconds)

        assert (
            conv.timeout_reached(
                timeout_minutes=30,
                clock=reloj,
            )
            is expected
        )

    @pytest.mark.parametrize(
        "state",
        [
            ConversationState.ESPERANDO_CONSULTA,
            ConversationState.CONSULTA_RECIBIDA,
            ConversationState.BUSCANDO_DATOS,
            ConversationState.ACLARANDO,
            ConversationState.RESPONDIENDO,
        ],
    )
    def test_close_if_timed_out_cierra_cualquier_estado_activo(
        self,
        state: ConversationState,
    ) -> None:
        """El cierre por ciclo de vida no depende de la transición funcional."""
        original = Conversation(
            state=state,
            last_activity=100.0,
            turn_count=3,
        )

        cerrada = original.close_if_timed_out(
            timeout_minutes=30,
            clock=RelojControlado(now=1900.0),
        )

        assert original.state == state
        assert original.last_activity == 100.0
        assert cerrada is not original
        assert cerrada.state == ConversationState.CERRADO
        assert cerrada.last_activity == 1900.0
        assert cerrada.turn_count == 3

    def test_close_if_timed_out_conserva_activa_antes_del_limite(self) -> None:
        """No crea una instancia nueva mientras la conversación siga vigente."""
        original = Conversation(last_activity=100.0)

        vigente = original.close_if_timed_out(
            timeout_minutes=30,
            clock=RelojControlado(now=1899.999),
        )

        assert vigente is original

    @pytest.mark.parametrize(
        "state",
        [ConversationState.CERRADO, ConversationState.DERIVADO],
    )
    def test_close_if_timed_out_conserva_estados_terminales(
        self,
        state: ConversationState,
    ) -> None:
        """Un estado terminal no se reemplaza aunque su actividad sea antigua."""
        original = Conversation(state=state, last_activity=0.0)

        terminal = original.close_if_timed_out(
            timeout_minutes=30,
            clock=RelojControlado(now=10_000.0),
        )

        assert terminal is original

    def test_timeout_rechaza_minutos_no_positivos(self) -> None:
        """El dominio también evita timeouts que cerrarían de inmediato."""
        conv = Conversation(last_activity=0.0)

        with pytest.raises(ValueError, match="mayor o igual a 1"):
            conv.timeout_reached(
                timeout_minutes=0,
                clock=RelojControlado(now=0.0),
            )


class TestIsActive:
    """Verifica is_active según el estado."""

    def test_activo_en_esperando(self) -> None:
        assert Conversation(state=ConversationState.ESPERANDO_CONSULTA).is_active

    def test_inactivo_en_cerrado(self) -> None:
        assert not Conversation(state=ConversationState.CERRADO).is_active

    def test_inactivo_en_derivado(self) -> None:
        assert not Conversation(state=ConversationState.DERIVADO).is_active


class TestConversationRegistry:
    """Contratos de aislamiento, ciclo de vida y privacidad del registro."""

    def test_rechaza_phone_hash_vacio(self) -> None:
        """Una clave vacía nunca crea una entrada anónima compartida."""
        registry = ConversationRegistry(
            timeout_minutes=30,
            clock=RelojControlado(now=0.0),
        )

        with pytest.raises(ValueError, match="phone_hash"):
            registry.get_or_create("   ")

        assert registry.clear() == 0

    @pytest.mark.parametrize(
        "invalid_phone_hash",
        [
            "+56912345678",
            "sin_chat",
            "A" * 64,
            "../../etc/passwd",
            "a" * 63,
        ],
    )
    def test_rechaza_identidades_no_hmac_sin_mutar_ni_loguear(
        self,
        invalid_phone_hash: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Inputs no HMAC se rechazan antes de tocar memoria o logs."""
        registry = ConversationRegistry(
            timeout_minutes=30,
            clock=RelojControlado(now=0.0),
        )
        caplog.set_level(
            logging.DEBUG,
            logger="app.services.conversation_state",
        )

        with pytest.raises(ValueError, match="HMAC-SHA256"):
            registry.get_or_create(invalid_phone_hash)

        assert registry.clear() == 0
        assert invalid_phone_hash not in caplog.text

    def test_aisla_conversaciones_entre_usuarios(self) -> None:
        """La transición de un hash no modifica la sesión de otro."""
        registry = ConversationRegistry(
            timeout_minutes=30,
            clock=RelojControlado(now=100.0),
        )
        hash_a = "a" * 64
        hash_b = "b" * 64

        registry.transition(hash_a, ConversationState.CONSULTA_RECIBIDA)
        conversation_b = registry.get_or_create(hash_b)

        conversation_a = registry.snapshot(hash_a)
        assert conversation_a is not None
        assert conversation_a.state == ConversationState.CONSULTA_RECIBIDA
        assert conversation_b.state == ConversationState.ESPERANDO_CONSULTA
        assert conversation_b.turn_count == 0

    def test_snapshot_no_retiene_identidad_ni_contenido(self) -> None:
        """El snapshot solo contiene el estado mínimo e inmutable."""
        phone_hash = "c" * 64
        registry = ConversationRegistry(
            timeout_minutes=30,
            clock=RelojControlado(now=100.0),
        )

        snapshot = registry.get_or_create(phone_hash)

        assert set(vars(snapshot)) == {
            "state",
            "last_activity",
            "turn_count",
            "comprehension_failures",
        }
        assert phone_hash not in repr(snapshot)
        assert "query" not in repr(snapshot).lower()
        assert "response" not in repr(snapshot).lower()
        with pytest.raises(FrozenInstanceError):
            snapshot.turn_count = 9

    def test_boundary_timeout_cierra_y_evicta_en_limite_exacto(self) -> None:
        """La entrada existe antes del límite y se elimina al alcanzarlo."""
        reloj = RelojControlado(now=100.0)
        phone_hash = "d" * 64
        registry = ConversationRegistry(timeout_minutes=1, clock=reloj)
        original = registry.get_or_create(phone_hash)

        reloj.now = 159.999
        assert registry.snapshot(phone_hash) is original

        reloj.now = 160.0
        assert registry.snapshot(phone_hash) is None

    def test_acceso_poda_otras_conversaciones_vencidas(self) -> None:
        """Crear otra sesión también evita crecimiento por entradas antiguas."""
        reloj = RelojControlado(now=0.0)
        registry = ConversationRegistry(timeout_minutes=1, clock=reloj)
        old_hash = "e" * 64
        new_hash = "f" * 64
        registry.get_or_create(old_hash)

        reloj.now = 60.0
        registry.get_or_create(new_hash)

        assert registry.snapshot(old_hash) is None
        assert registry.snapshot(new_hash) is not None

    def test_reinicia_limpio_despues_de_timeout(self) -> None:
        """Una consulta posterior obtiene un turno nuevo sin estado heredado."""
        reloj = RelojControlado(now=0.0)
        phone_hash = "1" * 64
        registry = ConversationRegistry(timeout_minutes=1, clock=reloj)
        registry.transition(phone_hash, ConversationState.CONSULTA_RECIBIDA)

        reloj.now = 60.0
        restarted = registry.get_or_create(phone_hash)

        assert restarted.state == ConversationState.ESPERANDO_CONSULTA
        assert restarted.last_activity == 60.0
        assert restarted.turn_count == 0
        assert restarted.comprehension_failures == 0

    def test_fallos_consecutivos_se_acumulan_y_se_topan_en_tres(self) -> None:
        """La escalada conserva tres niveles aunque continúen los fallos."""
        phone_hash = "a" * 64
        registry = ConversationRegistry(
            timeout_minutes=30,
            clock=RelojControlado(now=10.0),
        )

        niveles = [_registrar_fallo_de_comprension(registry, phone_hash) for _attempt in range(4)]

        assert niveles == [1, 2, 3, 3]
        snapshot = registry.snapshot(phone_hash)
        assert snapshot is not None
        assert snapshot.comprehension_failures == 3

    def test_consulta_comprendida_reinicia_escalada(self) -> None:
        """Un éxito confirmado hace que el próximo fallo vuelva al nivel uno."""
        phone_hash = "b" * 64
        registry = ConversationRegistry(
            timeout_minutes=30,
            clock=RelojControlado(now=10.0),
        )
        assert _registrar_fallo_de_comprension(registry, phone_hash) == 1
        assert _registrar_fallo_de_comprension(registry, phone_hash) == 2

        success = registry.claim(phone_hash)
        assert success.lease is not None
        assert success.lease.transition(ConversationState.BUSCANDO_DATOS)
        assert success.lease.reset_comprehension_failures()
        assert success.lease.transition(ConversationState.RESPONDIENDO)
        assert success.lease.transition(ConversationState.ESPERANDO_CONSULTA)
        assert success.lease.finish()

        assert _registrar_fallo_de_comprension(registry, phone_hash) == 1

    def test_escalada_se_aisla_entre_usuarios(self) -> None:
        """Los contadores no se mezclan entre dos hashes válidos."""
        registry = ConversationRegistry(
            timeout_minutes=30,
            clock=RelojControlado(now=10.0),
        )
        hash_a = "c" * 64
        hash_b = "d" * 64

        assert _registrar_fallo_de_comprension(registry, hash_a) == 1
        assert _registrar_fallo_de_comprension(registry, hash_a) == 2
        assert _registrar_fallo_de_comprension(registry, hash_b) == 1

        snapshot_a = registry.snapshot(hash_a)
        snapshot_b = registry.snapshot(hash_b)
        assert snapshot_a is not None
        assert snapshot_b is not None
        assert snapshot_a.comprehension_failures == 2
        assert snapshot_b.comprehension_failures == 1

    def test_timeout_descarta_escalada_anterior(self) -> None:
        """Una sesión vencida vuelve sin fallos heredados."""
        reloj = RelojControlado(now=0.0)
        phone_hash = "e" * 64
        registry = ConversationRegistry(timeout_minutes=1, clock=reloj)
        assert _registrar_fallo_de_comprension(registry, phone_hash) == 1

        reloj.now = 60.0
        assert registry.snapshot(phone_hash) is None
        restarted = registry.get_or_create(phone_hash)

        assert restarted.comprehension_failures == 0

    def test_reinicia_limpio_despues_de_estado_terminal(self) -> None:
        """El siguiente acceso reemplaza una conversación ya cerrada."""
        reloj = RelojControlado(now=10.0)
        phone_hash = "2" * 64
        registry = ConversationRegistry(timeout_minutes=30, clock=reloj)
        registry.transition(phone_hash, ConversationState.CONSULTA_RECIBIDA)
        registry.transition(phone_hash, ConversationState.ACLARANDO)
        terminal = registry.transition(phone_hash, ConversationState.CERRADO)

        restarted = registry.get_or_create(phone_hash)

        assert terminal.status == TransitionStatus.APLICADA
        assert terminal.conversation.state == ConversationState.CERRADO
        assert restarted.state == ConversationState.ESPERANDO_CONSULTA
        assert restarted.turn_count == 0
        assert restarted is not terminal.conversation

    def test_transicion_invalida_es_explicita_y_no_corrompe_estado(self) -> None:
        """Una transición rechazada conserva la sesión original."""
        phone_hash = "3" * 64
        registry = ConversationRegistry(
            timeout_minutes=30,
            clock=RelojControlado(now=10.0),
        )

        result = registry.transition(
            phone_hash,
            ConversationState.BUSCANDO_DATOS,
        )

        assert result.status == TransitionStatus.INVALIDA
        assert result.conversation.state == ConversationState.ESPERANDO_CONSULTA
        assert result.conversation.turn_count == 0
        assert registry.snapshot(phone_hash) is result.conversation

    def test_segunda_consulta_concurrente_se_rechaza_sin_corrupcion(self) -> None:
        """Dos threads producen exactamente una transición y un rechazo."""
        phone_hash = "4" * 64
        registry = ConversationRegistry(
            timeout_minutes=30,
            clock=RelojControlado(now=10.0),
        )
        barrier = Barrier(3)
        results_lock = Lock()
        statuses: list[TransitionStatus] = []

        def submit_query() -> None:
            barrier.wait()
            result = registry.transition(
                phone_hash,
                ConversationState.CONSULTA_RECIBIDA,
            )
            with results_lock:
                statuses.append(result.status)

        threads = [Thread(target=submit_query) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=2)

        assert all(not thread.is_alive() for thread in threads)
        assert sorted(status.value for status in statuses) == [
            TransitionStatus.APLICADA.value,
            TransitionStatus.OCUPADA.value,
        ]
        snapshot = registry.snapshot(phone_hash)
        assert snapshot is not None
        assert snapshot.state == ConversationState.CONSULTA_RECIBIDA
        assert snapshot.turn_count == 1

    def test_logs_de_rechazo_no_exponen_phone_hash(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Warnings operativos solo incluyen estados, nunca la identidad."""
        phone_hash = "7" * 64
        registry = ConversationRegistry(
            timeout_minutes=30,
            clock=RelojControlado(now=10.0),
        )
        caplog.set_level(
            logging.WARNING,
            logger="app.services.conversation_state",
        )
        registry.transition(phone_hash, ConversationState.CONSULTA_RECIBIDA)

        busy_result = registry.transition(
            phone_hash,
            ConversationState.CONSULTA_RECIBIDA,
        )
        invalid_result = registry.transition(
            phone_hash,
            ConversationState.RESPONDIENDO,
        )

        assert busy_result.status == TransitionStatus.OCUPADA
        assert invalid_result.status == TransitionStatus.INVALIDA
        assert phone_hash not in caplog.text

    def test_clear_elimina_todo_sin_exponer_claves(self) -> None:
        """La limpieza informa cantidad, no devuelve identidades."""
        registry = ConversationRegistry(
            timeout_minutes=30,
            clock=RelojControlado(now=10.0),
        )
        registry.get_or_create("5" * 64)
        registry.get_or_create("6" * 64)

        assert registry.clear() == 2
        assert registry.clear() == 0

    def test_claim_reserva_turno_y_lease_lo_finaliza_en_espera(self) -> None:
        """La lease gobierna la secuencia sin exponer un dueño mutable."""
        phone_hash = "8" * 64
        registry = ConversationRegistry(
            timeout_minutes=30,
            clock=RelojControlado(now=10.0),
        )

        claim = registry.claim(phone_hash)

        assert claim.status == TransitionStatus.APLICADA
        assert claim.lease is not None
        assert claim.conversation.state == ConversationState.CONSULTA_RECIBIDA
        assert claim.lease.transition(ConversationState.BUSCANDO_DATOS)
        assert claim.lease.transition(ConversationState.RESPONDIENDO)
        assert claim.lease.transition(ConversationState.ESPERANDO_CONSULTA)
        assert claim.lease.finish()
        snapshot = registry.snapshot(phone_hash)
        assert snapshot is not None
        assert snapshot.state == ConversationState.ESPERANDO_CONSULTA

    def test_segundo_claim_retorna_ocupada_sin_alterar_al_dueno(self) -> None:
        """Una lease activa no puede ser reemplazada por otro request."""
        phone_hash = "9" * 64
        registry = ConversationRegistry(
            timeout_minutes=30,
            clock=RelojControlado(now=10.0),
        )
        owner = registry.claim(phone_hash)

        rejected = registry.claim(phone_hash)

        assert owner.lease is not None
        assert rejected.status == TransitionStatus.OCUPADA
        assert rejected.lease is None
        assert rejected.conversation is owner.conversation
        assert owner.lease.transition(ConversationState.BUSCANDO_DATOS)

    def test_abort_retira_solo_el_turno_propietario(self) -> None:
        """Abort libera de inmediato el hash para una consulta posterior."""
        phone_hash = "0" * 64
        registry = ConversationRegistry(
            timeout_minutes=30,
            clock=RelojControlado(now=10.0),
        )
        first = registry.claim(phone_hash)
        assert first.lease is not None

        assert first.lease.abort()
        restarted = registry.claim(phone_hash)

        assert restarted.status == TransitionStatus.APLICADA
        assert restarted.lease is not None
        assert restarted.conversation.turn_count == 1

    def test_lease_vencida_no_elimina_al_nuevo_dueno(self) -> None:
        """El cleanup tardío de un request no toca la sesión reemplazante."""
        reloj = RelojControlado(now=0.0)
        phone_hash = "a" * 64
        registry = ConversationRegistry(timeout_minutes=1, clock=reloj)
        expired = registry.claim(phone_hash)
        assert expired.lease is not None

        reloj.now = 60.0
        replacement = registry.claim(phone_hash)
        assert replacement.lease is not None

        assert not expired.lease.abort()
        snapshot = registry.snapshot(phone_hash)
        assert snapshot is replacement.conversation
        assert snapshot.state == ConversationState.CONSULTA_RECIBIDA
