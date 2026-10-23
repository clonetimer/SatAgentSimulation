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


def _build(*, with_fsw: bool = False):
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
    feedback = None
    feedback_recorder = None
    feedback_input_owners = None
    if with_fsw:
        # Genuine C-backed Basilisk mrpFeedback, not a Python PID imitation.
        # These fixed guidance/config messages are owned by this test graph.
        from Basilisk.fswAlgorithms import mrpFeedback
        from Basilisk.architecture import messaging

        feedback = mrpFeedback.mrpFeedback()
        feedback.ModelTag = "test-mrp-feedback-integral"
        feedback.K = 0.15
        feedback.P = 8.0
        feedback.Ki = 0.08
        feedback.integralLimit = 12.0
        feedback.controlLawType = 0

        guid = messaging.AttGuidMsgPayload()
        guid.sigma_BR = [0.12, -0.06, 0.04]
        guid.omega_BR_B = [0.012, -0.008, 0.005]
        guid.omega_RN_B = [0.001, 0.0, -0.001]
        guid.domega_RN_B = [0.0, 0.0, 0.0]
        guidance_message = messaging.AttGuidMsg().write(guid)

        vehicle = messaging.VehicleConfigMsgPayload()
        vehicle.ISCPntB_B = [100.0, 0.0, 0.0,
                             0.0, 80.0, 0.0,
                             0.0, 0.0, 60.0]
        vehicle_message = messaging.VehicleConfigMsg().write(vehicle)
        feedback.guidInMsg.subscribeTo(guidance_message)
        feedback.vehConfigInMsg.subscribeTo(vehicle_message)
        feedback_input_owners = (guidance_message, vehicle_message)
        sim.AddModelToTask("slow", feedback, None, 90)
        feedback_recorder = feedback.cmdTorqueOutMsg.recorder()
        sim.AddModelToTask("slow", feedback_recorder, None, 80)

    sim.InitializeSimulation()
    base = (
        sim, {"dynamics": dynamics_proc, "observer": observer_proc},
        {"dynamics": fast, "observer": slow}, sc, rec,
    )
    if with_fsw:
        return (*base, feedback, feedback_recorder, feedback_input_owners)
    return base


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
    assert clock["thread_scheduler_priority_restored"] is False
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





def _feedback_rows(recorder, *, after_ns: int):
    return [
        (int(ns), [float(x) for x in torque])
        for ns, torque in zip(recorder.times(), recorder.torqueRequestBody)
        if int(ns) > after_ns
    ]


def _fsw_worker(checkpoint_file: str, end_s: float, mode: str) -> None:
    """A fresh Python process creates Basilisk C mrpFeedback and its input messages."""
    from Basilisk.utilities import macros
    from sat_sim.interactive.native_fsw_memory_pilot import (
        restore_mrp_feedback_memory_fresh, validate_mrp_feedback_memory,
    )
    if mode not in (
        "restore_fsw_memory", "omit_fsw_memory",
        "omit_prior_time", "omit_integral_state",
    ):
        raise ValueError("NATIVE_FSW_WORKER_MODE_INVALID")
    payload = json.loads(Path(checkpoint_file).read_text(encoding="utf-8"))
    assert payload["profile"] == PROFILE
    clock = validate_two_process_clock(payload["clock"])
    memory = validate_mrp_feedback_memory(payload["fsw_memory"])
    assert memory["prior_time_ns"] == 1_800_000_000
    sim, processes, tasks, sc, nav_rec, controller, feedback_rec, msg_owners = _build(
        with_fsw=True,
    )
    # Keep message owners alive until the native graph is done executing.
    assert len(msg_owners) == 2
    restore_state_subset_in_place(sc, payload["native_state_subset"], expected_rw_count=3)
    restore_two_process_clock_fresh(sim, processes, tasks, sc, payload["clock"])
    if mode != "omit_fsw_memory":
        restore_mrp_feedback_memory_fresh(controller, payload["fsw_memory"])
        if mode == "omit_prior_time":
            controller.priorTime = 0
        elif mode == "omit_integral_state":
            controller.int_sigma = [0.0, 0.0, 0.0]
    sim.ConfigureStopTime(macros.sec2nano(end_s))
    sim.ExecuteSimulation()
    print("FSW_MEMORY_RESULT:" + json.dumps({
        "worker_pid": os.getpid(),
        "mode": mode,
        "checkpoint_ns": clock["current_ns"],
        "final_sim_ns": int(sim.TotalSim.CurrentNanos),
        "feedback_rows": _feedback_rows(feedback_rec, after_ns=clock["current_ns"]),
        "native_state_subset": capture_state_subset(sc, expected_rw_count=3),
        "full_spacecraft_resume_supported": False,
    }, sort_keys=True), flush=True)


def test_cross_process_native_mrp_feedback_integral_memory_parity(tmp_path) -> None:
    """Controller integral memory affects real C-based FSW torque commands."""
    from Basilisk.utilities import macros
    from sat_sim.interactive.native_fsw_memory_pilot import (
        capture_mrp_feedback_memory, validate_mrp_feedback_memory,
    )

    sim, processes, tasks, sc, nav_rec, controller, torque_rec, owners = _build(
        with_fsw=True,
    )
    assert len(owners) == 2
    checkpoint_ns = macros.sec2nano(2.0)
    end_ns = macros.sec2nano(5.0)
    sim.ConfigureStopTime(checkpoint_ns)
    sim.ExecuteSimulation()
    clock = capture_two_process_clock(sim, processes, tasks, sc)
    memory = capture_mrp_feedback_memory(controller, checkpoint_ns=checkpoint_ns)
    assert memory["fields"]["prior_time_ns"] == macros.sec2nano(1.8)
    assert any(abs(x) > 1e-6 for x in memory["fields"]["memory"]["int_sigma"])
    validate_mrp_feedback_memory(memory)
    path = tmp_path / "native-fsw-checkpoint.json"
    path.write_text(json.dumps({
        "profile": PROFILE,
        "clock": clock,
        "native_state_subset": capture_state_subset(sc, expected_rw_count=3),
        "fsw_memory": memory,
    }, sort_keys=True), encoding="utf-8")

    sim.ConfigureStopTime(end_ns)
    sim.ExecuteSimulation()
    original_torques = _feedback_rows(torque_rec, after_ns=checkpoint_ns)
    assert len(original_torques) >= 3
    comparison = {}
    for mode in (
        "restore_fsw_memory", "omit_fsw_memory",
        "omit_prior_time", "omit_integral_state",
    ):
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()),
             "--fsw-worker", str(path), "5.0", mode],
            capture_output=True, text=True, timeout=45, check=False,
        )
        assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
        info = json.loads(next(
            row.removeprefix("FSW_MEMORY_RESULT:")
            for row in proc.stdout.splitlines()
            if row.startswith("FSW_MEMORY_RESULT:")
        ))
        assert info["worker_pid"] != os.getpid()
        assert info["final_sim_ns"] == end_ns
        assert info["full_spacecraft_resume_supported"] is False
        comparison[mode] = info["feedback_rows"]

    restored = comparison["restore_fsw_memory"]
    assert [ns for ns, _ in restored] == [ns for ns, _ in original_torques]
    for (_, actual), (_, expected) in zip(restored, original_torques):
        assert actual == pytest.approx(expected, abs=1e-10, rel=1e-12)

    for negative_case in ("omit_fsw_memory", "omit_prior_time", "omit_integral_state"):
        missed = comparison[negative_case]
        assert [ns for ns, _ in missed] == [ns for ns, _ in original_torques]
        assert max(
            abs(a - b)
            for (_, candidate), (_, expected) in zip(missed, original_torques)
            for a, b in zip(candidate, expected)
        ) > 1e-4, negative_case


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--worker":
        _worker(sys.argv[2], float(sys.argv[3]), sys.argv[4])
    elif len(sys.argv) == 5 and sys.argv[1] == "--fsw-worker":
        _fsw_worker(sys.argv[2], float(sys.argv[3]), sys.argv[4])
    else:
        raise SystemExit("isolated test-only dual-process/FSW worker")
