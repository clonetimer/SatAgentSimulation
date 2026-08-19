"""Persistent wrapper around the default unified Basilisk runtime graph."""
from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any

from sat_sim.adapter_base import SimulationResult
from sat_sim.bsk_engine.unified_native import UnifiedNativeRuntime, UnifiedRuntimeConfig


class _UnifiedExecutionController:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._prepared = False
        self._requested_stop: float | None = None
        self._advance_pending = False
        self._finalize = False
        self._abort = False
        self._delta: tuple[dict[str, Any], ...] = ()
        self._completed_stop: float | None = None
        self._commands: list[Any] = []

    def runtime_prepared(self) -> None:
        with self._condition:
            self._prepared = True
            self._condition.notify_all()

    def wait_prepared(self, timeout_s: float) -> None:
        with self._condition:
            if not self._condition.wait_for(lambda: self._prepared or self._abort, timeout_s):
                raise TimeoutError("unified runtime prepare timed out")
            if self._abort:
                raise RuntimeError("unified runtime aborted during prepare")

    def wait_for_advance(self) -> float | None:
        with self._condition:
            self._condition.wait_for(lambda: self._advance_pending or self._finalize or self._abort)
            if self._abort:
                raise RuntimeError("unified runtime aborted")
            if self._finalize:
                return None
            self._advance_pending = False
            return self._requested_stop

    def request_advance(self, stop_time_s: float, timeout_s: float) -> tuple[dict[str, Any], ...]:
        with self._condition:
            if self._advance_pending:
                raise RuntimeError("an advance request is already pending")
            self._requested_stop = stop_time_s
            self._completed_stop = None
            self._advance_pending = True
            self._condition.notify_all()
            if not self._condition.wait_for(lambda: self._completed_stop == stop_time_s or self._abort, timeout_s):
                raise TimeoutError("unified runtime advance timed out")
            if self._abort:
                raise RuntimeError("unified runtime aborted during advance")
            return self._delta

    def runtime_advanced(self, stop_time_s: float, delta: tuple[dict[str, Any], ...]) -> None:
        with self._condition:
            self._delta = delta
            self._completed_stop = stop_time_s
            self._condition.notify_all()

    def request_finalize(self) -> None:
        with self._condition:
            self._finalize = True
            self._condition.notify_all()

    def request_abort(self) -> None:
        with self._condition:
            self._abort = True
            self._condition.notify_all()

    def queue_command(self, command: Any) -> None:
        with self._condition:
            if self._abort or self._finalize:
                raise RuntimeError("unified runtime is closing")
            self._commands.append(command)

    def take_commands(self) -> tuple[Any, ...]:
        with self._condition:
            commands = tuple(self._commands)
            self._commands.clear()
            return commands


class UnifiedPersistentRuntime:
    """Keep the main unified model graph alive between explicit advances."""

    def __init__(self, config: UnifiedRuntimeConfig, *, timeout_s: float = 60.0) -> None:
        self.config = config
        self._timeout_s = timeout_s
        self._controller = _UnifiedExecutionController()
        self._thread: threading.Thread | None = None
        self._current_time_s = 0.0
        self._pending_delta: tuple[dict[str, Any], ...] = ()
        self._result: SimulationResult | None = None
        self._error: BaseException | None = None

    @property
    def current_time_s(self) -> float:
        return self._current_time_s

    @property
    def worker_ident(self) -> int | None:
        return self._thread.ident if self._thread else None

    def _run(self) -> None:
        try:
            config = replace(self.config, execution_stops_s=(), execution_controller=self._controller)
            self._result = UnifiedNativeRuntime(config).run()
        except BaseException as exc:
            self._error = exc
            self._controller.request_abort()

    def _raise_worker_error(self) -> None:
        if self._error is not None:
            raise RuntimeError(f"unified runtime worker failed: {self._error}") from self._error

    def prepare(self) -> None:
        if self._thread is not None:
            raise RuntimeError("runtime is already prepared")
        self._thread = threading.Thread(target=self._run, name="sat-unified-persistent", daemon=True)
        self._thread.start()
        self._controller.wait_prepared(self._timeout_s)
        self._raise_worker_error()

    def advance_to(self, stop_time_s: float) -> tuple[dict[str, Any], ...]:
        stop_time_s = float(stop_time_s)
        if self._thread is None:
            raise RuntimeError("runtime is not prepared")
        if stop_time_s <= self._current_time_s or stop_time_s > self.config.duration_s:
            raise ValueError("stop time must increase and remain within configured duration")
        self._pending_delta = self._controller.request_advance(stop_time_s, self._timeout_s)
        self._raise_worker_error()
        self._current_time_s = stop_time_s
        return self._pending_delta

    def read_delta(self) -> tuple[dict[str, Any], ...]:
        delta, self._pending_delta = self._pending_delta, ()
        return delta

    def apply_command(self, command: Any) -> None:
        if self._thread is None or not self._thread.is_alive():
            raise RuntimeError("runtime is not active")
        self._controller.queue_command(command)

    def finalize(self) -> dict[str, Any]:
        if self._thread is None or self._current_time_s <= 0.0:
            raise RuntimeError("runtime must be prepared and advanced before finalize")
        self._controller.request_finalize()
        self._thread.join(self._timeout_s)
        if self._thread.is_alive():
            raise TimeoutError("unified runtime finalize timed out")
        self._raise_worker_error()
        if self._result is None:
            raise RuntimeError("unified runtime produced no result")
        return {
            "persistent_instance": True,
            "final_sim_time_s": self._current_time_s,
            "summary": self._result.summary,
            "metadata": self._result.metadata,
            "labels": self._result.labels,
        }

    def abort(self) -> None:
        self._controller.request_abort()
        if self._thread is not None:
            self._thread.join(self._timeout_s)
