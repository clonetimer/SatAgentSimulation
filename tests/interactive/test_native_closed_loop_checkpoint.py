"""Real Basilisk ADCS closed-loop checkpoint *coverage* experiment.

This graph uses genuine Basilisk spacecraft and three reaction-wheel physics,
SimpleNav, inertial3D, attTrackingError, C mrpFeedback, and C rwMotorTorque.
It deliberately does NOT claim restart parity until outstanding message and
native RW actuator memory are checkpointed and shown equivalent.
"""
from __future__ import annotations

import pytest

from sat_sim.interactive.native_state_pilot import capture_state_subset
from sat_sim.interactive.native_multiprocess_pilot import capture_two_process_clock


def _build(*, closed_loop: bool = True):
    pytest.importorskip("Basilisk")
    from Basilisk.architecture import messaging
    from Basilisk.simulation import spacecraft, reactionWheelStateEffector, simpleNav
    from Basilisk.fswAlgorithms import (
        inertial3D, attTrackingError, mrpFeedback, rwMotorTorque,
    )
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
    sc.ModelTag = "native-closed-loop-spacecraft"
    sc.hub.mHub = 100.0
    sc.hub.IHubPntBc_B = [[100., 0., 0.], [0., 80., 0.], [0., 0., 60.]]
    sc.hub.r_CN_NInit = [7_000_000.0, 0.0, 0.0]
    sc.hub.v_CN_NInit = [0.0, 7_500.0, 0.0]
    sc.hub.sigma_BNInit = [0.12, -0.06, 0.04]
    sc.hub.omega_BN_BInit = [0.012, -0.008, 0.005]
    grav = simIncludeGravBody.gravBodyFactory()
    earth = grav.createEarth()
    earth.isCentralBody = True
    grav.addBodiesTo(sc)
    sim.AddModelToTask("fast", sc, None, 100)

    rw = reactionWheelStateEffector.ReactionWheelStateEffector()
    rw.ModelTag = "native-closed-loop-reaction-wheels"
    factory = simIncludeRW.rwFactory()
    for i, axis in enumerate(([1, 0, 0], [0, 1, 0], [0, 0, 1])):
        factory.create(
            "Honeywell_HR16", list(axis), maxMomentum=50.,
            Omega=100. * (i + 1), u_max=0.2, label=f"RW{i+1}",
        )
    factory.addToSpacecraft(rw.ModelTag, rw, sc)
    sim.AddModelToTask("fast", rw, None, 99)

    nav = simpleNav.SimpleNav()
    nav.scStateInMsg.subscribeTo(sc.scStateOutMsg)
    sim.AddModelToTask("slow", nav, None, 100)
    attitude_reference = inertial3D.inertial3D()
    attitude_reference.sigma_R0N = [0., 0., 0.]
    sim.AddModelToTask("slow", attitude_reference, None, 95)
    error = attTrackingError.attTrackingError()
    error.attNavInMsg.subscribeTo(nav.attOutMsg)
    error.attRefInMsg.subscribeTo(attitude_reference.attRefOutMsg)
    sim.AddModelToTask("slow", error, None, 90)

    controller = mrpFeedback.mrpFeedback()
    controller.K = 3.5
    controller.P = 30.
    controller.Ki = 0.02 if closed_loop else -1.
    controller.integralLimit = 2.
    controller.controlLawType = 0
    config_message = messaging.VehicleConfigMsg().write(
        messaging.VehicleConfigMsgPayload(
            ISCPntB_B=[100., 0., 0., 0., 80., 0., 0., 0., 60.],
        )
    )
    rw_params = factory.getConfigMessage()
    controller.guidInMsg.subscribeTo(error.attGuidOutMsg)
    controller.vehConfigInMsg.subscribeTo(config_message)
    controller.rwParamsInMsg.subscribeTo(rw_params)
    controller.rwSpeedsInMsg.subscribeTo(rw.rwSpeedOutMsg)
    sim.AddModelToTask("slow", controller, None, 85)

    allocator = rwMotorTorque.rwMotorTorque()
    allocator.controlAxes_B = [1., 0., 0., 0., 1., 0., 0., 0., 1.]
    allocator.rwParamsInMsg.subscribeTo(rw_params)
    allocator.vehControlInMsg.subscribeTo(controller.cmdTorqueOutMsg)
    sim.AddModelToTask("slow", allocator, None, 80)
    if closed_loop:
        rw.rwMotorCmdInMsg.subscribeTo(allocator.rwMotorTorqueOutMsg)

    control_log = controller.cmdTorqueOutMsg.recorder()
    wheel_log = rw.rwSpeedOutMsg.recorder()
    attitude_log = nav.attOutMsg.recorder()
    motor_log = allocator.rwMotorTorqueOutMsg.recorder()
    for log in (control_log, attitude_log, motor_log):
        sim.AddModelToTask("slow", log, None, 60)
    sim.AddModelToTask("fast", wheel_log, None, 50)

    sim.InitializeSimulation()
    return {
        "sim": sim,
        "processes": {"dynamics": dynamics_proc, "observer": observer_proc},
        "tasks": {"dynamics": fast, "observer": slow},
        "sc": sc, "rw": rw, "controller": controller,
        "allocator": allocator, "nav": nav, "attitude_reference": attitude_reference,
        "attitude_error": error,
        "messages": (config_message, rw_params),
        "control_log": control_log, "motor_log": motor_log,
        "wheel_log": wheel_log, "attitude_log": attitude_log,
    }


def _vectors(rows):
    return [[float(x) for x in v] for v in rows]


def test_real_native_adcs_loop_actuates_reaction_wheels_and_changes_attitude() -> None:
    from Basilisk.utilities import macros

    full = _build(closed_loop=True)
    open_loop = _build(closed_loop=False)
    for graph in (full, open_loop):
        graph["sim"].ConfigureStopTime(macros.sec2nano(4.0))
        graph["sim"].ExecuteSimulation()

    closed = capture_state_subset(full["sc"], expected_rw_count=3)
    baseline = capture_state_subset(open_loop["sc"], expected_rw_count=3)
    assert full["rw"].rwMotorCmdInMsg.isLinked()
    torques = _vectors(full["control_log"].torqueRequestBody)
    motors = _vectors(full["motor_log"].motorTorque)
    assert len(torques) >= 5
    assert len(motors) >= 5
    assert max(abs(v) for vector in motors for v in vector[:3]) > 1e-5
    assert max(abs(v) for vector in torques for v in vector[:3]) > 1e-5
    assert max(abs(x - y) for x, y in zip(
        closed["vectors"]["reaction_wheel_speed_rad_s"],
        baseline["vectors"]["reaction_wheel_speed_rad_s"],
    )) > 1e-4
    assert max(abs(x - y) for x, y in zip(
        closed["vectors"]["hub_mrp"], baseline["vectors"]["hub_mrp"],
    )) > 1e-7
    assert max(abs(x - y) for x, y in zip(
        closed["vectors"]["hub_angular_rate_rad_s"],
        baseline["vectors"]["hub_angular_rate_rad_s"],
    )) > 1e-8


def test_live_closed_loop_joint_checkpoint_fails_closed_until_rw_and_messages_covered() -> None:
    from Basilisk.utilities import macros

    graph = _build(closed_loop=True)
    graph["sim"].ConfigureStopTime(macros.sec2nano(2.0))
    graph["sim"].ExecuteSimulation()

    # The previously developed modules can already read the true C++ dynamics
    # state vectors and both original process scheduling tables.
    state = capture_state_subset(graph["sc"], expected_rw_count=3)
    clock = capture_two_process_clock(
        graph["sim"], graph["processes"], graph["tasks"], graph["sc"],
    )
    controller = graph["controller"]
    assert controller.priorTime > 0
    assert max(abs(float(x)) for x in controller.int_sigma) > 0
    assert controller.rwConfigParams.numRW == 3
    assert clock["state"]["current_ns"] == macros.sec2nano(2.0)

    # However, the previous fixed-input controller memory format explicitly
    # rejects the 3-wheel closed-loop cached RW configuration. It cannot be
    # silently re-used as proof of a complete satellite checkpoint.
    from sat_sim.interactive.native_fsw_memory_pilot import (
        capture_mrp_feedback_memory,
    )
    with pytest.raises(ValueError, match="FSW_CHECKPOINT_RW_CONFIGURATION_UNSUPPORTED"):
        capture_mrp_feedback_memory(controller, checkpoint_ns=macros.sec2nano(2.0))
    assert state["resume_supported"] is False
    assert clock["complete_satellite_resume"] is False

    from sat_sim.interactive.native_closed_loop_evidence import (
        MISSING_RESTORE_OWNERS, capture_closed_loop_evidence,
        verify_closed_loop_evidence,
    )
    import copy
    report = capture_closed_loop_evidence(
        sim=graph["sim"], processes=graph["processes"],
        tasks=graph["tasks"], spacecraft=graph["sc"],
        controller=graph["controller"], allocator=graph["allocator"],
    )
    verify_closed_loop_evidence(report)
    assert report["checkpoint_sim_time_ns"] == macros.sec2nano(2.0)
    assert report["fsw_integrator"]["cached_rw_count"] == 3
    assert report["fsw_integrator"]["prior_time_ns"] == macros.sec2nano(1.8)
    assert len(report["spacecraft_rw_state"]["vectors"]["reaction_wheel_speed_rad_s"]) == 3
    assert len(report["last_rw_motor_command_nm"]) == 3
    assert "rw_command_message_history" in report["restore_gaps"]
    assert report["restore_gaps"] == list(MISSING_RESTORE_OWNERS)
    assert report["resume_supported"] is False
    corrupted = copy.deepcopy(report)
    corrupted["resume_supported"] = True
    with pytest.raises(ValueError, match="ADCS_EVIDENCE_DIGEST_INVALID"):
        verify_closed_loop_evidence(corrupted)




def _closed_loop_rows(graph, *, after_ns: int):
    """Capture only native telemetry after the agreed physical checkpoint."""
    return {
        "fsw_torque": [
            (int(t), [float(v) for v in torque])
            for t, torque in zip(graph["control_log"].times(),
                                 graph["control_log"].torqueRequestBody)
            if int(t) > after_ns
        ],
        "motor_torque": [
            (int(t), [float(v) for v in torque[:3]])
            for t, torque in zip(graph["motor_log"].times(),
                                 graph["motor_log"].motorTorque)
            if int(t) > after_ns
        ],
        "rw_speeds": [
            (int(t), [float(v) for v in speeds[:3]])
            for t, speeds in zip(graph["wheel_log"].times(),
                                 graph["wheel_log"].wheelSpeeds)
            if int(t) > after_ns
        ],
    }


def _closed_loop_worker(path: str, end_s: float, mode: str) -> None:
    """New OS process with the same native ADCS graph and optional missing state."""
    import json
    import os
    from pathlib import Path
    from Basilisk.architecture import messaging
    from Basilisk.utilities import macros
    from sat_sim.interactive.native_state_pilot import restore_state_subset_in_place
    from sat_sim.interactive.native_multiprocess_pilot import restore_two_process_clock_fresh
    from sat_sim.interactive.native_closed_loop_evidence import verify_closed_loop_evidence

    if mode not in ("complete_pilot", "no_held_rw_command", "no_fsw_memory"):
        raise ValueError("ADCS_CLOSED_LOOP_UNSUPPORTED_TEST_MODE")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    evidence = data["evidence"]
    verify_closed_loop_evidence(evidence)
    graph = _build(closed_loop=True)
    restore_state_subset_in_place(
        graph["sc"], evidence["spacecraft_rw_state"], expected_rw_count=3,
    )
    restore_two_process_clock_fresh(
        graph["sim"], graph["processes"], graph["tasks"],
        graph["sc"], evidence["native_scheduler"],
    )
    fsw = evidence["fsw_integrator"]
    if mode != "no_fsw_memory":
        assert graph["controller"].rwConfigParams.numRW == fsw["cached_rw_count"]
        for name in ("K", "P", "Ki"):
            assert float(getattr(graph["controller"], name)) == fsw[name]
        graph["controller"].priorTime = fsw["prior_time_ns"]
        graph["controller"].int_sigma = fsw["int_sigma"]
        graph["controller"].z = fsw["z"]

    if mode != "no_held_rw_command":
        # The wheels have a zero/default command in a fresh Basilisk process.
        # Restore the *actual source module's last C output message* before
        # the first 0.2-second dynamics tick (the slow FSW next runs at 2.4s).
        # No synthetic motor calculation is involved.
        payload = messaging.ArrayMotorTorqueMsgPayload()
        payload.motorTorque = data["last_rw_motor_payload"]
        graph["allocator"].rwMotorTorqueOutMsg.write(
            payload, int(fsw["prior_time_ns"]),
        )

    checkpoint_ns = evidence["checkpoint_sim_time_ns"]
    graph["sim"].ConfigureStopTime(macros.sec2nano(end_s))
    graph["sim"].ExecuteSimulation()
    print("CLOSED_LOOP_RESTART_JSON:" + json.dumps({
        "pid": os.getpid(),
        "mode": mode,
        "checkpoint_ns": checkpoint_ns,
        "end_ns": int(graph["sim"].TotalSim.CurrentNanos),
        "resume_supported": False,  # Full spacecraft recovery still unproven
        "final_state": capture_state_subset(graph["sc"], expected_rw_count=3),
        "telemetry": _closed_loop_rows(graph, after_ns=checkpoint_ns),
    }, sort_keys=True), flush=True)


def test_native_full_loop_joint_checkpoint_cross_process_parity(tmp_path) -> None:
    """Strictly compare native spacecraft, wheels, FSW and motor-torque outputs.

    This isolated graph has no faults, TC, mode switches or stochastic noise.
    Proof of parity here must NEVER turn on production satellite resume.
    """
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    from Basilisk.utilities import macros
    from sat_sim.interactive.native_closed_loop_evidence import (
        capture_closed_loop_evidence, verify_closed_loop_evidence,
    )

    source = _build(closed_loop=True)
    checkpoint_ns = macros.sec2nano(2.0)
    stop_ns = macros.sec2nano(5.0)
    source["sim"].ConfigureStopTime(checkpoint_ns)
    source["sim"].ExecuteSimulation()
    evidence = capture_closed_loop_evidence(
        sim=source["sim"], processes=source["processes"],
        tasks=source["tasks"], spacecraft=source["sc"],
        controller=source["controller"], allocator=source["allocator"],
    )
    verify_closed_loop_evidence(evidence)
    # The complete MAX_EFF_CNT payload is saved verbatim, so zero-padded
    # motor torques remain byte-for-byte representable across SWIG bindings.
    native_motor_payload = [
        float(x) for x in source["allocator"].rwMotorTorqueOutMsg.read().motorTorque
    ]
    path = tmp_path / "closed-loop-native-evidence.json"
    path.write_text(json.dumps({
        "evidence": evidence,
        "last_rw_motor_payload": native_motor_payload,
    }, sort_keys=True), encoding="utf-8")
    source["sim"].ConfigureStopTime(stop_ns)
    source["sim"].ExecuteSimulation()
    baseline = capture_state_subset(source["sc"], expected_rw_count=3)
    expected = _closed_loop_rows(source, after_ns=checkpoint_ns)
    assert len(expected["fsw_torque"]) >= 3
    assert len(expected["rw_speeds"]) >= 10

    results = {}
    for mode in ("complete_pilot", "no_held_rw_command", "no_fsw_memory"):
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--adcs-worker",
             str(path), "5.0", mode],
            capture_output=True, text=True, timeout=45, check=False,
        )
        assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
        output = json.loads(next(
            row.removeprefix("CLOSED_LOOP_RESTART_JSON:")
            for row in proc.stdout.splitlines()
            if row.startswith("CLOSED_LOOP_RESTART_JSON:")
        ))
        assert output["pid"] != os.getpid()
        assert output["checkpoint_ns"] == checkpoint_ns
        assert output["end_ns"] == stop_ns
        assert output["resume_supported"] is False
        results[mode] = output

    restored = results["complete_pilot"]
    # Report the FIRST mismatched native record rather than hiding a missing
    # state behind a final attitude-only assertion.
    for name in ("fsw_torque", "motor_torque", "rw_speeds"):
        actual_rows, expected_rows = restored["telemetry"][name], expected[name]
        assert [ns for ns, _ in actual_rows] == [ns for ns, _ in expected_rows], name
        for row_idx, ((actual_t, actual), (expected_t, target)) in enumerate(
            zip(actual_rows, expected_rows)
        ):
            max_error = max(abs(a - b) for a, b in zip(actual, target))
            assert actual == pytest.approx(target, abs=1e-8, rel=1e-11), (
                f"FIRST_NATIVE_MISMATCH channel={name} row={row_idx} "
                f"time_ns={actual_t} max_error={max_error} "
                f"observed={actual[:5]} expected={target[:5]}"
            )
    for key, values in baseline["vectors"].items():
        assert restored["final_state"]["vectors"][key] == pytest.approx(
            values, abs=1e-8, rel=1e-11,
        ), key

    for mode in ("no_held_rw_command", "no_fsw_memory"):
        wrong = results[mode]
        delta = max(
            abs(a - b)
            for a, b in zip(
                wrong["final_state"]["vectors"]["reaction_wheel_speed_rad_s"],
                baseline["vectors"]["reaction_wheel_speed_rad_s"],
            )
        )
        assert delta > 1e-7, mode


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 5 and sys.argv[1] == "--adcs-worker":
        _closed_loop_worker(sys.argv[2], float(sys.argv[3]), sys.argv[4])
    else:
        raise SystemExit("native ADCS test-only worker")
