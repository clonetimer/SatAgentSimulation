"""Single-owner session worker for pause, resume, step, rate, and stop."""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import interactive_enabled
from .clock import PaceObservation, SoftRealtimeClock
from .command_catalog import CommandCatalog, CommandValidationError
from .command_mailbox import DeterministicCommandMailbox
from .models import CommandAck, CommandState, InteractiveSessionSpec, SessionEvent, SessionSnapshot, SessionState, Telecommand
from .runtime import PersistentSimulationRuntime
from .state_machine import InvalidSessionTransition, SessionStateMachine
from .telemetry_bus import TelemetryBus
from .workspace import SessionWorkspace, WorkspaceInspection


@dataclass
class _ControlRequest:
    action: str
    value: Any = None
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None


class InteractiveSession:
    """Own a runtime on one worker thread; callers only submit controls."""

    def __init__(
        self,
        spec: InteractiveSessionSpec,
        runtime: PersistentSimulationRuntime,
        *,
        end_time_s: float,
        workspace: SessionWorkspace | None = None,
        command_catalog: CommandCatalog | None = None,
    ) -> None:
        self.spec = spec
        self.runtime = runtime
        self.end_time_s = min(float(end_time_s), spec.max_sim_time_s)
        self.telemetry = TelemetryBus(spec.session_id, spec.telemetry_buffer_frames)
        self._state = SessionStateMachine()
        self._requests: queue.Queue[_ControlRequest] = queue.Queue(maxsize=spec.control_queue_capacity)
        self._accept_lock = threading.Lock()
        self._terminated = threading.Event()
        self._clock = SoftRealtimeClock(spec.rate)
        self._workspace = workspace
        self._command_catalog = command_catalog or CommandCatalog.default()
        self._command_mailbox = DeterministicCommandMailbox(spec.command_queue_capacity)
        self._acks: list[CommandAck] = []
        self._ack_sequence = 0
        self._ack_lock = threading.Lock()
        self._events: list[SessionEvent] = []
        self._event_lock = threading.Lock()
        self._created_monotonic = time.monotonic()
        self._last_activity_monotonic = self._created_monotonic
        self._last_heartbeat_monotonic = self._created_monotonic
        self._pace_observation_count = 0
        self._pace_observations: list[PaceObservation] = []
        self._pace_lock = threading.Lock()
        self._last_drift_s = 0.0
        self._max_abs_drift_s = 0.0
        self._runtime_summary: dict[str, Any] = {}
        self._terminal_error: str | None = None
        outputs = spec.task_spec.get("outputs") if isinstance(spec.task_spec, dict) else None
        raw_streams = outputs.get("telemetry_streams", ()) if isinstance(outputs, dict) else ()
        self._telemetry_stream_specs: tuple[tuple[str, float, tuple[str, ...]], ...] = tuple(
            (
                str(item["stream_id"]),
                float(item["sample_s"]),
                tuple(str(field) for field in item["fields"]),
            )
            for item in raw_streams
            if isinstance(item, dict) and item.get("stream_id") and float(item.get("sample_s", 0.0)) > 0.0 and item.get("fields")
        )
        if self._workspace is not None:
            self._workspace.append_state(SessionState.CREATED, 0, 0.0, "SESSION_CREATED")
        self._thread = threading.Thread(target=self._worker, name=f"sat-interactive-{spec.session_id}", daemon=True)
        self._thread.start()

    @property
    def state(self) -> SessionState:
        return self._state.state

    @property
    def revision(self) -> int:
        return self._state.revision

    @property
    def current_time_s(self) -> float:
        return self.runtime.current_time_s

    @property
    def worker_ident(self) -> int | None:
        return self._thread.ident

    def snapshot(self) -> SessionSnapshot:
        return SessionSnapshot(
            session_id=self.spec.session_id,
            capability_id=self.spec.capability_id,
            state=self.state,
            revision=self.revision,
            sim_time_s=self.current_time_s,
            rate=self._clock.rate,
            paced=self.spec.paced,
            worker_alive=self._thread.is_alive(),
            pace_observation_count=self._pace_observation_count,
            last_drift_s=self._last_drift_s,
            max_abs_drift_s=self._max_abs_drift_s,
            timing_status="DEGRADED" if self._max_abs_drift_s > self.spec.drift_tolerance_s else "GOOD",
        )

    def pace_observations(self, after_index: int = 0) -> tuple[PaceObservation, ...]:
        """Return immutable pacing samples without exposing worker-owned state."""

        if after_index < 0:
            raise ValueError("after_index must be non-negative")
        with self._pace_lock:
            return tuple(self._pace_observations[after_index:])

    @property
    def terminal_error(self) -> str | None:
        """Expose the stable worker failure text for diagnostics and evidence."""

        return self._terminal_error

    def _request(self, action: str, value: Any = None, timeout_s: float = 30.0) -> Any:
        request = _ControlRequest(action, value)
        with self._accept_lock:
            if self._terminated.is_set():
                raise RuntimeError("SESSION_TERMINAL")
            try:
                self._requests.put_nowait(request)
            except queue.Full as exc:
                raise RuntimeError("SESSION_CONTROL_QUEUE_FULL") from exc
        if not request.done.wait(timeout_s):
            raise TimeoutError(f"interactive control timed out: {action}")
        if request.error is not None:
            raise request.error
        return request.result

    def prepare(self) -> None:
        self._request("prepare")

    def start(self) -> None:
        self._request("start")

    def pause(self) -> None:
        self._request("pause")

    def resume(self) -> None:
        self._request("resume")

    def step(self, quanta: int = 1) -> tuple[dict[str, Any], ...]:
        if not isinstance(quanta, int) or quanta <= 0:
            raise ValueError("quanta must be a positive integer")
        return self._request("step", quanta)

    def set_rate(self, rate: float) -> None:
        self._request("rate", rate)

    def stop(self) -> dict[str, Any]:
        return self._request("stop")

    def abort(self) -> None:
        self._request("abort")

    def heartbeat(self) -> SessionSnapshot:
        return self._request("heartbeat")

    def _emit_ack(
        self,
        command: Telecommand,
        state: CommandState,
        reason_code: str = "OK",
        detail: str = "",
        sim_time_s: float | None = None,
    ) -> CommandAck:
        with self._ack_lock:
            ack = CommandAck(
                command_id=command.command_id,
                session_id=command.session_id,
                state=state,
                reason_code=reason_code,
                detail=detail,
                sim_time_s=sim_time_s,
                sequence=self._ack_sequence,
            )
            self._ack_sequence += 1
            self._acks.append(ack)
            if self._workspace is not None:
                self._workspace.append_command_ack(command, ack)
            return ack

    def command_acks(self, command_id: str | None = None) -> tuple[CommandAck, ...]:
        with self._ack_lock:
            rows = tuple(self._acks)
        return tuple(row for row in rows if command_id is None or row.command_id == command_id)

    def session_events(self, after_sequence: int = -1) -> tuple[SessionEvent, ...]:
        with self._event_lock:
            return tuple(event for event in self._events if event.sequence > after_sequence)

    @property
    def allowed_telemetry_streams(self) -> tuple[str, ...]:
        return tuple(item[0] for item in self._telemetry_stream_specs)

    def submit_command(self, command: Telecommand) -> CommandAck:
        if not isinstance(command, Telecommand):
            raise TypeError("only a confirmed Telecommand can be submitted")
        self._emit_ack(command, CommandState.RECEIVED)
        if command.session_id != self.spec.session_id:
            return self._emit_ack(command, CommandState.REJECTED, "SESSION_MISMATCH")
        if self.state not in {SessionState.READY, SessionState.RUNNING, SessionState.PAUSED}:
            return self._emit_ack(command, CommandState.REJECTED, "SESSION_NOT_COMMANDABLE")
        try:
            self._command_catalog.validate(command)
        except CommandValidationError as exc:
            return self._emit_ack(command, CommandState.REJECTED, exc.reason_code, str(exc))
        self._emit_ack(command, CommandState.VALIDATED)
        receipt = self._command_mailbox.submit(command, session_revision=self.revision)
        if receipt.duplicate:
            previous = self.command_acks(command.command_id)
            previous_state = next((ack.state for ack in reversed(previous[:-1]) if ack.state not in {CommandState.RECEIVED, CommandState.VALIDATED}), CommandState.QUEUED)
            return self._emit_ack(command, previous_state, receipt.reason_code)
        if not receipt.accepted:
            state = CommandState.EXPIRED if receipt.reason_code == "COMMAND_EXPIRED" else CommandState.REJECTED
            return self._emit_ack(command, state, receipt.reason_code)
        return self._emit_ack(command, CommandState.QUEUED, receipt.reason_code)

    def wait(self, timeout_s: float | None = None) -> bool:
        self._thread.join(timeout_s)
        return not self._thread.is_alive()

    def _transition(self, target: SessionState, reason_code: str = "OK") -> None:
        event = self._state.transition(target, reason_code)
        with self._event_lock:
            self._events.append(SessionEvent(
                session_id=self.spec.session_id,
                sequence=len(self._events),
                previous_state=event.previous,
                state=event.current,
                revision=event.revision,
                sim_time_s=self.current_time_s,
                reason_code=event.reason_code,
                wall_time=event.wall_time_iso,
            ))
        if self._workspace is not None:
            self._workspace.append_state(event.current, event.revision, self.current_time_s, reason_code)

    def _write_heartbeat(self, reason_code: str = "HEARTBEAT") -> None:
        now = time.monotonic()
        if self._workspace is not None:
            self._workspace.append_state(self.state, self.revision, self.current_time_s, reason_code)
        self._last_heartbeat_monotonic = now

    def _advance(self, quanta: int = 1) -> tuple[dict[str, Any], ...]:
        # Basilisk stop times are integer nanoseconds.  Repeated binary-float
        # addition otherwise eventually lands one nanosecond before a recorder
        # boundary (observed at 1754.4 s for a 0.1 s quantum), making a valid
        # physical advance look like a no-progress segment.
        target_ns = round((self.current_time_s + self.spec.quantum_s * quanta) * 1.0e9)
        end_ns = round(self.end_time_s * 1.0e9)
        target = min(target_ns, end_ns) / 1_000_000_000
        if target <= self.current_time_s:
            return ()
        commands, expired = self._command_mailbox.pop_due_partitioned(target)
        for command in expired:
            self._emit_ack(command, CommandState.EXPIRED, "COMMAND_EXPIRED", sim_time_s=self.current_time_s)
        executing: list[Telecommand] = []
        for command in commands:
            self._emit_ack(command, CommandState.EXECUTING, sim_time_s=self.current_time_s)
            try:
                self.runtime.apply_command(command)
                executing.append(command)
            except BaseException as exc:
                self._emit_ack(command, CommandState.FAILED, "COMMAND_APPLY_FAILED", f"{type(exc).__name__}: {exc}", self.current_time_s)
        rows = self.runtime.advance_to(target)
        for row in rows:
            sim_time_s = float(row.get("time_s", target))
            frame = self.telemetry.publish("runtime.trace", sim_time_s, dict(row))
            if self._workspace is not None:
                self._workspace.append_telemetry(frame)
            for stream_id, sample_s, fields in self._telemetry_stream_specs:
                sample_index = round(sim_time_s / sample_s)
                if abs(sim_time_s - sample_index * sample_s) <= max(1e-9, sample_s * 1e-9):
                    values = {field: row[field] for field in fields if field in row}
                    quality = "GOOD" if len(values) == len(fields) else "INVALID"
                    frame = self.telemetry.publish(stream_id, sim_time_s, values, quality)
                    if self._workspace is not None:
                        self._workspace.append_telemetry(frame)
        for command in executing:
            self._emit_ack(command, CommandState.ACKED, "OK", sim_time_s=target)
        return rows

    def _handle(self, request: _ControlRequest) -> bool:
        action = request.action
        if action == "prepare":
            self._transition(SessionState.PREPARING)
            self.runtime.prepare()
            self._transition(SessionState.READY)
        elif action == "start":
            self._transition(SessionState.RUNNING)
            self._clock.reset(self.current_time_s)
        elif action == "pause":
            self._transition(SessionState.PAUSED)
        elif action == "resume":
            self._transition(SessionState.RUNNING)
            self._clock.reset(self.current_time_s)
        elif action == "step":
            if self.state is not SessionState.PAUSED:
                raise ValueError("step requires PAUSED state")
            request.result = self._advance(request.value)
        elif action == "rate":
            if self.state not in {SessionState.READY, SessionState.RUNNING, SessionState.PAUSED}:
                raise ValueError("rate change requires READY, RUNNING, or PAUSED state")
            self._clock.set_rate(float(request.value), self.current_time_s)
        elif action == "stop":
            self._transition(SessionState.STOPPING)
            self._runtime_summary = self.runtime.finalize()
            request.result = self._runtime_summary
            self._transition(SessionState.COMPLETED)
            return False
        elif action == "abort":
            self.runtime.abort()
            self._transition(SessionState.ABORTED, "ABORT_REQUESTED")
            return False
        elif action == "heartbeat":
            self._write_heartbeat("CLIENT_HEARTBEAT")
            request.result = self.snapshot()
        else:
            raise ValueError(f"unknown control action: {action}")
        return True

    def _automatic_limit_reason(self) -> str | None:
        now = time.monotonic()
        if now - self._created_monotonic >= self.spec.max_wall_time_s:
            return "MAX_WALL_TIME_EXCEEDED"
        if self.state in {SessionState.CREATED, SessionState.READY, SessionState.PAUSED} and now - self._last_activity_monotonic >= self.spec.idle_timeout_s:
            return "IDLE_TIMEOUT"
        return None

    def _abort_for_limit(self, reason_code: str) -> None:
        self.runtime.abort()
        self._transition(SessionState.ABORTED, reason_code)

    def _close_request_queue(self) -> None:
        with self._accept_lock:
            self._terminated.set()
            while True:
                try:
                    request = self._requests.get_nowait()
                except queue.Empty:
                    break
                request.error = RuntimeError("SESSION_TERMINAL")
                request.done.set()

    def _worker(self) -> None:
        keep_running = True
        while keep_running:
            limit_reason = self._automatic_limit_reason()
            if limit_reason is not None:
                try:
                    self._abort_for_limit(limit_reason)
                except BaseException:
                    if self.state not in {SessionState.ABORTED, SessionState.FAILED}:
                        self._transition(SessionState.FAILED, "LIMIT_ABORT_FAILED")
                break
            try:
                request = self._requests.get(
                    timeout=0.0 if self.state is SessionState.RUNNING else min(self.spec.heartbeat_interval_s, self.spec.idle_timeout_s)
                )
            except queue.Empty:
                request = None
            if request is not None:
                self._last_activity_monotonic = time.monotonic()
                try:
                    keep_running = self._handle(request)
                except (InvalidSessionTransition, ValueError) as exc:
                    request.error = exc
                    keep_running = True
                except BaseException as exc:
                    request.error = exc
                    self._terminal_error = f"{type(exc).__name__}: {exc}"
                    if self.state in {SessionState.PREPARING, SessionState.READY, SessionState.RUNNING, SessionState.PAUSED}:
                        self._transition(SessionState.FAILED, "SESSION_WORKER_ERROR")
                    try:
                        self.runtime.abort()
                    except Exception:
                        pass
                    keep_running = False
                finally:
                    request.done.set()
                continue
            if self.state is not SessionState.RUNNING:
                if time.monotonic() - self._last_heartbeat_monotonic >= self.spec.heartbeat_interval_s:
                    self._write_heartbeat()
                continue
            try:
                rows = self._advance()
                if self.spec.paced:
                    observation = self._clock.pace_to(self.current_time_s)
                    with self._pace_lock:
                        self._pace_observations.append(observation)
                        self._pace_observation_count += 1
                        self._last_drift_s = observation.drift_s
                        self._max_abs_drift_s = max(self._max_abs_drift_s, abs(observation.drift_s))
                if time.monotonic() - self._last_heartbeat_monotonic >= self.spec.heartbeat_interval_s:
                    self._write_heartbeat()
                if self.current_time_s >= self.end_time_s:
                    self._transition(SessionState.STOPPING)
                    self._runtime_summary = self.runtime.finalize()
                    self._transition(SessionState.COMPLETED)
                    keep_running = False
                elif not rows:
                    raise RuntimeError("runtime made no progress")
            except BaseException as exc:
                self._terminal_error = f"{type(exc).__name__}: {exc}"
                self._transition(SessionState.FAILED, "SESSION_RUNTIME_ERROR")
                try:
                    self.runtime.abort()
                except Exception:
                    pass
                keep_running = False
        try:
            if self._workspace is not None:
                self._workspace.finalize_telemetry()
                if self.state is SessionState.COMPLETED:
                    from .evidence import seal_interactive_workspace
                    seal_interactive_workspace(self._workspace.path)
        finally:
            self._close_request_queue()


class InteractiveFeatureDisabled(RuntimeError):
    reason_code = "INTERACTIVE_FEATURE_DISABLED"


class InteractiveSessionManager:
    """Bounded in-process registry; constructing it never starts a worker."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        max_sessions: int = 4,
        workspace_root: str | Path | None = None,
    ) -> None:
        if max_sessions <= 0:
            raise ValueError("max_sessions must be positive")
        self.enabled = interactive_enabled() if enabled is None else enabled
        self.max_sessions = max_sessions
        self._sessions: dict[str, InteractiveSession] = {}
        self._lock = threading.Lock()
        self.workspace_root = Path(workspace_root).resolve() if workspace_root is not None else None
        self.interrupted_workspaces: tuple[WorkspaceInspection, ...] = ()
        if self.enabled and self.workspace_root is not None:
            self.workspace_root.mkdir(parents=True, exist_ok=True)
            self.interrupted_workspaces = SessionWorkspace.mark_unfinished_interrupted(self.workspace_root)

    def create(self, spec: InteractiveSessionSpec, runtime: PersistentSimulationRuntime, *, end_time_s: float) -> InteractiveSession:
        if not self.enabled:
            raise InteractiveFeatureDisabled("interactive simulation is disabled")
        with self._lock:
            if spec.session_id in self._sessions:
                raise ValueError("session_id already exists")
            active = sum(1 for session in self._sessions.values() if session.state not in {SessionState.COMPLETED, SessionState.FAILED, SessionState.ABORTED, SessionState.INTERRUPTED})
            if active >= self.max_sessions:
                raise RuntimeError("interactive session capacity exhausted")
            workspace = SessionWorkspace(self.workspace_root, spec) if self.workspace_root is not None else None
            session = InteractiveSession(spec, runtime, end_time_s=end_time_s, workspace=workspace)
            self._sessions[spec.session_id] = session
            return session

    def get(self, session_id: str) -> InteractiveSession:
        with self._lock:
            try:
                return self._sessions[session_id]
            except KeyError as exc:
                raise KeyError(f"unknown interactive session: {session_id}") from exc

    def snapshots(self) -> tuple[SessionSnapshot, ...]:
        with self._lock:
            sessions = tuple(self._sessions.values())
        return tuple(session.snapshot() for session in sessions)

    def reap_terminal(self) -> tuple[str, ...]:
        terminal = {SessionState.COMPLETED, SessionState.FAILED, SessionState.ABORTED, SessionState.INTERRUPTED}
        with self._lock:
            removed = tuple(sorted(session_id for session_id, session in self._sessions.items() if session.state in terminal and not session.snapshot().worker_alive))
            for session_id in removed:
                del self._sessions[session_id]
        return removed

    def shutdown(self, timeout_s: float = 5.0) -> None:
        with self._lock:
            sessions = tuple(self._sessions.values())
        for session in sessions:
            if session.state not in {SessionState.COMPLETED, SessionState.FAILED, SessionState.ABORTED, SessionState.INTERRUPTED}:
                try:
                    session.abort()
                except (RuntimeError, InvalidSessionTransition):
                    pass
            session.wait(timeout_s)
