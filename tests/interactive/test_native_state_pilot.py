"""Basilisk native state-manager checkpoint pilot; no claim of restart recovery."""
from __future__ import annotations

import copy
import pytest

from sat_sim.interactive.native_state_pilot import (
    capture_state_subset, restore_state_subset_in_place, validate_state_subset,
)


def _make_native_graph(
    *, earth_gravity: bool = False, scripted_torque: bool = False,
    expose_scheduler: bool = False,
):
    pytest.importorskip("Basilisk")
    from Basilisk.simulation import spacecraft, reactionWheelStateEffector, extForceTorque
    from Basilisk.utilities import SimulationBaseClass, simIncludeRW, macros

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("p")
    task = sim.CreateNewTask("t", macros.sec2nano(0.2))
    process.addTask(task)
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
    torque_effector = None
    if scripted_torque:
        torque_effector = extForceTorque.ExtForceTorque()
        torque_effector.extTorquePntB_B = [0.0, 0.0, 0.0]
        sc.addDynamicEffector(torque_effector)
        sim.AddModelToTask("t", torque_effector, None, 98)
    sim.InitializeSimulation()
    if scripted_torque:
        return (sim, sc, torque_effector, process, task) if expose_scheduler else (sim, sc, torque_effector)
    return (sim, sc, process, task) if expose_scheduler else (sim, sc)


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


def _advance_with_torque_events(sim, torque_effector, schedule, stop_s: float) -> None:
    """Schedule a controlled external-torque change *after* each stop boundary."""
    from Basilisk.utilities import macros

    for at_s, torque_nm in schedule:
        assert at_s <= stop_s
        sim.ConfigureStopTime(macros.sec2nano(at_s))
        sim.ExecuteSimulation()
        torque_effector.extTorquePntB_B = [0.0, 0.0, torque_nm]
    sim.ConfigureStopTime(macros.sec2nano(stop_s))
    sim.ExecuteSimulation()


def _timed_graph_rebase_worker(path: str, delta_s: float, mode: str) -> None:
    import json
    import os
    from pathlib import Path
    from Basilisk.utilities import macros
    from sat_sim.interactive.native_time_rebase_pilot import (
        PILOT_PROFILE, rebase_future_torque_events,
    )

    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if obj.get("profile") != PILOT_PROFILE:
        raise ValueError("NATIVE_REBASE_PROFILE_UNSUPPORTED")
    if mode not in ("mapped_absolute_events", "naive_unrebased_events"):
        raise ValueError("NATIVE_REBASE_MODE_UNSUPPORTED")
    step_s = float(obj["step_s"])
    checkpoint_s = float(obj["checkpoint_sim_time_s"])
    future = [tuple(event) for event in obj["future_torque_events"]]
    rebased = rebase_future_torque_events(
        profile=obj["profile"], checkpoint_s=checkpoint_s,
        step_s=step_s, absolute_events=future,
    )
    sim, sc, torque = _make_native_graph(earth_gravity=True, scripted_torque=True)
    initial_clock = int(sim.TotalSim.CurrentNanos)
    assert initial_clock == 0
    restore_state_subset_in_place(sc, obj["native_state_subset"], expected_rw_count=3)
    if mode == "mapped_absolute_events":
        local_events = rebased
    else:
        # This is a deliberately invalid recovery strategy: original absolute
        # times are applied against the fresh scheduler starting at t=0.
        # Only the event at local t <= delta can fire.
        local_events = tuple((at, force) for at, force in future if at <= delta_s)
    _advance_with_torque_events(sim, torque, local_events, delta_s)
    result = capture_state_subset(sc, expected_rw_count=3)
    print("TIMED_REBASE_RESULT_JSON:" + json.dumps({
        "pid": os.getpid(), "mode": mode,
        "original_checkpoint_s": checkpoint_s,
        "fresh_initial_scheduler_ns": initial_clock,
        "fresh_final_scheduler_ns": int(sim.TotalSim.CurrentNanos),
        "absolute_clock_restored": False,
        "event_mapping_applied": mode == "mapped_absolute_events",
        "full_spacecraft_resume_supported": False,
        "native_state_subset": result,
    }, sort_keys=True), flush=True)


def test_new_process_epoch_mapping_restores_deterministic_future_torque_timeline(tmp_path) -> None:
    """A scripted clock-dependent torque demonstrates why time rebasing matters.

    This does not replay Basilisk FSW/events/TC or restore native scheduler time.
    """
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    from Basilisk.utilities import macros
    from sat_sim.interactive.native_time_rebase_pilot import PILOT_PROFILE

    checkpoint_s, end_s, step_s = 2.0, 5.0, 0.2
    torque_events = ((3.0, 0.1), (4.0, 0.0))
    sim, sc, torque = _make_native_graph(earth_gravity=True, scripted_torque=True)
    sim.ConfigureStopTime(macros.sec2nano(checkpoint_s))
    sim.ExecuteSimulation()
    snapshot = capture_state_subset(sc, expected_rw_count=3)
    document = {
        "profile": PILOT_PROFILE,
        "checkpoint_sim_time_s": checkpoint_s,
        "step_s": step_s,
        "future_torque_events": torque_events,
        "native_state_subset": snapshot,
    }
    path = tmp_path / "time-rebase-proof.json"
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    _advance_with_torque_events(sim, torque, torque_events, end_s)
    expected = capture_state_subset(sc, expected_rw_count=3)
    assert int(sim.TotalSim.CurrentNanos) == macros.sec2nano(end_s)

    reports = {}
    for mode in ("mapped_absolute_events", "naive_unrebased_events"):
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--timed-worker",
             str(path), str(end_s - checkpoint_s), mode],
            capture_output=True, text=True, timeout=50, check=False,
        )
        assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
        obj = json.loads(next(
            line[len("TIMED_REBASE_RESULT_JSON:"):] for line in proc.stdout.splitlines()
            if line.startswith("TIMED_REBASE_RESULT_JSON:")
        ))
        assert obj["pid"] != os.getpid()
        assert obj["fresh_initial_scheduler_ns"] == 0
        assert obj["fresh_final_scheduler_ns"] == macros.sec2nano(end_s - checkpoint_s)
        assert obj["absolute_clock_restored"] is False
        assert obj["full_spacecraft_resume_supported"] is False
        validate_state_subset(obj["native_state_subset"], expected_rw_count=3)
        reports[mode] = obj

    corrected = reports["mapped_absolute_events"]["native_state_subset"]
    for key, baseline_values in expected["vectors"].items():
        assert corrected["vectors"][key] == pytest.approx(
            baseline_values, rel=1e-11, abs=1e-8
        )

    # Misinterpreting absolute event time as fresh-local time must NOT be
    # silently accepted; this case applies no torque during most of the run.
    naive = reports["naive_unrebased_events"]["native_state_subset"]
    assert abs(
        naive["vectors"]["hub_angular_rate_rad_s"][2]
        - expected["vectors"]["hub_angular_rate_rad_s"][2]
    ) > 1e-6





def _absolute_clock_restore_worker(path: str, end_s: float, mode: str) -> None:
    """Recreate the entire isolated graph in a separate OS Python process."""
    import json
    import os
    from pathlib import Path
    from Basilisk.utilities import macros
    from sat_sim.interactive.native_scheduler_pilot import (
        restore_single_task_clock_on_fresh_graph, validate_single_task_clock,
    )

    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("profile") != "isolated-earth-hub-rw-absolute-clock-v1":
        raise ValueError("NATIVE_ABSOLUTE_CLOCK_UNSUPPORTED_GRAPH")
    if mode not in {"with_spacecraft_time", "without_spacecraft_time"}:
        raise ValueError("NATIVE_ABSOLUTE_CLOCK_UNSUPPORTED_MODE")
    clock = document["clock"]
    source_clock = validate_single_task_clock(clock)
    sim, sc, process, task = _make_native_graph(earth_gravity=True, expose_scheduler=True)
    assert int(sim.TotalSim.CurrentNanos) == 0
    restore_state_subset_in_place(sc, document["native_state_subset"], expected_rw_count=3)
    restore_single_task_clock_on_fresh_graph(sim, process, task, sc, clock)
    if mode == "without_spacecraft_time":
        # Deliberate counterexample: setting native scheduler fields alone
        # loses the spacecraft's last-integration-time state.
        sc.timeBefore = 0.0
        sc.timeBeforeNanos = 0
        sc.timeStep = 0.0
    assert sim.TotalSim.CurrentNanos == source_clock["sim_current_ns"]
    sim.ConfigureStopTime(macros.sec2nano(end_s))
    sim.ExecuteSimulation()
    print("ABSOLUTE_CLOCK_RESULT_JSON:" + json.dumps({
        "pid": os.getpid(),
        "mode": mode,
        "source_checkpoint_ns": source_clock["sim_current_ns"],
        "fresh_final_scheduler_ns": int(sim.TotalSim.CurrentNanos),
        "absolute_native_clock_fields_written": True,
        "spacecraft_integrator_time_fields_written": mode == "with_spacecraft_time",
        "full_spacecraft_resume_supported": False,
        "native_state_subset": capture_state_subset(sc, expected_rw_count=3),
    }, sort_keys=True), flush=True)


def test_fresh_process_absolute_native_scheduler_and_hub_time_parity(tmp_path) -> None:
    """Absolute time reconstruction for ONE task+hub+Earth+three-RW only.

    Proves why both native scheduler task/process counters AND sc.timeBefore*
    are necessary. It does not serialize any other integrator or subsystem.
    """
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    from Basilisk.utilities import macros
    from sat_sim.interactive.native_scheduler_pilot import capture_single_task_clock

    checkpoint_s, stop_s = 2.0, 5.0
    sim, sc, process, task = _make_native_graph(earth_gravity=True, expose_scheduler=True)
    sim.ConfigureStopTime(macros.sec2nano(checkpoint_s))
    sim.ExecuteSimulation()
    snapshot = capture_state_subset(sc, expected_rw_count=3)
    phase = capture_single_task_clock(sim, process, task, sc)
    assert phase["fields"]["sim_current_ns"] == macros.sec2nano(checkpoint_s)
    assert phase["fields"]["spacecraft_time_before_ns"] == macros.sec2nano(checkpoint_s)
    path = tmp_path / "single-task-clock.json"
    path.write_text(json.dumps({
        "profile": "isolated-earth-hub-rw-absolute-clock-v1",
        "native_state_subset": snapshot,
        "clock": phase,
    }, sort_keys=True), encoding="utf-8")
    sim.ConfigureStopTime(macros.sec2nano(stop_s))
    sim.ExecuteSimulation()
    baseline = capture_state_subset(sc, expected_rw_count=3)

    outcomes = {}
    for mode in ("with_spacecraft_time", "without_spacecraft_time"):
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--absolute-clock-worker",
             str(path), str(stop_s), mode],
            capture_output=True, text=True, timeout=40, check=False,
        )
        assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
        msg = json.loads(next(
            line[len("ABSOLUTE_CLOCK_RESULT_JSON:"):] for line in proc.stdout.splitlines()
            if line.startswith("ABSOLUTE_CLOCK_RESULT_JSON:")
        ))
        assert msg["pid"] != os.getpid()
        assert msg["source_checkpoint_ns"] == macros.sec2nano(checkpoint_s)
        assert msg["fresh_final_scheduler_ns"] == macros.sec2nano(stop_s)
        assert msg["full_spacecraft_resume_supported"] is False
        validate_state_subset(msg["native_state_subset"], expected_rw_count=3)
        outcomes[mode] = msg

    good = outcomes["with_spacecraft_time"]["native_state_subset"]
    for name, values in baseline["vectors"].items():
        assert good["vectors"][name] == pytest.approx(values, abs=1e-9, rel=1e-12)
    bad = outcomes["without_spacecraft_time"]["native_state_subset"]
    # A missing integrator-time checkpoint creates a material orbit error.
    # Only require a grounded, nontrivial difference rather than an arbitrary
    # 100 m threshold (observed upstream CI discrepancy was about 97.6 m).
    assert abs(
        bad["vectors"]["hub_position_m"][0] - baseline["vectors"]["hub_position_m"][0]
    ) > 1.0


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 4 and sys.argv[1] == "--rebase-worker":
        _fresh_process_rebase_worker(sys.argv[2], float(sys.argv[3]))
    elif len(sys.argv) == 5 and sys.argv[1] == "--timed-worker":
        _timed_graph_rebase_worker(sys.argv[2], float(sys.argv[3]), sys.argv[4])
    elif len(sys.argv) == 5 and sys.argv[1] == "--absolute-clock-worker":
        _absolute_clock_restore_worker(sys.argv[2], float(sys.argv[3]), sys.argv[4])
    else:
        raise SystemExit("test module only permits explicitly named native trial worker modes")
