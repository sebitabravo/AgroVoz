"""Máquina de estados de turnos para el canal IVR de respaldo (C10).

Decide QUÉ hacer ante un evento de habla del productor (VAD detectó que
empezó o dejó de hablar, una locución terminó, hay una transcripción lista)
sin saber CÓMO se conecta con el canal real de Asterisk. El puente ARI
(scripts/ivr_ari_bridge.py) traduce eventos de Asterisk a los métodos de
``IvrTurnController`` y traduce las acciones devueltas a comandos ARI
(Playback, Record, variable TALK_DETECT).

Este split — lógica de turnos vs. adaptador de I/O — es lo que permite
testear a fondo la decisión de negocio (cuándo cortar la locución, cuándo
empezar a grabar) sin depender de un Asterisk real corriendo.

Solo cubre el canal IVR de respaldo (spike técnico, issue #172): sin
llamadas públicas ni PSTN — ver docs/spike-ivr.md. El barge-in (interrumpir
la locución porque el productor empezó a hablar encima) y el streaming de
la respuesta en fragmentos son la extensión de tiempo real que ese spike
no cubría todavía.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class IvrTurnState(enum.Enum):
    """Estado del turno de habla de una llamada IVR."""

    IDLE = "idle"
    PLAYING = "playing"
    LISTENING = "listening"
    PROCESSING = "processing"
    ENDED = "ended"


class IvrAction(enum.Enum):
    """Acción que el puente ARI debe ejecutar contra el canal real."""

    NONE = "none"
    START_PLAYBACK = "start_playback"
    START_RECORDING = "start_recording"
    STOP_PLAYBACK_START_RECORDING = "stop_playback_start_recording"
    STOP_RECORDING_TRANSCRIBE = "stop_recording_transcribe"
    HANGUP = "hangup"


@dataclass
class IvrTurnController:
    """Controla el turno de habla de una llamada IVR (VAD, streaming, barge-in).

    No conoce Asterisk ni ARI: solo decide la próxima acción dada una
    transición. ``pending_chunks`` guarda los fragmentos de audio que
    todavía no terminaron de reproducirse, para poder descartarlos de
    inmediato si el productor interrumpe hablando encima (barge-in).
    """

    state: IvrTurnState = IvrTurnState.IDLE
    pending_chunks: list[str] = field(default_factory=list)

    def start_playback(self, chunks: list[str]) -> IvrAction:
        """Inicia la reproducción de fragmentos de audio (locución o respuesta)."""
        if self.state == IvrTurnState.ENDED:
            return IvrAction.NONE
        if not chunks:
            return IvrAction.NONE
        self.pending_chunks = list(chunks)
        self.state = IvrTurnState.PLAYING
        return IvrAction.START_PLAYBACK

    def on_talk_started(self) -> IvrAction:
        """VAD detectó que el productor empezó a hablar.

        Solo es un barge-in real si estábamos reproduciendo algo: si ya
        estábamos escuchando, este evento es ruido (el productor sigue
        hablando) y no dispara ninguna acción nueva.
        """
        if self.state != IvrTurnState.PLAYING:
            return IvrAction.NONE
        self.pending_chunks = []
        self.state = IvrTurnState.LISTENING
        return IvrAction.STOP_PLAYBACK_START_RECORDING

    def on_talk_finished(self) -> IvrAction:
        """VAD detectó que el productor dejó de hablar: a procesar lo grabado."""
        if self.state != IvrTurnState.LISTENING:
            return IvrAction.NONE
        self.state = IvrTurnState.PROCESSING
        return IvrAction.STOP_RECORDING_TRANSCRIBE

    def on_playback_finished(self) -> IvrAction:
        """La locución terminó sin interrupción: toca escuchar al productor."""
        if self.state != IvrTurnState.PLAYING:
            return IvrAction.NONE
        self.pending_chunks = []
        self.state = IvrTurnState.LISTENING
        return IvrAction.START_RECORDING

    def on_transcription_ready(self, response_chunks: list[str]) -> IvrAction:
        """La respuesta generada a partir de la transcripción está lista.

        ``response_chunks`` puede venir vacío si el pipeline no encontró
        una respuesta (ej: producto no reconocido); en ese caso no hay
        nada que reproducir y la llamada queda pendiente de un hangup
        explícito del puente ARI.
        """
        if self.state != IvrTurnState.PROCESSING:
            return IvrAction.NONE
        return self.start_playback(response_chunks)

    def hangup(self) -> IvrAction:
        """Cierra el turno: ningún evento posterior dispara otra acción."""
        if self.state == IvrTurnState.ENDED:
            return IvrAction.NONE
        self.state = IvrTurnState.ENDED
        self.pending_chunks = []
        return IvrAction.HANGUP
