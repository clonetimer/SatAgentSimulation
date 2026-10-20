"""Basilisk native state-manager checkpoint pilot; no claim of restart recovery."""
from __future__ import annotations

import copy
import pytest

from sat_sim.interactive.native_state_pilot import (
    capture_state_subset, restore_state_subset_in_place, validate_state_subset,
)


def _make_native_graph(*, earth_gravity: bool = False):
    pytest.importorskip("Basilisk")
    from Basilisk.simulation import spacecraft, reactionWheelStateEffector
    from Basilisk.utilities import SimulationBaseClass, simIncludeRW, macros

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("p")
    process.addTask(sim.CreateNewTask("t", macros.sec2nano(0.2)))
    sc = spacecraft.Spacecraft()
    sc.hub.mHub = 100.0
    sc.hub.IHubPntBc_B = [[100., 0., 0.], [0., 80., 0.], [0., 0., 60.]]
    sc.hub.r_CN_NInit = [7_000_000.0, 0.0, 0.0]
    sc.hub.v_CN_NInit = [0.0, 7_500.0, 0.0]
    sc.hub.sigma_BNInit = [0.02, 0.0, 0.0]
    sc.hub.omega_BN_BInit = [0.01, 0.02, 0.01]
    if earth_gravity:
        # Create the gravity body before InitializeSimulation. Modifying a
        # dynamics graph after initialization is neither safe nor a restore.
        from Basilisk.utilities import simIncludeGravBody
        gravity_factory = simIncludeGravBody.gravBodyFactory()
        earth = gravity_factory.createEarth()
        earth.isCentralBody = True
        gravity_factory.addBodiesTo(sc)
    sim.AddModelToTask("t", sc, None, 100)
    wheels = reactionWheelStateEffector.ReactionWheelStateEffector()
    wheel_factory = simIncludeRW.rwFactory()
    for i, axis in enumerate(([1, 0, 0], [0, 1, 0], [0, 0, 1])):
        wheel_factory.create(
            "Honeywell_HR16", list(axis), maxMomentum=50.0,
            Omega=100.0 * (i + 1), u_max=0.2, label=f"RW{i + 1}",
        )
    wheel_factory.addToSpacecraft("reaction_wheels", wheels, sc)
    sim.AddModelToTask("t", wheels, None, 99)
    sim.InitializeSimulation()
    return sim, sc


def test_basilisk_in_place_state_subset_roundtrip_matches_uninterrupted_graph() -> None:
    from Basilisk.utilities import macros
    import numpy as np

    baseline, original_sc = _make_native_graph()
    resumed_in_same_graph, candidate_sc = _make_native_graph()
    for sim in (baseline, resumed_in_same_graph):
        sim.ConfigureStopTime(macros.sec2nano(0.8))
        sim.ExecuteSimulation()
    snapshot = capture_state_subset(candidate_sc, expected_rw_count=3)
    validate_state_subset(snapshot, expected_rw_count=3)
    assert not snapshot["resume_supported"]
    assert not snapshot["scheduler_time_restored"]

    native_state = candidate_sc.dynManager.getStateObject("hubPosition")
    original_position = native_state.getState()
    native_state.setState(np.array([
        [original_position[0][0] + 1000.],
        [original_position[1][0]],
        [original_position[2][0]],
    ]))
    assert capture_state_subset(candidate_sc)["vectors"]["hub_position_m"] != snapshot["vectors"]["hub_position_m"]
    restore_state_subset_in_place(candidate_sc, snapshot, expected_rw_count=3)
    assert capture_state_subset(candidate_sc)["vectors"] == snapshot["vectors"]
    for sim in (baseline, resumed_in_same_graph):
        sim.ConfigureStopTime(macros.sec2nano(1.6))
        sim.ExecuteSimulation()
    before = capture_state_subset(original_sc, expected_rw_count=3)
    after = capture_state_subset(candidate_sc, expected_rw_count=3)
    for key in before["vectors"]:
        assert after["vectors"][key] == pytest.approx(
            before["vectors"][key], rel=1e-13, abs=1e-10
        )


def test_basilisk_pilot_rejects_wrong_wheel_count_and_tampered_resume_claim() -> None:
    from Basilisk.utilities import macros

    sim, sc = _make_native_graph()
    sim.ConfigureStopTime(macros.sec2nano(0.8))
    sim.ExecuteSimulation()
    snapshot = capture_state_subset(sc, expected_rw_count=3)
    with pytest.raises(ValueError, match="WHEEL_COUNT_MISMATCH"):
        restore_state_subset_in_place(sc, snapshot, expected_rw_count=4)
    forged = {**snapshot, "resume_supported": True}
    with pytest.raises(ValueError, match="HASH_INVALID"):
        validate_state_subset(forged)
    malformed = copy.deepcopy(snapshot)
    malformed["vectors"]["hub_velocity_m_s"] = [1.0, 2.0]
    with pytest.raises(ValueError, match="HASH_INVALID"):
        validate_state_subset(malformed)
    assert capture_state_subset(sc, expected_rw_count=3)["vectors"] == snapshot["vectors"]



def _fresh_process_rebase_worker(checkpoint_path: str, delta_s: float) -> None:
    """Separate OS process: fresh Basilisk graph and relative-time experiment.

    This demonstrator intentionally does not restore original scheduler time.
    It applies only to the isolated autonomous hub+gravity+RW configuration,
    which has no external absolute-time events, commands or stochastic inputs.
    """
    import json
    import os
    from pathlib import Path
    from Basilisk.utilities import macros
    from sat_sim.interactive.native_state_pilot import (
        capture_state_subset, restore_state_subset_in_place,
    )

    checkpoint = json.loads(Path(checkpoint_path).read_text(encoding="utf-8"))
    if checkpoint.get("profile") != "isolated-autonomous-hub-earth-rw-v1":
        raise ValueError("NATIVE_REBASE_PROFILE_UNSUPPORTED")
    if checkpoint.get("absolute_time_dependencies") is not False:
        raise ValueError("NATIVE_REBASE_ABSOLUTE_TIME_DEPENDENCY")
    snapshot = checkpoint["native_state_subset"]
    sim, spacecraft = _make_native_graph(earth_gravity=True)
    initial_clock = int(sim.TotalSim.CurrentNanos)
    if initial_clock != 0:
        raise RuntimeError("NATIVE_REBASE_FRESH_SCHEDULER_NOT_ZERO")
    restore_state_subset_in_place(spacecraft, snapshot, expected_rw_count=3)
    assert capture_state_subset(spacecraft, expected_rw_count=3)["vectors"] == snapshot["vectors"]
    sim.ConfigureStopTime(macros.sec2nano(delta_s))
    sim.ExecuteSimulation()
    result = capture_state_subset(spacecraft, expected_rw_count=3)
    print("REBASE_RESULT_JSON:" + json.dumps({
        "pid": os.getpid(),
        "fresh_initial_scheduler_ns": initial_clock,
        "fresh_final_scheduler_ns": int(sim.TotalSim.CurrentNanos),
        "native_state_subset": result,
        "original_clock_restored": False,
        "isolated_autonomous_parity_only": True,
        "full_spacecraft_resume_supported": False,
    }, sort_keys=True), flush=True)


def test_separate_process_fresh_graph_state_parity_with_autonomous_earth_gravity_rw(tmp_path) -> None:
    """Prove portable StateData in a *different process*, not whole-system restart."""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    from Basilisk.utilities import macros

    # Native dynamics graph intentionally contains gravity and three wheels,
    # but no time-indexed FSW, power/thermal memory or queued TC.
    sim, sc = _make_native_graph(earth_gravity=True)
    checkpoint_s = 2.0
    end_s = 5.0
    step_s = 0.2
    assert checkpoint_s / step_s == pytest.approx(round(checkpoint_s / step_s))
    assert (end_s - checkpoint_s) / step_s == pytest.approx(round((end_s - checkpoint_s) / step_s))
    sim.ConfigureStopTime(macros.sec2nano(checkpoint_s))
    sim.ExecuteSimulation()
    snapshot = capture_state_subset(sc, expected_rw_count=3)
    path = tmp_path / "native-subset-rebase.json"
    path.write_text(json.dumps({
        "profile": "isolated-autonomous-hub-earth-rw-v1",
        "absolute_time_dependencies": False,
        "checkpoint_sim_time_s": checkpoint_s,
        "step_s": step_s,
        "native_state_subset": snapshot,
    }, sort_keys=True), encoding="utf-8")
    sim.ConfigureStopTime(macros.sec2nano(end_s))
    sim.ExecuteSimulation()
    baseline = capture_state_subset(sc, expected_rw_count=3)
    assert sim.TotalSim.CurrentNanos == macros.sec2nano(end_s)

    worker = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--rebase-worker", str(path), str(end_s - checkpoint_s)],
        capture_output=True, text=True, timeout=45, check=False,
    )
    assert worker.returncode == 0, worker.stdout + "\n" + worker.stderr
    record_line = next(
        x[len("REBASE_RESULT_JSON:"):] for x in worker.stdout.splitlines()
        if x.startswith("REBASE_RESULT_JSON:")
    )
    report = json.loads(record_line)
    assert report["pid"] != os.getpid()
    assert report["fresh_initial_scheduler_ns"] == 0
    assert report["fresh_final_scheduler_ns"] == macros.sec2nano(end_s - checkpoint_s)
    assert report["original_clock_restored"] is False
    assert report["isolated_autonomous_parity_only"] is True
    assert report["full_spacecraft_resume_supported"] is False
    recovered = report["native_state_subset"]
    validate_state_subset(recovered, expected_rw_count=3)
    for key, expected in baseline["vectors"].items():
        assert recovered["vectors"][key] == pytest.approx(expected, rel=1e-12, abs=1e-9)
    # This scenario proves exact local dynamics rebase (all five vectors),
    # *not* the integration history or scheduler state in the whole satellite.


def test_autonomous_rebase_rejects_unapproved_profile_and_absolute_time_inputs(tmp_path) -> None:
    import json
    import subprocess
    import sys
    from pathlib import Path

    graph = {
        "profile": "whole-spacecraft-with-time-triggered-faults",
        "absolute_time_dependencies": True,
        "native_state_subset": {},
    }
    path = tmp_path / "unapproved-rebase.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--rebase-worker", str(path), "1.0"],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert proc.returncode != 0
    assert "NATIVE_REBASE_PROFILE_UNSUPPORTED" in proc.stderr


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 4 and sys.argv[1] == "--rebase-worker":
        _fresh_process_rebase_worker(sys.argv[2], float(sys.argv[3]))
    else:
        raise SystemExit("test module only permits --rebase-worker")
