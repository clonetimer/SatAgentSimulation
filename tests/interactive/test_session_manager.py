from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from sat_sim.interactive.manager import InteractiveFeatureDisabled, InteractiveSession, InteractiveSessionManager
from sat_sim.interactive.models import InteractiveSessionSpec, SessionState


class RecordingRuntime:
    def __init__(self) -> None:
        self._time = 0.0
        self.owner_threads: set[int] = set()
        self.closed = False

    @property
    def current_time_s(self) -> float:
        return self._time

    def _record(self) -> None:
        self.owner_threads.add(threading.get_ident())

    def prepare(self) -> None:
        self._record()

    def advance_to(self, stop_time_s: float) -> tuple[dict[str, Any], ...]:
        self._record()
        self._time = stop_time_s
        return ({"time_s": stop_time_s, "value": stop_time_s},)

    def apply_command(self, command) -> None:
        self._record()

    def read_delta(self) -> tuple[dict[str, Any], ...]:
        return ()

    def finalize(self) -> dict[str, Any]:
        self._record()
        self.closed = True
        return {"final_sim_time_s": self._time}

    def abort(self) -> None:
        self._record()
        self.closed = True


class FailingRuntime(RecordingRuntime):
    def prepare(self) -> None:
        self._record()
        raise RuntimeError("intentional prepare failure")


class SlowRuntime(RecordingRuntime):
    def advance_to(self, stop_time_s: float) -> tuple[dict[str, Any], ...]:
        time.sleep(0.03)
        return super().advance_to(stop_time_s)


def _session(runtime: RecordingRuntime, *, paced: bool = False, end_time_s: float = 10.0) -> InteractiveSession:
    spec = InteractiveSessionSpec(
        session_id="session-manager-test", capability_id="whole_spacecraft.bsksim_foundation.v1",
        task_spec={}, quantum_s=0.1, rate=1.0, paced=paced, max_sim_time_s=end_time_s,
    )
    return InteractiveSession(spec, runtime, end_time_s=end_time_s)


def test_prepare_step_and_stop_use_one_physical_owner_thread() -> None:
    runtime = RecordingRuntime()
    session = _session(runtime)
    session.prepare()
    session.start()
    # Unpaced execution may reach the end quickly; use a separate paused session for exact stepping.
    while session.state not in {SessionState.COMPLETED, SessionState.FAILED}:
        time.sleep(0.001)
    assert session.state is SessionState.COMPLETED
    assert runtime.owner_threads == {session.worker_ident}
    assert session.telemetry.read_after("runtime.trace").latest_sequence is not None

    stepped_runtime = RecordingRuntime()
    stepped = _session(stepped_runtime, paced=True)
    stepped.prepare()
    stepped.start()
    # A paced loop leaves a quantum boundary for the pause request.
    # Here pause can legally land after one or more completed quanta.
    if stepped.state is SessionState.RUNNING:
        stepped.pause()
    if stepped.state is SessionState.PAUSED:
        before = stepped.current_time_s
        rows = stepped.step(2)
        assert stepped.current_time_s == pytest.approx(before + 0.2)
        assert stepped.state is SessionState.PAUSED
        assert len(rows) == 1
        stepped.stop()
        assert stepped.state is SessionState.COMPLETED
    assert stepped_runtime.owner_threads == {stepped.worker_ident}


def test_invalid_control_does_not_kill_ready_session() -> None:
    runtime = RecordingRuntime()
    session = _session(runtime)
    session.prepare()
    with pytest.raises(ValueError, match="step requires PAUSED"):
        session.step()
    assert session.state is SessionState.READY
    session.abort()
    assert session.state is SessionState.ABORTED


def test_manager_is_feature_gated_bounded_and_exposes_immutable_snapshots() -> None:
    spec = InteractiveSessionSpec(
        session_id="managed-session", capability_id="whole_spacecraft.bsksim_foundation.v1",
        task_spec={}, quantum_s=0.1, paced=False, max_sim_time_s=1.0,
    )
    with pytest.raises(InteractiveFeatureDisabled):
        InteractiveSessionManager(enabled=False).create(spec, RecordingRuntime(), end_time_s=1.0)
    manager = InteractiveSessionManager(enabled=True, max_sessions=1)
    session = manager.create(spec, RecordingRuntime(), end_time_s=1.0)
    assert manager.get(spec.session_id) is session
    assert manager.snapshots()[0].state is SessionState.CREATED
    duplicate = spec.model_copy(update={"session_id": "second-session"})
    with pytest.raises(RuntimeError, match="capacity exhausted"):
        manager.create(duplicate, RecordingRuntime(), end_time_s=1.0)
    session.abort()
    replacement = manager.create(duplicate, RecordingRuntime(), end_time_s=1.0)
    replacement.abort()


def test_concurrent_controls_receive_terminal_results_without_deadlock() -> None:
    runtime = RecordingRuntime()
    spec = InteractiveSessionSpec(
        session_id="concurrent-controls", capability_id="whole_spacecraft.bsksim_foundation.v1",
        task_spec={}, quantum_s=0.01, rate=1.0, paced=True, max_sim_time_s=100.0,
        control_queue_capacity=32,
    )
    session = InteractiveSession(spec, runtime, end_time_s=100.0)
    session.prepare()
    session.start()
    barrier = threading.Barrier(11)
    outcomes: list[tuple[str, str]] = []
    outcomes_lock = threading.Lock()

    actions = [
        ("pause", session.pause),
        ("resume", session.resume),
        ("step", session.step),
        ("rate", lambda: session.set_rate(2.0)),
        ("pause", session.pause),
        ("resume", session.resume),
        ("step", session.step),
        ("rate", lambda: session.set_rate(0.5)),
        ("pause", session.pause),
        ("stop", session.stop),
    ]

    def invoke(name, action) -> None:
        barrier.wait()
        try:
            action()
            result = "OK"
        except BaseException as exc:
            result = f"{type(exc).__name__}:{exc}"
        with outcomes_lock:
            outcomes.append((name, result))

    threads = [threading.Thread(target=invoke, args=item) for item in actions]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(3.0)
    assert all(not thread.is_alive() for thread in threads)
    assert len(outcomes) == len(actions)
    assert not any("TimeoutError" in result for _, result in outcomes)
    assert session.wait(1.0)
    assert session.state is SessionState.COMPLETED
    assert runtime.owner_threads == {session.worker_ident}


def test_failed_session_is_isolated_from_another_session() -> None:
    manager = InteractiveSessionManager(enabled=True, max_sessions=2)
    failed = manager.create(
        InteractiveSessionSpec(session_id="failed-session", capability_id="capability", task_spec={}, paced=False),
        FailingRuntime(),
        end_time_s=1.0,
    )
    healthy = manager.create(
        InteractiveSessionSpec(session_id="healthy-session", capability_id="capability", task_spec={}, paced=False),
        RecordingRuntime(),
        end_time_s=1.0,
    )
    with pytest.raises(RuntimeError, match="intentional prepare failure"):
        failed.prepare()
    assert failed.state is SessionState.FAILED
    healthy.prepare()
    healthy.start()
    assert healthy.wait(1.0)
    assert healthy.state is SessionState.COMPLETED


def test_compute_overrun_is_explicitly_reported_as_degraded_timing() -> None:
    spec = InteractiveSessionSpec(
        session_id="timing-overrun", capability_id="capability", task_spec={},
        quantum_s=0.01, rate=1.0, paced=True, max_sim_time_s=0.03,
        drift_tolerance_s=0.005,
    )
    session = InteractiveSession(spec, SlowRuntime(), end_time_s=0.03)
    session.prepare()
    session.start()
    assert session.wait(1.0)
    snapshot = session.snapshot()
    assert snapshot.state is SessionState.COMPLETED
    assert snapshot.pace_observation_count == 3
    assert snapshot.max_abs_drift_s > spec.drift_tolerance_s
    assert snapshot.timing_status == "DEGRADED"


def test_session_publishes_field_allowlisted_multi_rate_streams() -> None:
    task_spec = {
        "outputs": {
            "telemetry_streams": [
                {"stream_id": "fast", "sample_s": 0.5, "fields": ["value"]},
                {"stream_id": "housekeeping", "sample_s": 1.0, "fields": ["value"]},
                {"stream_id": "invalid", "sample_s": 1.0, "fields": ["unknown.internal"]},
            ]
        }
    }
    spec = InteractiveSessionSpec(
        session_id="multi-rate-session", capability_id="capability", task_spec=task_spec,
        quantum_s=0.5, paced=False, max_sim_time_s=2.0,
    )
    session = InteractiveSession(spec, RecordingRuntime(), end_time_s=2.0)
    session.prepare()
    session.start()
    assert session.wait(1.0)
    fast = session.telemetry.read_after("fast").frames
    housekeeping = session.telemetry.read_after("housekeeping").frames
    invalid = session.telemetry.read_after("invalid").frames
    assert [frame.sim_time_s for frame in fast] == [0.5, 1.0, 1.5, 2.0]
    assert [frame.sim_time_s for frame in housekeeping] == [1.0, 2.0]
    assert all(set(frame.values) == {"value"} for frame in (*fast, *housekeeping))
    assert all(frame.quality == "INVALID" and frame.values == {} for frame in invalid)
