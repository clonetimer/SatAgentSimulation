"""Native Basilisk two-rate absolute scheduler recovery across OS processes.

Restricted to one process, 200ms dynamics task and 600ms native SimpleNav
observer task. Physics/observation parity proves scheduling phase, not full
system checkpoint; SimpleNav/FSW private history is NOT serialized.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sat_sim.interactive.native_multitask_pilot import (
    PROFILE, capture_two_task_clock, restore_two_task_clock_fresh,
    validate_two_task_clock,
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
    process = sim.CreateNewProcess("p")
    fast = sim.CreateNewTask("fast", macros.sec2nano(0.2))
    slow = sim.CreateNewTask("slow", macros.sec2nano(0.6))
    process.addTask(fast)
    process.addTask(slow)

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
    wheels = reactionWheelStateEffector.ReactionWheelStateEffector()
    factory = simIncludeRW.rwFactory()
    for i, axis in enumerate(([1, 0, 0], [0, 1, 0], [0, 0, 1])):
        factory.create("Honeywell_HR16", list(axis),
                       maxMomentum=50.0, Omega=100.0 * (i + 1),
                       u_max=0.2, label=f"RW{i+1}")
    factory.addToSpacecraft("rw", wheels, sc)
    sim.AddModelToTask("fast", wheels, None, 99)

    observer = simpleNav.SimpleNav()
    observer.scStateInMsg.subscribeTo(sc.scStateOutMsg)
    sim.AddModelToTask("slow", observer, None, 100)
    recorder = observer.transOutMsg.recorder()
    sim.AddModelToTask("slow", recorder, None, 99)
    sim.InitializeSimulation()
    return sim, process, fast, slow, sc, observer, recorder


def _observed(recorder, *, after_ns: int):
    out = []
    for ns, pos in zip(recorder.times(), recorder.r_BN_N):
        if int(ns) > after_ns:
            out.append((int(ns), [float(x) for x in pos]))
    return out


def _worker(filepath: str, stop_s: float, *, corrupt_slow_phase: bool) -> None:
    from Basilisk.utilities import macros
    item = json.loads(Path(filepath).read_text(encoding="utf-8"))
    if item["profile"] != PROFILE:
        raise ValueError("NATIVE_MULTITASK_PROFILE_INVALID")
    phase = item["scheduler"]
    fields = validate_two_task_clock(phase)
    sim, process, fast, slow, sc, nav, recorder = _build()
    restore_state_subset_in_place(sc, item["native_state_subset"], expected_rw_count=3)
    restore_two_task_clock_fresh(sim, process, fast, slow, sc, phase)
    if corrupt_slow_phase:
        # Deliberately shift the observer task by one full 600ms period.
        # This must change which observer timestamps exist downstream.
        slow.TaskData.NextStartTime += slow.TaskData.TaskPeriod
    sim.ConfigureStopTime(macros.sec2nano(stop_s))
    sim.ExecuteSimulation()
    print("MULTITASK_RESULT_JSON:" + json.dumps({
        "pid": os.getpid(),
        "checkpoint_ns": fields["sim_current_ns"],
        "last_native_time_ns": int(sim.TotalSim.CurrentNanos),
        "clock_phase_restored": not corrupt_slow_phase,
        "native_state_subset": capture_state_subset(sc, expected_rw_count=3),
        "observer_rows": _observed(
            recorder, after_ns=fields["sim_current_ns"],
        ),
        "full_spacecraft_resume_supported": False,
    }, sort_keys=True), flush=True)


def test_native_cross_process_two_rate_scheduler_phase_and_nav_observations(tmp_path):
    from Basilisk.utilities import macros

    sim, process, fast, slow, sc, nav, recorder = _build()
    checkpoint_s, finish_s = 2.0, 5.0
    sim.ConfigureStopTime(macros.sec2nano(checkpoint_s))
    sim.ExecuteSimulation()
    phase = capture_two_task_clock(sim, process, fast, slow, sc)
    assert phase["fields"]["tasks"]["dynamics"]["next_start_ns"] == 2_200_000_000
    assert phase["fields"]["tasks"]["observer"]["next_start_ns"] == 2_400_000_000
    state = capture_state_subset(sc, expected_rw_count=3)
    doc = tmp_path / "native-two-rate-checkpoint.json"
    doc.write_text(json.dumps({
        "profile": PROFILE, "scheduler": phase,
        "native_state_subset": state,
    }, sort_keys=True), encoding="utf-8")
    sim.ConfigureStopTime(macros.sec2nano(finish_s))
    sim.ExecuteSimulation()
    baseline = capture_state_subset(sc, expected_rw_count=3)
    expected_nav = _observed(recorder, after_ns=macros.sec2nano(checkpoint_s))
    assert len(expected_nav) >= 3

    outputs = {}
    for bad in (False, True):
        cmd = [sys.executable, str(Path(__file__).resolve()),
               "--two-task-worker", str(doc), str(finish_s), "bad" if bad else "good"]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=45, check=False,
        )
        assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
        result = json.loads(next(
            line[len("MULTITASK_RESULT_JSON:"):]
            for line in proc.stdout.splitlines()
            if line.startswith("MULTITASK_RESULT_JSON:")
        ))
        assert result["pid"] != os.getpid()
        assert result["checkpoint_ns"] == macros.sec2nano(checkpoint_s)
        assert result["last_native_time_ns"] == macros.sec2nano(finish_s)
        assert result["full_spacecraft_resume_supported"] is False
        validate_state_subset(result["native_state_subset"], expected_rw_count=3)
        outputs[bad] = result

    correct = outputs[False]
    for key, values in baseline["vectors"].items():
        assert correct["native_state_subset"]["vectors"][key] == pytest.approx(
            values, abs=1e-9, rel=1e-12,
        )
    assert [row[0] for row in correct["observer_rows"]] == [row[0] for row in expected_nav]
    for restored, expected in zip(correct["observer_rows"], expected_nav):
        assert restored[1] == pytest.approx(expected[1], abs=1e-8, rel=1e-12)

    # The misplaced slower task cannot reproduce the original observer
    # timestamps, even though fast dynamics might remain numerically equal.
    wrong = outputs[True]
    assert [row[0] for row in wrong["observer_rows"]] != [
        row[0] for row in expected_nav
    ]


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--two-task-worker":
        _worker(sys.argv[2], float(sys.argv[3]), corrupt_slow_phase=sys.argv[4] == "bad")
    else:
        raise SystemExit("native two-task checkpoint test subprocess only")
