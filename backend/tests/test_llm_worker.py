"""Tests del proceso aislado y reiniciable de llama-cpp."""

import os
import time

from app.services.llm_worker import (
    LlmWorkerConfig,
    LlmWorkerCrashedError,
    LlmWorkerManager,
    LlmWorkerProtocolError,
    LlmWorkerTimeoutError,
)


class _FakeModel:
    """Modelo mínimo ejecutado realmente dentro del proceso spawn."""

    def create_chat_completion(
        self,
        *,
        messages: list[dict[str, object]],
        temperature: float,
        max_tokens: int,
    ) -> object:
        command = str(messages[-1].get("content", ""))
        if command == "timeout":
            time.sleep(0.5)
        if command == "crash":
            # Simula un aborto nativo que no puede capturar Python.
            os._exit(23)
        if command == "malformed":
            return "respuesta-no-es-dict"
        return {
            "choices": [
                {
                    "message": {
                        "content": f"respuesta-worker-{max_tokens}",
                    }
                }
            ]
        }


def _fake_model_factory(config: LlmWorkerConfig) -> _FakeModel:
    """Crea el modelo falso sin acceder al GGUF."""
    return _FakeModel()


def _failing_model_factory(config: LlmWorkerConfig) -> _FakeModel:
    """Simula un fallo controlado durante la carga."""
    raise RuntimeError("modelo-no-disponible")


def _manager() -> LlmWorkerManager:
    """Construye un manager rápido que conserva el método spawn real."""
    return LlmWorkerManager(
        LlmWorkerConfig(
            model_path="fake.gguf",
            n_ctx=128,
            n_threads=1,
            n_batch=16,
            prompt_cache_bytes=1_024,
            startup_timeout_seconds=5.0,
            request_timeout_seconds=0.15,
        ),
        model_factory=_fake_model_factory,
    )


def _messages(command: str) -> list[dict[str, object]]:
    """Crea un request mínimo y serializable."""
    return [{"role": "user", "content": command}]


def test_happy_path_health_lifecycle_y_restart() -> None:
    """El worker responde, reporta salud, reinicia y cierra limpiamente."""
    manager = _manager()
    try:
        assert manager.start() is True
        first_health = manager.health()
        assert first_health.running is True
        assert first_health.pid is not None
        assert first_health.restart_count == 0

        response = manager.complete(_messages("ok"), max_tokens=32)
        assert response["choices"] == [
            {"message": {"content": "respuesta-worker-32"}}
        ]

        assert manager.restart() is True
        restarted_health = manager.health()
        assert restarted_health.running is True
        assert restarted_health.restart_count == 1
        assert restarted_health.pid != first_health.pid
    finally:
        manager.stop()

    assert manager.is_healthy() is False


def test_timeout_termina_worker_y_siguiente_request_reinicia() -> None:
    """Una inferencia colgada no sobrevive y el manager se recupera."""
    manager = _manager()
    try:
        try:
            manager.complete(_messages("timeout"), max_tokens=16)
        except LlmWorkerTimeoutError as exc:
            assert exc.code == "worker_timeout"
        else:
            raise AssertionError("Se esperaba timeout del worker")

        assert manager.is_healthy() is False
        assert manager.health().last_error_code == "worker_timeout"

        response = manager.complete(_messages("ok"), max_tokens=16)
        assert response["choices"] == [
            {"message": {"content": "respuesta-worker-16"}}
        ]
        assert manager.health().restart_count == 1
    finally:
        manager.stop()


def test_crash_nativo_no_mata_padre_y_worker_se_reinicia() -> None:
    """Un os._exit en el hijo se reporta y una inferencia posterior funciona."""
    manager = _manager()
    try:
        try:
            manager.complete(_messages("crash"), max_tokens=16)
        except LlmWorkerCrashedError as exc:
            assert exc.code == "worker_crashed"
        else:
            raise AssertionError("Se esperaba crash del worker")

        assert manager.is_healthy() is False
        response = manager.complete(_messages("ok"), max_tokens=8)
        assert response["choices"] == [
            {"message": {"content": "respuesta-worker-8"}}
        ]
    finally:
        manager.stop()


def test_respuesta_malformada_descarta_y_reinicia_worker() -> None:
    """Un payload fuera del contrato nunca llega a answer()."""
    manager = _manager()
    try:
        try:
            manager.complete(_messages("malformed"), max_tokens=16)
        except LlmWorkerProtocolError as exc:
            assert exc.code == "malformed_response"
        else:
            raise AssertionError("Se esperaba error de protocolo")

        assert manager.is_healthy() is False
        response = manager.complete(_messages("ok"), max_tokens=4)
        assert response["choices"] == [
            {"message": {"content": "respuesta-worker-4"}}
        ]
    finally:
        manager.stop()


def test_fallo_de_carga_deja_worker_no_saludable() -> None:
    """El fallo controlado del loader queda como código y no mata al padre."""
    manager = LlmWorkerManager(
        LlmWorkerConfig(
            model_path="fake.gguf",
            n_ctx=128,
            n_threads=1,
            n_batch=16,
            prompt_cache_bytes=1_024,
            startup_timeout_seconds=5.0,
            request_timeout_seconds=0.15,
        ),
        model_factory=_failing_model_factory,
    )
    try:
        assert manager.start() is False
        health = manager.health()
        assert health.running is False
        assert health.last_error_code == "RuntimeError"
    finally:
        manager.stop()
