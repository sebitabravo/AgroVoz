"""Worker reiniciable que aísla llama-cpp en un proceso hijo.

Se usa siempre el método ``spawn``. Heredar con ``fork`` un runtime nativo ya
inicializado puede duplicar locks internos de llama.cpp y producir corrupción o
deadlocks. El proceso padre solo intercambia mensajes serializables por Pipe.
"""

from __future__ import annotations

import ctypes
import logging
import multiprocessing
import pickle
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.context import BaseContext
from multiprocessing.process import BaseProcess
from typing import Literal, Protocol, cast
from uuid import uuid4

logger = logging.getLogger(__name__)


class _LlmModel(Protocol):
    """Contrato mínimo implementado por ``llama_cpp.Llama``."""

    def create_chat_completion(
        self,
        *,
        messages: list[dict[str, object]],
        temperature: float,
        max_tokens: int,
    ) -> object:
        """Genera una respuesta serializable."""


@dataclass(frozen=True, slots=True)
class LlmWorkerConfig:
    """Configuración inmutable necesaria para cargar y ejecutar el modelo."""

    model_path: str
    n_ctx: int
    n_threads: int
    n_batch: int
    prompt_cache_bytes: int
    startup_timeout_seconds: float = 60.0
    request_timeout_seconds: float = 25.0


@dataclass(frozen=True, slots=True)
class LlmWorkerHealth:
    """Snapshot observable del ciclo de vida del worker."""

    running: bool
    pid: int | None
    restart_count: int
    last_error_code: str | None


@dataclass(frozen=True, slots=True)
class _WorkerCommand:
    """Solicitud interna enviada al proceso hijo."""

    kind: Literal["complete", "shutdown"]
    request_id: str
    messages: list[dict[str, object]]
    max_tokens: int


@dataclass(frozen=True, slots=True)
class _WorkerReady:
    """Handshake de carga del modelo."""

    ok: bool
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class _WorkerResponse:
    """Respuesta correlacionada de una inferencia."""

    request_id: str
    status: Literal["ok", "error"]
    payload: object | None = None
    error_code: str | None = None


class LlmWorkerError(RuntimeError):
    """Error base con código seguro para telemetría."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LlmWorkerBusyError(LlmWorkerError):
    """Ya existe una inferencia en curso."""


class LlmWorkerTimeoutError(LlmWorkerError):
    """El worker excedió el timeout y fue terminado."""


class LlmWorkerCrashedError(LlmWorkerError):
    """El proceso terminó antes de responder."""


class LlmWorkerUnavailableError(LlmWorkerError):
    """El proceso no pudo iniciar o cargar el modelo."""


class LlmWorkerProtocolError(LlmWorkerError):
    """El worker entregó un envelope o payload inválido."""


class LlmWorkerRemoteError(LlmWorkerError):
    """La inferencia falló de forma controlada dentro del hijo."""


ModelFactory = Callable[[LlmWorkerConfig], _LlmModel]


def _preload_cxx_runtime() -> None:
    """Carga el runtime C++ dentro del hijo antes de importar llama_cpp."""
    for soname in ("libstdc++.so.6", "libc++.1.dylib"):
        try:
            ctypes.CDLL(soname, mode=ctypes.RTLD_GLOBAL)
            return
        except OSError:
            continue


def _load_llama_model(config: LlmWorkerConfig) -> _LlmModel:
    """Carga llama.cpp y su cache KV exclusivamente en el proceso hijo."""
    _preload_cxx_runtime()
    from llama_cpp import Llama

    model = Llama(
        model_path=config.model_path,
        n_ctx=config.n_ctx,
        n_threads=config.n_threads,
        n_batch=config.n_batch,
        verbose=False,
    )
    try:
        # Los stubs no exponen esta clase, disponible en runtime desde 0.2.x.
        from llama_cpp import LlamaRAMCache  # type: ignore[attr-defined]

        model.set_cache(
            LlamaRAMCache(capacity_bytes=config.prompt_cache_bytes)
        )
    except (ImportError, AttributeError, TypeError, ValueError):
        pass
    return cast("_LlmModel", model)


def _worker_main(
    connection: Connection,
    config: LlmWorkerConfig,
    model_factory: ModelFactory,
) -> None:
    """Carga el modelo y procesa solicitudes hasta recibir shutdown."""
    try:
        try:
            model = model_factory(config)
        except BaseException as exc:
            connection.send(_WorkerReady(ok=False, error_code=type(exc).__name__))
            return

        connection.send(_WorkerReady(ok=True))
        while True:
            command = connection.recv()
            if not isinstance(command, _WorkerCommand):
                continue
            if command.kind == "shutdown":
                return

            try:
                payload = model.create_chat_completion(
                    messages=command.messages,
                    temperature=0.0,
                    max_tokens=command.max_tokens,
                )
                response = _WorkerResponse(
                    request_id=command.request_id,
                    status="ok",
                    payload=payload,
                )
            except BaseException as exc:
                response = _WorkerResponse(
                    request_id=command.request_id,
                    status="error",
                    error_code=type(exc).__name__,
                )
            connection.send(response)
    except (
        EOFError,
        BrokenPipeError,
        OSError,
        TypeError,
        AttributeError,
        pickle.PicklingError,
    ):
        return
    finally:
        connection.close()


class LlmWorkerManager:
    """Administra salud, timeout y reinicio del proceso llama-cpp."""

    def __init__(
        self,
        config: LlmWorkerConfig,
        *,
        model_factory: ModelFactory = _load_llama_model,
    ) -> None:
        self._config = config
        self._model_factory = model_factory
        self._context: BaseContext = multiprocessing.get_context("spawn")
        self._state_lock = threading.RLock()
        self._request_lock = threading.Lock()
        self._process: BaseProcess | None = None
        self._connection: Connection | None = None
        self._ready = False
        self._starts = 0
        self._last_error_code: str | None = None

    def start(self) -> bool:
        """Inicia el worker y espera un handshake acotado."""
        with self._state_lock:
            if self._is_healthy_locked():
                return True
            self._dispose_locked(terminate=True)

            parent_connection, child_connection = self._context.Pipe(duplex=True)
            # ``get_context('spawn')`` expone Process en runtime; typeshed
            # modela BaseContext sin ese factory concreto.
            process = self._context.Process(  # type: ignore[attr-defined]
                target=_worker_main,
                args=(child_connection, self._config, self._model_factory),
                daemon=True,
                name="agrovoz-llm-worker",
            )
            try:
                process.start()
            except (
                OSError,
                RuntimeError,
                TypeError,
                AttributeError,
                pickle.PicklingError,
            ) as exc:
                parent_connection.close()
                child_connection.close()
                self._last_error_code = type(exc).__name__
                return False

            child_connection.close()
            self._process = process
            self._connection = parent_connection
            if not parent_connection.poll(self._config.startup_timeout_seconds):
                self._last_error_code = "startup_timeout"
                self._dispose_locked(terminate=True)
                return False

            try:
                ready = parent_connection.recv()
            except (EOFError, OSError):
                self._last_error_code = "worker_crashed"
                self._dispose_locked(terminate=True)
                return False
            if not isinstance(ready, _WorkerReady) or not ready.ok:
                self._last_error_code = (
                    ready.error_code
                    if isinstance(ready, _WorkerReady)
                    else "protocol_error"
                )
                self._dispose_locked(terminate=True)
                return False

            self._ready = True
            self._starts += 1
            self._last_error_code = None
            logger.info(
                "LLM worker listo — pid=%s reinicios=%d",
                process.pid,
                max(self._starts - 1, 0),
            )
            return True

    def complete(
        self,
        messages: list[dict[str, object]],
        max_tokens: int,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        """Ejecuta una inferencia o produce un error de worker tipado."""
        if not self._request_lock.acquire(blocking=False):
            raise LlmWorkerBusyError("worker_busy")
        try:
            if not self.start():
                raise LlmWorkerUnavailableError(
                    self._last_error_code or "worker_unavailable"
                )
            return self._complete_locked(
                messages,
                max_tokens,
                timeout_seconds or self._config.request_timeout_seconds,
            )
        finally:
            self._request_lock.release()

    def restart(self) -> bool:
        """Reemplaza el proceso actual y espera que el nuevo quede listo."""
        self.stop()
        return self.start()

    def stop(self) -> None:
        """Solicita cierre ordenado y fuerza término si no responde."""
        with self._request_lock, self._state_lock:
            if self._is_healthy_locked() and self._connection is not None:
                with suppress(BrokenPipeError, EOFError, OSError):
                    self._connection.send(
                        _WorkerCommand(
                            kind="shutdown",
                            request_id=str(uuid4()),
                            messages=[],
                            max_tokens=0,
                        )
                    )
            process = self._process
            if process is not None and process.is_alive():
                process.join(timeout=1.0)
            self._dispose_locked(terminate=True)

    def is_healthy(self) -> bool:
        """Indica si existe un hijo listo y vivo."""
        with self._state_lock:
            return self._is_healthy_locked()

    def health(self) -> LlmWorkerHealth:
        """Retorna salud y métricas sin iniciar el proceso."""
        with self._state_lock:
            process = self._process
            return LlmWorkerHealth(
                running=self._is_healthy_locked(),
                pid=process.pid if process is not None else None,
                restart_count=max(self._starts - 1, 0),
                last_error_code=self._last_error_code,
            )

    def _complete_locked(
        self,
        messages: list[dict[str, object]],
        max_tokens: int,
        timeout_seconds: float,
    ) -> dict[str, object]:
        """Intercambia un request mientras el lock serial ya está tomado."""
        with self._state_lock:
            connection = self._connection
            process = self._process
            if connection is None or process is None:
                raise LlmWorkerUnavailableError("worker_unavailable")

            request_id = str(uuid4())
            try:
                connection.send(
                    _WorkerCommand(
                        kind="complete",
                        request_id=request_id,
                        messages=messages,
                        max_tokens=max_tokens,
                    )
                )
            except (
                BrokenPipeError,
                EOFError,
                OSError,
                TypeError,
                AttributeError,
                pickle.PicklingError,
            ):
                self._last_error_code = "worker_crashed"
                self._dispose_locked(terminate=True)
                raise LlmWorkerCrashedError("worker_crashed") from None

            try:
                has_response = connection.poll(timeout_seconds)
            except (EOFError, OSError):
                self._last_error_code = "worker_crashed"
                self._dispose_locked(terminate=True)
                raise LlmWorkerCrashedError("worker_crashed") from None
            if not has_response:
                error_code = (
                    "worker_timeout" if process.is_alive() else "worker_crashed"
                )
                self._last_error_code = error_code
                self._dispose_locked(terminate=True, force=True)
                if error_code == "worker_timeout":
                    raise LlmWorkerTimeoutError(error_code)
                raise LlmWorkerCrashedError(error_code)

            try:
                response = connection.recv()
            except (
                EOFError,
                OSError,
                TypeError,
                AttributeError,
                pickle.UnpicklingError,
            ):
                self._last_error_code = "worker_crashed"
                self._dispose_locked(terminate=True)
                raise LlmWorkerCrashedError("worker_crashed") from None

            if (
                not isinstance(response, _WorkerResponse)
                or response.request_id != request_id
            ):
                self._last_error_code = "protocol_error"
                self._dispose_locked(terminate=True)
                raise LlmWorkerProtocolError("protocol_error")
            if response.status == "error":
                error_code = response.error_code or "remote_error"
                self._last_error_code = error_code
                raise LlmWorkerRemoteError(error_code)
            if not isinstance(response.payload, dict):
                self._last_error_code = "malformed_response"
                self._dispose_locked(terminate=True)
                raise LlmWorkerProtocolError("malformed_response")

            self._last_error_code = None
            return cast("dict[str, object]", response.payload)

    def _is_healthy_locked(self) -> bool:
        """Evalúa salud con el lock de estado ya adquirido."""
        return (
            self._ready
            and self._process is not None
            and self._process.is_alive()
            and self._connection is not None
        )

    def _dispose_locked(self, *, terminate: bool, force: bool = False) -> None:
        """Libera handles con el lock de estado ya adquirido.

        ``llama.cpp`` puede quedarse ejecutando código nativo aunque Python ya
        haya vencido el timeout. En ese caso ``SIGTERM`` no basta para liberar
        el lock de inferencia: se usa ``SIGKILL`` inmediatamente y el siguiente
        request puede levantar un worker limpio sin contaminar la cola.
        """
        process = self._process
        connection = self._connection
        self._ready = False
        self._process = None
        self._connection = None

        if connection is not None:
            connection.close()
        if process is None:
            return
        if terminate and process.is_alive():
            if force:
                process.kill()
                process.join(timeout=1.0)
                return
            process.terminate()
            process.join(timeout=1.0)
            if process.is_alive():
                process.kill()
                process.join(timeout=1.0)
        elif not process.is_alive():
            process.join(timeout=0.1)
