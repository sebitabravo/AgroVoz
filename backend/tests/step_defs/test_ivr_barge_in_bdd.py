"""Steps BDD del barge-in en el canal IVR (C10).

Ejercita IvrTurnController directamente: la lógica de negocio del turno de
habla, desacoplada del puente ARI real (ver app/services/ivr_turn_service.py
para la justificación del split).
"""

from __future__ import annotations

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from app.services.ivr_turn_service import IvrAction, IvrTurnController, IvrTurnState

scenarios("../features/ivr_barge_in.feature")


@pytest.fixture
def contexto() -> dict[str, object]:
    """Guarda el controller y la última acción entre steps Given/When/Then."""
    return {"controller": IvrTurnController()}


@given("que el IVR está reproduciendo la locución del precio de la papa")
def _reproduciendo_locucion(contexto: dict[str, object]) -> None:
    controller = contexto["controller"]
    assert isinstance(controller, IvrTurnController)
    controller.start_playback(["El precio de la papa es 500 pesos"])


@given("que el IVR está grabando lo que dice el productor")
def _grabando(contexto: dict[str, object]) -> None:
    controller = contexto["controller"]
    assert isinstance(controller, IvrTurnController)
    controller.start_playback(["El precio de la papa es 500 pesos"])
    controller.on_talk_started()


@given("que el IVR está procesando la transcripción del productor")
def _procesando(contexto: dict[str, object]) -> None:
    controller = contexto["controller"]
    assert isinstance(controller, IvrTurnController)
    controller.start_playback(["El precio de la papa es 500 pesos"])
    controller.on_talk_started()
    controller.on_talk_finished()


@when("el productor empieza a hablar", target_fixture="contexto")
def _empieza_a_hablar(contexto: dict[str, object]) -> dict[str, object]:
    controller = contexto["controller"]
    assert isinstance(controller, IvrTurnController)
    contexto["accion"] = controller.on_talk_started()
    return contexto


@when("el productor sigue hablando", target_fixture="contexto")
def _sigue_hablando(contexto: dict[str, object]) -> dict[str, object]:
    controller = contexto["controller"]
    assert isinstance(controller, IvrTurnController)
    contexto["accion"] = controller.on_talk_started()
    return contexto


@when("el productor deja de hablar", target_fixture="contexto")
def _deja_de_hablar(contexto: dict[str, object]) -> dict[str, object]:
    controller = contexto["controller"]
    assert isinstance(controller, IvrTurnController)
    contexto["accion"] = controller.on_talk_finished()
    return contexto


@when(
    parsers.parse("la respuesta sobre el precio del {producto} está lista"),
    target_fixture="contexto",
)
def _respuesta_lista(producto: str, contexto: dict[str, object]) -> dict[str, object]:
    controller = contexto["controller"]
    assert isinstance(controller, IvrTurnController)
    contexto["accion"] = controller.on_transcription_ready([f"El precio del {producto} es 300 pesos"])
    return contexto


@when("la locución termina sin que el productor la interrumpa", target_fixture="contexto")
def _locucion_termina(contexto: dict[str, object]) -> dict[str, object]:
    controller = contexto["controller"]
    assert isinstance(controller, IvrTurnController)
    contexto["accion"] = controller.on_playback_finished()
    return contexto


@then("el IVR corta la locución y empieza a grabar")
def _valida_corte_y_grabacion(contexto: dict[str, object]) -> None:
    controller = contexto["controller"]
    assert isinstance(controller, IvrTurnController)
    assert contexto["accion"] == IvrAction.STOP_PLAYBACK_START_RECORDING
    assert controller.state == IvrTurnState.LISTENING
    assert controller.pending_chunks == []


@then("el IVR deja de grabar y procesa la transcripción")
def _valida_fin_de_grabacion(contexto: dict[str, object]) -> None:
    controller = contexto["controller"]
    assert isinstance(controller, IvrTurnController)
    assert contexto["accion"] == IvrAction.STOP_RECORDING_TRANSCRIBE
    assert controller.state == IvrTurnState.PROCESSING


@then("el IVR reproduce la nueva locución")
def _valida_nueva_locucion(contexto: dict[str, object]) -> None:
    controller = contexto["controller"]
    assert isinstance(controller, IvrTurnController)
    assert contexto["accion"] == IvrAction.START_PLAYBACK
    assert controller.state == IvrTurnState.PLAYING
    assert controller.pending_chunks


@then("el IVR no repite ninguna acción y sigue grabando")
def _valida_sin_repeticion(contexto: dict[str, object]) -> None:
    controller = contexto["controller"]
    assert isinstance(controller, IvrTurnController)
    assert contexto["accion"] == IvrAction.NONE
    assert controller.state == IvrTurnState.LISTENING


@then("el IVR empieza a grabar la respuesta del productor")
def _valida_empieza_a_grabar(contexto: dict[str, object]) -> None:
    controller = contexto["controller"]
    assert isinstance(controller, IvrTurnController)
    assert contexto["accion"] == IvrAction.START_RECORDING
    assert controller.state == IvrTurnState.LISTENING
