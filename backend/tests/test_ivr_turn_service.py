"""Tests de la máquina de estados de turnos del IVR (C10): VAD y barge-in."""

from __future__ import annotations

from app.services.ivr_turn_service import IvrAction, IvrTurnController, IvrTurnState


class TestStartPlayback:
    """Inicio de la reproducción de una locución o respuesta."""

    def test_desde_idle_pasa_a_playing(self) -> None:
        controller = IvrTurnController()

        action = controller.start_playback(["Hola productor"])

        assert action == IvrAction.START_PLAYBACK
        assert controller.state == IvrTurnState.PLAYING
        assert controller.pending_chunks == ["Hola productor"]

    def test_sin_chunks_no_hace_nada(self) -> None:
        controller = IvrTurnController()

        action = controller.start_playback([])

        assert action == IvrAction.NONE
        assert controller.state == IvrTurnState.IDLE

    def test_llamada_terminada_no_reinicia_reproduccion(self) -> None:
        controller = IvrTurnController(state=IvrTurnState.ENDED)

        action = controller.start_playback(["texto"])

        assert action == IvrAction.NONE
        assert controller.state == IvrTurnState.ENDED


class TestBargeIn:
    """El productor interrumpe hablando encima de la locución."""

    def test_hablar_durante_playback_corta_y_empieza_a_grabar(self) -> None:
        controller = IvrTurnController()
        controller.start_playback(["El precio de la papa es 500 pesos"])

        action = controller.on_talk_started()

        assert action == IvrAction.STOP_PLAYBACK_START_RECORDING
        assert controller.state == IvrTurnState.LISTENING
        assert controller.pending_chunks == []

    def test_hablar_en_idle_no_dispara_barge_in(self) -> None:
        controller = IvrTurnController()

        action = controller.on_talk_started()

        assert action == IvrAction.NONE
        assert controller.state == IvrTurnState.IDLE

    def test_hablar_mientras_ya_escucha_no_repite_la_accion(self) -> None:
        """VAD puede disparar talk_started más de una vez mientras la persona sigue hablando."""
        controller = IvrTurnController()
        controller.start_playback(["texto"])
        controller.on_talk_started()

        action = controller.on_talk_started()

        assert action == IvrAction.NONE
        assert controller.state == IvrTurnState.LISTENING

    def test_hablar_durante_processing_no_hace_nada(self) -> None:
        controller = IvrTurnController(state=IvrTurnState.PROCESSING)

        action = controller.on_talk_started()

        assert action == IvrAction.NONE
        assert controller.state == IvrTurnState.PROCESSING


class TestTalkFinished:
    """El productor deja de hablar: toca procesar lo grabado."""

    def test_desde_listening_pasa_a_processing(self) -> None:
        controller = IvrTurnController(state=IvrTurnState.LISTENING)

        action = controller.on_talk_finished()

        assert action == IvrAction.STOP_RECORDING_TRANSCRIBE
        assert controller.state == IvrTurnState.PROCESSING

    def test_sin_estar_escuchando_no_hace_nada(self) -> None:
        controller = IvrTurnController()

        action = controller.on_talk_finished()

        assert action == IvrAction.NONE
        assert controller.state == IvrTurnState.IDLE


class TestPlaybackFinished:
    """La locución termina sola, sin interrupción."""

    def test_termina_reproduccion_y_empieza_a_grabar(self) -> None:
        controller = IvrTurnController()
        controller.start_playback(["texto"])

        action = controller.on_playback_finished()

        assert action == IvrAction.START_RECORDING
        assert controller.state == IvrTurnState.LISTENING
        assert controller.pending_chunks == []

    def test_sin_estar_reproduciendo_no_hace_nada(self) -> None:
        controller = IvrTurnController()

        action = controller.on_playback_finished()

        assert action == IvrAction.NONE
        assert controller.state == IvrTurnState.IDLE


class TestTranscriptionReady:
    """La respuesta generada a partir de la transcripción está lista."""

    def test_con_respuesta_valida_reproduce_de_nuevo(self) -> None:
        controller = IvrTurnController(state=IvrTurnState.PROCESSING)

        action = controller.on_transcription_ready(["El precio del trigo es 300 pesos"])

        assert action == IvrAction.START_PLAYBACK
        assert controller.state == IvrTurnState.PLAYING
        assert controller.pending_chunks == ["El precio del trigo es 300 pesos"]

    def test_sin_respuesta_no_reproduce_nada(self) -> None:
        controller = IvrTurnController(state=IvrTurnState.PROCESSING)

        action = controller.on_transcription_ready([])

        assert action == IvrAction.NONE
        assert controller.state == IvrTurnState.PROCESSING

    def test_fuera_de_processing_no_hace_nada(self) -> None:
        controller = IvrTurnController()

        action = controller.on_transcription_ready(["texto"])

        assert action == IvrAction.NONE
        assert controller.state == IvrTurnState.IDLE


class TestHangup:
    """Cierre de la llamada: ningún evento posterior dispara otra acción."""

    def test_hangup_termina_el_turno(self) -> None:
        controller = IvrTurnController()
        controller.start_playback(["texto"])

        action = controller.hangup()

        assert action == IvrAction.HANGUP
        assert controller.state == IvrTurnState.ENDED
        assert controller.pending_chunks == []

    def test_hangup_repetido_no_hace_nada(self) -> None:
        controller = IvrTurnController(state=IvrTurnState.ENDED)

        action = controller.hangup()

        assert action == IvrAction.NONE
        assert controller.state == IvrTurnState.ENDED

    def test_ningun_evento_reacciona_despues_del_hangup(self) -> None:
        controller = IvrTurnController()
        controller.hangup()

        assert controller.on_talk_started() == IvrAction.NONE
        assert controller.on_talk_finished() == IvrAction.NONE
        assert controller.on_playback_finished() == IvrAction.NONE
        assert controller.on_transcription_ready(["texto"]) == IvrAction.NONE
        assert controller.state == IvrTurnState.ENDED


class TestCicloCompleto:
    """Un intercambio completo: locución, barge-in, transcripción, respuesta."""

    def test_ciclo_con_barge_in(self) -> None:
        controller = IvrTurnController()

        assert controller.start_playback(["Precio de la papa..."]) == IvrAction.START_PLAYBACK
        assert controller.on_talk_started() == IvrAction.STOP_PLAYBACK_START_RECORDING
        assert controller.on_talk_finished() == IvrAction.STOP_RECORDING_TRANSCRIBE
        assert controller.on_transcription_ready(["El precio del trigo es 300 pesos"]) == IvrAction.START_PLAYBACK
        assert controller.state == IvrTurnState.PLAYING

    def test_ciclo_sin_interrupcion(self) -> None:
        controller = IvrTurnController()

        assert controller.start_playback(["Precio de la papa..."]) == IvrAction.START_PLAYBACK
        assert controller.on_playback_finished() == IvrAction.START_RECORDING
        assert controller.on_talk_finished() == IvrAction.STOP_RECORDING_TRANSCRIBE
        assert controller.on_transcription_ready([]) == IvrAction.NONE
        assert controller.hangup() == IvrAction.HANGUP
