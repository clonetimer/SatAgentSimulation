"""Regression tests for the opt-in continuous whole-spacecraft session mode."""
from __future__ import annotations

import time

import pytest

from sat_sim.interactive.manager import InteractiveSession
from sat_sim.interactive.models import ActorRole, CommandState, InteractiveSessionSpec, SessionState, Telecommand
from sat_sim.interactive.runtime_factory import (
    build_persistent_runtime,
    WHOLE_UNIFIED_CAPABILITY_ID,
)


class _RecordingContinuousRuntime:
    def __init__(self) -> None:
        self.current_time_s = 0.0
        self.prepared = False
        self.closed = False
        self.commands: list[Telecommand] = []

    def prepare(self) -> None:
        self.prepared = True

    def advance_to(self, stop_time_s: float) -> tuple[dict, ...]:
        assert self.prepared and not self.closed
        assert stop_time_s > self.current_time_s
        self.current_time_s = stop_time_s
        return ({"time_s": stop_time_s, "orbit.radius_m": 7000000.0, "received_commands": len(self.commands)},)

    def apply_command(self, command) -> None:
        assert self.prepared and not self.closed
        self.commands.append(command)

    def read_delta(self) -> tuple[dict, ...]:
        return ()

    def finalize(self) -> dict:
        self.closed = True
        return {"final_sim_time_s": self.current_time_s}

    def abort(self) -> None:
        self.closed = True


def _spec(continuous: bool = True) -> InteractiveSessionSpec:
    return InteractiveSessionSpec(
        session_id="continuous-regression",
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        task_spec={},
        continuous=continuous,
        quantum_s=0.1,
        paced=True,
        rate=1.0,
        max_sim_time_s=0.2,
        max_wall_time_s=0.2,
        idle_timeout_s=0.1,
    )


def test_continuous_session_crosses_bounded_deadlines_and_only_stops_explicitly() -> None:
    runtime = _RecordingContinuousRuntime()
    session = InteractiveSession(_spec(), runtime, end_time_s=None)
    try:
        session.prepare()
        command = Telecommand(
            command_id="continuous-command",
            session_id=session.spec.session_id,
            session_revision=session.revision,
            operation="adcs.target.set",
            target="subsystem.adcs",
            parameters={"sigma_rn": [0.2, 0.0, 0.0]},
            actor_id="operator",
            actor_role=ActorRole.OPERATOR,
            execute_at_sim_time_s=0.3,
        )
        assert session.submit_command(command).state is CommandState.QUEUED
        session.start()
        # Running across both the former sim-time and wall-time deadlines must not auto-finalize.
        deadline = time.monotonic() + 3.0
        while session.current_time_s < 0.4 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert session.current_time_s >= 0.4
        assert len(runtime.commands) == 1
        assert session.command_acks("continuous-command")[-1].state is CommandState.ACKED
        session.pause()
        paused_at = session.current_time_s
        time.sleep(0.25)  # Greater than idle_timeout_s, without heartbeat.
        assert session.state is SessionState.PAUSED
        assert session.current_time_s == paused_at
        session.step()
        assert session.current_time_s > paused_at
        assert session.state is SessionState.PAUSED
        frames = session.telemetry.read_after("runtime.trace").frames
        assert frames
        assert frames[-1].values["received_commands"] == 1
        assert frames[-1].sim_time_s > session.spec.max_sim_time_s
        summary = session.stop()
        assert summary["final_sim_time_s"] > 0.4
        assert session.state is SessionState.COMPLETED
        assert runtime.closed
    finally:
        if session.state not in {SessionState.COMPLETED, SessionState.ABORTED, SessionState.FAILED}:
            session.abort()
        session.wait(2.0)


def test_bounded_session_is_not_silently_converted_to_continuous() -> None:
    runtime = _RecordingContinuousRuntime()
    session = InteractiveSession(_spec(continuous=False), runtime, end_time_s=0.2)
    session.prepare()
    session.start()
    assert session.wait(2.0)
    assert session.state is SessionState.COMPLETED
    assert session.current_time_s == pytest.approx(0.2)


def test_continuous_factory_is_fail_closed_for_non_whole_spacecraft() -> None:
    unsupported = _spec().model_copy(update={"capability_id": "subsystem.eps.unified_native.v1"})
    with pytest.raises(ValueError, match="CONTINUOUS_MODE_REQUIRES_WHOLE_UNIFIED_RUNTIME"):
        build_persistent_runtime(unsupported)


def test_continuous_factory_reports_no_session_deadline() -> None:
    runtime, end_time = build_persistent_runtime(_spec())
    assert end_time is None
    assert runtime.config.interactive_unbounded is True
    assert runtime.config.duration_s == pytest.approx(0.2)


def test_native_basilisk_continues_past_initial_setup_duration() -> None:
    pytest.importorskip("Basilisk")
    from sat_sim.bsk_engine.unified_native import UnifiedRuntimeConfig
    from sat_sim.interactive.unified_runtime import UnifiedPersistentRuntime

    config = UnifiedRuntimeConfig(
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=1.0, step_s=0.2, sample_s=0.2,
        adcs_only=False, values={}, events=(),
        interactive_unbounded=True,
    )
    runtime = UnifiedPersistentRuntime(config, continuous=True)
    runtime.prepare()
    try:
        before = runtime.advance_to(0.8)
        assert before
        runtime.apply_command(Telecommand(
            command_id="native-target-past-horizon",
            session_id="native-continuous",
            session_revision=0,
            operation="adcs.target.set",
            target="subsystem.adcs",
            parameters={"sigma_rn": [0.5, 0.0, 0.0]},
            actor_id="operator",
            actor_role=ActorRole.OPERATOR,
            execute_at_sim_time_s=0.8,
        ))
        after = runtime.advance_to(1.2)
        assert after
        assert runtime.current_time_s == pytest.approx(1.2)
        assert after[-1]["time_s"] > config.duration_s
        summary = runtime.finalize()
        assert summary["persistent_instance"] is True
        assert summary["summary"]["execution_segment_count"] == 2
        assert len(summary["summary"]["execution_stops_s"]) == 1
        from sat_sim.bsk_engine.unified_native import UnifiedNativeRuntime
        baseline = UnifiedNativeRuntime(UnifiedRuntimeConfig(
            capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
            duration_s=1.2, step_s=0.2, sample_s=0.2,
            adcs_only=False, values={}, events=(),
        )).run()
        assert after[-1]["adcs.pointing_error_deg"] != pytest.approx(
            baseline.trace_rows[-1]["adcs.pointing_error_deg"], abs=1e-4,
        )
    finally:
        if runtime._thread is not None and runtime._thread.is_alive():
            runtime.abort()
