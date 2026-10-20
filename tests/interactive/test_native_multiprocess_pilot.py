"""Native Basilisk TWO independent processes checkpoint experiment.

No threads, no FSW/internal SimpleNav restoration and no full satellite claim.
High-priority dynamics process executes each 200ms, lower-priority native
SimpleNav each 600ms; the new Python process resumes at ABSOLUTE t=2s.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sat_sim.interactive.native_multiprocess_pilot import (
    PROFILE, capture_two_process_clock, restore_two_process_clock_fresh,
    validate_two_process_clock,
)
from sat_sim.interactive.native_state_pilot import (
    capture_state_subset, restore_state_subset_in_place, validate_state_subset,
)


def _build():
    pytest.importorskip("Basilisk")
    from Basilisk.simulation import spacecraft, reactionWheelStateEffector, simpleNav
    from Basilisk.utilities import (
        SimulationBaseClass, macros, simIncludeRW, simIncludeGravBody,
    )

    sim = SimulationBaseClass.SimBaseClass()
    dynamics_proc = sim.CreateNewProcess("dyn", priority=20)
    observer_proc = sim.CreateNewProcess("nav", priority=5)
    fast = sim.CreateNewTask("fast", macros.sec2nano(0.2))
    slow = sim.CreateNewTask("slow", macros.sec2nano(0.6))
    dynamics_proc.addTask(fast)
    observer_proc.addTask(slow)

    sc = spacecraft.Spacecraft()
    sc.hub.mHub = 100.0
    sc.hub.IHubPntBc_B = [[100., 0., 0.], [0., 80., 0.], [0., 0., 60.]]
    sc.hub.r_CN_NInit = [7_000_000.0, 0.0, 0.0]
    sc.hub.v_CN_NInit = [0.0, 7_500.0, 0.0]
    sc.hub.sigma_BNInit = [0.02, 0.0, 0.0]
    sc.hub.omega_BN_BInit = [0.01, 0.02, 0.01]
    grav = simIncludeGravBody.gravBodyFactory()
    earth = grav.createEarth()
    earth.isCentralBody = True
    grav.addBodiesTo(sc)

    sim.AddModelToTask("fast", sc, None, 100)
    rw = reactionWheelStateEffector.ReactionWheelStateEffector()
    factory = simIncludeRW.rwFactory()
    for i, axis in enumerate(([1, 0, 0], [0, 1, 0], [0, 0, 1])):
        factory.create(
            "Honeywell_HR16", list(axis), maxMomentum=50.0,
            Omega=100.0 * (i + 1), u_max=0.2, label=f"RW{i+1}",
        )
    factory.addToSpacecraft("rw", rw, sc)
    sim.AddModelToTask("fast", rw, None, 99)
    nav = simpleNav.SimpleNav()
    nav.scStateInMsg.subscribeTo(sc.scStateOutMsg)
    sim.AddModelToTask("slow", nav, None, 100)
    rec = nav.transOutMsg.recorder()
    sim.AddModelToTask("slow", rec, None, 99)
    sim.InitializeSimulation()
    return (
        sim, {"dynamics": dynamics_proc, "observer": observer_proc},
        {"dynamics": fast, "observer": slow}, sc, rec,
    )


def _records(rec, since_ns):
    return [
        (int(ns), [float(x) for x in pos])
        for ns, pos in zip(rec.times(), rec.r_BN_N)
        if int(ns) > since_ns
    ]


def _worker(path: str, until_s: float, mode: str):
    from Basilisk.utilities import macros

    item = json.loads(Path(path).read_text(encoding="utf-8"))
    if item.get("profile") != PROFILE:
        raise ValueError("NATIVE_MULTIPROCESS_PROFILE_INVALID")
    if mode not in {"correct", "bad_observer_cursor"}:
        raise ValueError("NATIVE_MULTIPROCESS_TEST_MODE_INVALID")
    state = validate_two_process_clock(item["clock"])
    sim, proc, tasks, sc, rec = _build()
    restore_state_subset_in_place(sc, item["native_state_subset"], expected_rw_count=3)
    restore_two_process_clock_fresh(sim, proc, tasks, sc, item["clock"])

    if mode == "bad_observer_cursor":
        # A native process maintains a separate scheduling-table cursor.
        nav_data = proc["observer"].processData
        entry = nav_data.processTasks[0]
        entry.NextTaskStart = state["current_ns"] + 200_000_000
        nav_data.processTasks[0] = entry
        nav_data.nextTaskTime = entry.NextTaskStart

    sim.ConfigureStopTime(macros.sec2nano(until_s))
    sim.ExecuteSimulation()
    print("DUAL_PROCESS_RESULT:" + json.dumps({
        "worker_pid": os.getpid(),
        "restored_checkpoint_ns": state["current_ns"],
        "final_native_ns": int(sim.TotalSim.CurrentNanos),
        "test_mode": mode,
        "full_spacecraft_resume_supported": False,
        "native_state_subset": capture_state_subset(sc, expected_rw_count=3),
        "observer_rows": _records(rec, since_ns=state["current_ns"]),
    }, sort_keys=True), flush=True)


def test_native_two_process_priority_and_phase_parity_across_new_python_process(tmp_path):
    from Basilisk.utilities import macros

    source, proc, tasks, sc, recorder = _build()
    checkpoint_s, end_s = 2.0, 5.0
    source.ConfigureStopTime(macros.sec2nano(checkpoint_s))
    source.ExecuteSimulation()
    clock = capture_two_process_clock(source, proc, tasks, sc)
    assert clock["state"]["next_process_priority"] == 20
    assert clock["state"]["processes"]["observer"]["priority"] == 5
    assert clock["state"]["processes"]["observer"]["scheduled_ns"] == 2_400_000_000
    native_states = capture_state_subset(sc, expected_rw_count=3)
    path = tmp_path / "two-process-clock.json"
    path.write_text(json.dumps({
        "profile": PROFILE, "clock": clock,
        "native_state_subset": native_states,
    }, sort_keys=True), encoding="utf-8")
    source.ConfigureStopTime(macros.sec2nano(end_s))
    source.ExecuteSimulation()
    physics = capture_state_subset(sc, expected_rw_count=3)
    nav_rows = _records(recorder, since_ns=macros.sec2nano(checkpoint_s))
    assert len(nav_rows) >= 3

    reports = {}
    for mode in ("correct", "bad_observer_cursor"):
        proc_run = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker",
             str(path), str(end_s), mode],
            capture_output=True, text=True, timeout=45, check=False,
        )
        assert proc_run.returncode == 0, proc_run.stdout + "\n" + proc_run.stderr
        result = json.loads(next(
            line.removeprefix("DUAL_PROCESS_RESULT:")
            for line in proc_run.stdout.splitlines()
            if line.startswith("DUAL_PROCESS_RESULT:")
        ))
        assert result["worker_pid"] != os.getpid()
        assert result["restored_checkpoint_ns"] == macros.sec2nano(checkpoint_s)
        assert result["final_native_ns"] == macros.sec2nano(end_s)
        assert result["full_spacecraft_resume_supported"] is False
        validate_state_subset(result["native_state_subset"], expected_rw_count=3)
        reports[mode] = result

    good = reports["correct"]
    for name, values in physics["vectors"].items():
        assert good["native_state_subset"]["vectors"][name] == pytest.approx(
            values, abs=1e-9, rel=1e-12,
        )
    assert [t for t, _ in good["observer_rows"]] == [t for t, _ in nav_rows]
    for observed, expected in zip(good["observer_rows"], nav_rows):
        assert observed[1] == pytest.approx(expected[1], abs=1e-8, rel=1e-12)
    assert [t for t, _ in reports["bad_observer_cursor"]["observer_rows"]] != [
        t for t, _ in nav_rows
    ]


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--worker":
        _worker(sys.argv[2], float(sys.argv[3]), sys.argv[4])
    else:
        raise SystemExit("isolated test-only dual-process worker")
