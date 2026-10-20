"""Short accelerated real-Basilisk soak for the continuous whole-spacecraft session.

This is a regression gate, NOT evidence of 24-hour wall-clock reliability.
"""
from __future__ import annotations

import json
import os
import resource
import time

import pytest

from sat_sim.bsk_engine.unified_native import WHOLE_UNIFIED_CAPABILITY_ID
from sat_sim.interactive.manager import InteractiveSession
from sat_sim.interactive.models import (
    ActorRole, CommandState, InteractiveSessionSpec, SessionState, Telecommand,
)
from sat_sim.interactive.runtime_factory import build_persistent_runtime
from sat_sim.interactive.workspace import SessionWorkspace


def test_accelerated_continuous_180_seconds_tc_tm_beyond_initial_horizon(tmp_path) -> None:
    pytest.importorskip("Basilisk")
    target_s = float(os.environ.get("SAT_SIM_CONTINUOUS_SOAK_TARGET_S", "180"))
    assert 180.0 <= target_s <= 7200.0
    spec = InteractiveSessionSpec(
        session_id="native-continuous-180s-soak",
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        continuous=True,
        quantum_s=0.5,
        paced=False,
        max_sim_time_s=1.0,  # Deliberately far shorter than target.
        max_wall_time_s=1.0,
        idle_timeout_s=0.5,
        task_spec={
            "simulation": {
                "duration_s": 1.0,
                "sample_s": 0.5,
                "solver": {"step_s": 0.2},
            },
            "parameters": {"values": {}},
            "outputs": {"telemetry_streams": [
                {
                    "stream_id": "whole.soak",
                    "sample_s": 1.0,
                    "fields": ["orbit.radius_m", "adcs.pointing_error_deg", "eps.battery_soc"],
                },
            ]},
        },
    )
    runtime, end_time = build_persistent_runtime(spec)
    assert end_time is None
    workspace = SessionWorkspace(tmp_path, spec)
    session = InteractiveSession(spec, runtime, end_time_s=end_time, workspace=workspace)
    initial_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    try:
        session.prepare()
        command = Telecommand(
            command_id="set-target-at-t30",
            session_id=spec.session_id,
            session_revision=session.revision,
            operation="adcs.target.set",
            target="subsystem.adcs",
            parameters={"sigma_rn": [0.25, 0.0, 0.0]},
            actor_id="test-operator",
            actor_role=ActorRole.OPERATOR,
            execute_at_sim_time_s=30.0,
        )
        assert session.submit_command(command).state is CommandState.QUEUED
        session.start()
        deadline = time.monotonic() + max(90.0, target_s / 20.0)
        while session.current_time_s < target_s:
            if session.state is SessionState.FAILED:
                raise AssertionError(f"native continuous session failed: {session.terminal_error}")
            if time.monotonic() > deadline:
                raise AssertionError(f"continuous native simulation failed {target_s}s accelerated deadline")
            time.sleep(0.02)
        session.pause()
        assert session.state is SessionState.PAUSED
        assert session.current_time_s >= target_s
        frames = session.telemetry.read_after("whole.soak").frames
        assert len(frames) >= int(target_s) - 5, len(frames)
        assert all(frame.quality == "GOOD" for frame in frames)
        assert all(left.sim_time_s < right.sim_time_s for left, right in zip(frames, frames[1:]))
        assert all(set(frame.values) == {"orbit.radius_m", "adcs.pointing_error_deg", "eps.battery_soc"} for frame in frames)
        assert session.command_acks(command.command_id)[-1].state is CommandState.ACKED
        assert frames[-1].sim_time_s >= target_s
        summary = session.stop()
        assert summary["summary"]["execution_segment_count"] >= int(2 * target_s)
        assert len(summary["summary"]["execution_stops_s"]) == 1
        assert session.state is SessionState.COMPLETED
        assert session.wait(15.0)
        assert session.snapshot().archive_status == "SEALED"
        manifest = json.loads(workspace.telemetry_manifest_path.read_text(encoding="utf-8"))
        assert manifest["frame_count"] >= len(frames)
        assert manifest["streams"]["whole.soak"] == len(frames)
        assert SessionWorkspace.inspect(workspace.path).valid is True
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux ru_maxrss is KiB, and the allowed extra 1 GiB is deliberately lenient.
        assert peak_rss - initial_rss < 1024 * 1024
        print(
            f"CONTINUOUS_SOAK_OK sim_time_s={session.current_time_s:.1f} "
            f"tm_frames={len(frames)} native_segments={summary['summary']['execution_segment_count']} "
            f"rss_growth_kib={peak_rss-initial_rss}"
        )
    finally:
        if session.state not in {SessionState.COMPLETED, SessionState.ABORTED, SessionState.FAILED}:
            session.abort()
        session.wait(15.0)
