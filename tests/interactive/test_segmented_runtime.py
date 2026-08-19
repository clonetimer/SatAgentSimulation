from __future__ import annotations

import pytest

from sat_sim.bsk_engine.types import BSKEventSpec, BSKScenarioConfig
from sat_sim.interactive.basilisk_runtime import FoundationBasiliskRuntime
from sat_sim.interactive.manager import InteractiveSession
from sat_sim.interactive.models import InteractiveSessionSpec, SessionState
from sat_sim.interactive.runtime import PersistentSimulationRuntime


def _config() -> BSKScenarioConfig:
    return BSKScenarioConfig(
        scenario_id="interactive-segment-probe",
        capability_id="whole_spacecraft.bsksim_foundation.v1",
        duration_s=4.0,
        step_s=0.1,
        sample_s=1.0,
        mode_request="inertialPoint",
        parameters={"initial_pointing_error_deg": 5.0, "orbit_rate_rad_s": 0.0011},
        events=(BSKEventSpec(event_id="marker", category="fault", effect="marker", target="spacecraft", start_s=2.0),),
    )


def _run(stops: tuple[float, ...]) -> tuple[FoundationBasiliskRuntime, tuple[dict, ...], dict]:
    runtime = FoundationBasiliskRuntime(_config())
    assert isinstance(runtime, PersistentSimulationRuntime)
    runtime.prepare()
    instance_id = id(runtime._sim)
    rows = tuple(row for stop in stops for row in runtime.advance_to(stop))
    assert id(runtime._sim) == instance_id
    metadata = runtime.finalize()
    return runtime, rows, metadata


def test_same_basilisk_instance_advances_in_segments_without_duplicate_samples() -> None:
    runtime, rows, metadata = _run((1.0, 2.0, 3.0, 4.0))
    times = [row["time_s"] for row in rows]
    assert times == sorted(set(times))
    assert times[0] == pytest.approx(0.0)
    assert times[-1] == pytest.approx(4.0)
    assert runtime.current_time_s == pytest.approx(4.0)
    assert metadata["persistent_instance"] is True
    assert any(row["label.fault_active"] is True for row in rows)


def test_segmented_and_single_advance_results_are_equivalent() -> None:
    _, segmented, _ = _run((1.0, 2.0, 3.0, 4.0))
    _, single, _ = _run((4.0,))
    assert [row["time_s"] for row in segmented] == [row["time_s"] for row in single]
    assert len(segmented) == len(single)
    for segmented_row, single_row in zip(segmented, single, strict=True):
        assert segmented_row.keys() == single_row.keys()
        for key, expected in single_row.items():
            if isinstance(expected, float):
                assert segmented_row[key] == pytest.approx(expected, rel=1e-11, abs=1e-11)
            else:
                assert segmented_row[key] == expected


def test_runtime_rejects_non_monotonic_or_out_of_bounds_advance_and_releases_context() -> None:
    runtime = FoundationBasiliskRuntime(_config())
    runtime.prepare()
    runtime.advance_to(1.0)
    with pytest.raises(ValueError, match="increase monotonically"):
        runtime.advance_to(1.0)
    with pytest.raises(ValueError, match="exceeds configured duration"):
        runtime.advance_to(5.0)
    runtime.abort()
    assert runtime._sim is None
    with pytest.raises(RuntimeError, match="not active"):
        runtime.advance_to(2.0)


def test_real_basilisk_session_pauses_steps_and_finalizes_same_context() -> None:
    runtime = FoundationBasiliskRuntime(_config())
    spec = InteractiveSessionSpec(
        session_id="real-basilisk-session", capability_id=_config().capability_id,
        task_spec={}, quantum_s=0.5, rate=10.0, paced=True, max_sim_time_s=4.0,
    )
    session = InteractiveSession(spec, runtime, end_time_s=4.0)
    session.prepare()
    session.start()
    session.pause()
    assert session.state is SessionState.PAUSED
    paused_at = session.current_time_s
    rows = session.step()
    assert session.state is SessionState.PAUSED
    assert session.current_time_s == pytest.approx(paused_at + 0.5)
    assert rows
    metadata = session.stop()
    assert session.state is SessionState.COMPLETED
    assert metadata["persistent_instance"] is True
