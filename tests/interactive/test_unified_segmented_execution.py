from __future__ import annotations

import time

import pytest

from sat_sim.bsk_engine.unified_native import (
    ADCS_UNIFIED_CAPABILITY_ID,
    WHOLE_UNIFIED_CAPABILITY_ID,
    UnifiedNativeRuntime,
    UnifiedRuntimeConfig,
)
from sat_sim.interactive.unified_runtime import UnifiedPersistentRuntime
from sat_sim.interactive.manager import InteractiveSession
from sat_sim.interactive.models import InteractiveSessionSpec, SessionState


def _run(stops: tuple[float, ...]):
    return UnifiedNativeRuntime(UnifiedRuntimeConfig(
        capability_id=ADCS_UNIFIED_CAPABILITY_ID,
        duration_s=2.0,
        step_s=0.2,
        sample_s=0.5,
        adcs_only=True,
        values={"initial_pointing_error_deg": 8.0},
        events=(),
        execution_stops_s=stops,
    )).run()


def test_unified_adcs_presegmented_execution_matches_legacy_single_segment() -> None:
    single = _run(())
    segmented = _run((0.5, 1.0, 1.5, 2.0))
    assert single.summary["execution_segment_count"] == 1
    assert segmented.summary["execution_segment_count"] == 4
    assert [row["time_s"] for row in segmented.trace_rows] == [row["time_s"] for row in single.trace_rows]
    for left, right in zip(segmented.trace_rows, single.trace_rows, strict=True):
        assert left.keys() == right.keys()
        for key in left:
            if isinstance(left[key], float):
                assert left[key] == pytest.approx(right[key], rel=1e-11, abs=1e-11)
            else:
                assert left[key] == right[key]


def test_unified_segment_stops_fail_closed_when_non_monotonic_or_out_of_range() -> None:
    with pytest.raises(ValueError, match="increase strictly"):
        _run((1.0, 0.5))
    with pytest.raises(ValueError, match="within"):
        _run((3.0,))


def test_unified_whole_spacecraft_segmented_execution_matches_single_segment() -> None:
    def run(stops: tuple[float, ...]):
        return UnifiedNativeRuntime(UnifiedRuntimeConfig(
            capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
            duration_s=2.0,
            step_s=0.2,
            sample_s=0.5,
            adcs_only=False,
            values={},
            events=(),
            execution_stops_s=stops,
        )).run()

    single = run(())
    segmented = run((0.5, 1.0, 1.5, 2.0))
    assert single.summary["overall_status"] == segmented.summary["overall_status"]
    assert [row["time_s"] for row in segmented.trace_rows] == [row["time_s"] for row in single.trace_rows]
    for left, right in zip(segmented.trace_rows, single.trace_rows, strict=True):
        for key in left:
            if isinstance(left[key], float):
                assert left[key] == pytest.approx(right[key], rel=1e-10, abs=1e-10)
            else:
                assert left[key] == right[key]


def test_default_whole_spacecraft_context_remains_persistent_between_external_advances() -> None:
    config = UnifiedRuntimeConfig(
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=2.0,
        step_s=0.2,
        sample_s=0.5,
        adcs_only=False,
        values={},
        events=(),
    )
    runtime = UnifiedPersistentRuntime(config)
    runtime.prepare()
    worker_ident = runtime.worker_ident
    deltas = [runtime.advance_to(stop) for stop in (0.5, 1.0, 1.5, 2.0)]
    rows = tuple(row for delta in deltas for row in delta)
    assert runtime.worker_ident == worker_ident
    assert [row["time_s"] for row in rows] == sorted({row["time_s"] for row in rows})
    metadata = runtime.finalize()
    assert metadata["persistent_instance"] is True
    assert metadata["summary"]["execution_segment_count"] == 4

    single = UnifiedNativeRuntime(config).run()
    assert [row["time_s"] for row in rows] == [row["time_s"] for row in single.trace_rows]
    for left, right in zip(rows, single.trace_rows, strict=True):
        for key in left:
            if isinstance(left[key], float):
                assert left[key] == pytest.approx(right[key], rel=1e-10, abs=1e-10)
            else:
                assert left[key] == right[key]


def test_unified_persistent_runtime_abort_releases_waiting_worker() -> None:
    runtime = UnifiedPersistentRuntime(UnifiedRuntimeConfig(
        capability_id=ADCS_UNIFIED_CAPABILITY_ID, duration_s=2.0, step_s=0.2,
        sample_s=0.5, adcs_only=True, values={}, events=(),
    ))
    runtime.prepare()
    runtime.abort()
    assert runtime._thread is not None
    assert runtime._thread.is_alive() is False


def test_default_whole_spacecraft_runtime_supports_session_pause_and_step() -> None:
    config = UnifiedRuntimeConfig(
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID, duration_s=2.0, step_s=0.1,
        sample_s=0.5, adcs_only=False, values={}, events=(),
    )
    runtime = UnifiedPersistentRuntime(config)
    session = InteractiveSession(
        InteractiveSessionSpec(
            session_id="whole-unified-session", capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
            task_spec={}, quantum_s=0.5, rate=10.0, paced=True, max_sim_time_s=2.0,
        ),
        runtime,
        end_time_s=2.0,
    )
    session.prepare()
    session.start()
    session.pause()
    assert session.state is SessionState.PAUSED
    paused_at = session.current_time_s
    time.sleep(0.15)
    assert session.current_time_s == pytest.approx(paused_at)
    session.set_rate(5.0)
    assert session.snapshot().rate == 5.0
    session.step()
    assert session.current_time_s == pytest.approx(paused_at + 0.5)
    assert session.state is SessionState.PAUSED
    stepped_at = session.current_time_s
    session.resume()
    session.pause()
    assert session.state is SessionState.PAUSED
    assert session.current_time_s > stepped_at
    session.stop()
    assert session.state is SessionState.COMPLETED
