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


def _build(*, closed_loop: bool = True, wheel_count: int = 3):
    pytest.importorskip("Basilisk")
    from Basilisk.architecture import messaging
    from Basilisk.simulation import spacecraft, reactionWheelStateEffector, simpleNav
    from Basilisk.fswAlgorithms import (
        inertial3D, hillPoint, attTrackingError, mrpFeedback, rwMotorTorque,
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

    if wheel_count not in (3, 4):
        raise ValueError("ADCS_NATIVE_TEST_GRAPH_RW_COUNT_INVALID")
    rw = reactionWheelStateEffector.ReactionWheelStateEffector()
    rw.ModelTag = "native-closed-loop-reaction-wheels"
    factory = simIncludeRW.rwFactory()
    if wheel_count == 3:
        axes = ([1., 0., 0.], [0., 1., 0.], [0., 0., 1.])
    else:
        import math
        scale = 1. / math.sqrt(3.)
        axes = tuple(
            [scale * x for x in axis]
            for axis in ((1., 1., 1.), (1., -1., -1.),
                         (-1., 1., -1.), (-1., -1., 1.))
        )
    for i, axis in enumerate(axes):
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
    hill = hillPoint.hillPoint()
    hill.ModelTag = "native-nadir-hill-point"
    hill.transNavInMsg.subscribeTo(nav.transOutMsg)
    sim.AddModelToTask("slow", hill, None, 94)
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
        "sim": sim, "wheel_count": wheel_count,
        "processes": {"dynamics": dynamics_proc, "observer": observer_proc},
        "tasks": {"dynamics": fast, "observer": slow},
        "sc": sc, "rw": rw, "controller": controller,
        "allocator": allocator, "nav": nav, "attitude_reference": attitude_reference,
        "hill_guidance": hill,
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
        rw_effector=graph["rw"],
    )
    verify_closed_loop_evidence(report)
    assert report["checkpoint_sim_time_ns"] == macros.sec2nano(2.0)
    assert report["fsw_integrator"]["cached_rw_count"] == 3
    assert len(report["rw_effector_memory"]) == 3
    assert any(abs(w["u_current"]) > 0 for w in report["rw_effector_memory"])
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
    count = graph["wheel_count"]
    return {
        "attitude_mrp": [
            (int(t), [float(v) for v in sigma])
            for t, sigma in zip(graph["attitude_log"].times(),
                                graph["attitude_log"].sigma_BN)
            if int(t) > after_ns
        ],
        "body_rate": [
            (int(t), [float(v) for v in rate])
            for t, rate in zip(graph["attitude_log"].times(),
                               graph["attitude_log"].omega_BN_B)
            if int(t) > after_ns
        ],
        "fsw_torque": [
            (int(t), [float(v) for v in torque])
            for t, torque in zip(graph["control_log"].times(),
                                 graph["control_log"].torqueRequestBody)
            if int(t) > after_ns
        ],
        "motor_torque": [
            (int(t), [float(v) for v in torque[:count]])
            for t, torque in zip(graph["motor_log"].times(),
                                 graph["motor_log"].motorTorque)
            if int(t) > after_ns
        ],
        "rw_speeds": [
            (int(t), [float(v) for v in speeds[:count]])
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
    from sat_sim.interactive.native_closed_loop_evidence import (
        verify_closed_loop_evidence, restore_rw_effector_memory_fresh,
    )

    if mode not in (
        "complete_pilot", "no_held_rw_command", "no_fsw_memory",
        "no_rw_effector_memory", "guidance_switch", "guidance_missing_switch",
        "guidance_pending", "guidance_pending_not_applied",
    ):
        raise ValueError("ADCS_CLOSED_LOOP_UNSUPPORTED_TEST_MODE")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    evidence = data["evidence"]
    verify_closed_loop_evidence(evidence)
    wheel_count = evidence["fsw_integrator"]["cached_rw_count"]
    graph = _build(closed_loop=True, wheel_count=wheel_count)
    if mode in (
        "guidance_switch", "guidance_missing_switch",
        "guidance_pending", "guidance_pending_not_applied",
    ):
        from sat_sim.interactive.native_mode_checkpoint import validate_mode_checkpoint
        from sat_sim.interactive.native_guidance_route_pilot import (
            restore_native_guidance_route_fresh,
        )
        journal = data["guidance_mode_checkpoint"]
        validated = validate_mode_checkpoint(journal)
        if validated["checkpoint_ns"] != evidence["checkpoint_sim_time_ns"]:
            raise ValueError("ADCS_NATIVE_GUIDANCE_CHECKPOINT_TIME_MISMATCH")
        if mode in ("guidance_pending", "guidance_pending_not_applied"):
            if (
                validated["current_mode"] != "inertial3d"
                or validated["transitions"][-1]["status"] != "pending"
                or validated["transitions"][-1]["to_mode"] != "nadir"
                or data.get("pending_apply_ns") != 2_200_000_000
            ):
                raise ValueError("ADCS_NATIVE_PENDING_MODE_CONTRACT_MISMATCH")
        if mode != "guidance_missing_switch":
            restore_native_guidance_route_fresh(graph, journal)
    restore_state_subset_in_place(
        graph["sc"], evidence["spacecraft_rw_state"], expected_rw_count=wheel_count,
    )
    if mode != "no_rw_effector_memory":
        restore_rw_effector_memory_fresh(
            graph["rw"], evidence["rw_effector_memory"],
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
    if mode in ("guidance_pending", "guidance_pending_not_applied"):
        # Advance only to the 2.2s dynamics-only boundary. Next native
        # FSW guidance/actuation update is due at 2.4s.
        apply_ns = data["pending_apply_ns"]
        if not checkpoint_ns < apply_ns < macros.sec2nano(end_s):
            raise ValueError("ADCS_NATIVE_PENDING_APPLY_TIME_INVALID")
        graph["sim"].ConfigureStopTime(apply_ns)
        graph["sim"].ExecuteSimulation()
        if mode == "guidance_pending":
            from sat_sim.interactive.native_guidance_route_pilot import (
                switch_native_guidance,
            )
            switch_native_guidance(graph, "nadir")
    graph["sim"].ConfigureStopTime(macros.sec2nano(end_s))
    graph["sim"].ExecuteSimulation()
    print("CLOSED_LOOP_RESTART_JSON:" + json.dumps({
        "pid": os.getpid(),
        "mode": mode,
        "checkpoint_ns": checkpoint_ns,
        "end_ns": int(graph["sim"].TotalSim.CurrentNanos),
        "resume_supported": False,  # Full spacecraft recovery still unproven
        "final_state": capture_state_subset(graph["sc"], expected_rw_count=wheel_count),
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
        rw_effector=source["rw"],
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
    for mode in (
        "complete_pilot", "no_held_rw_command", "no_fsw_memory",
        "no_rw_effector_memory",
    ):
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

    for mode in (
        "no_held_rw_command", "no_fsw_memory", "no_rw_effector_memory",
    ):
        wrong = results[mode]
        delta = max(
            abs(a - b)
            for a, b in zip(
                wrong["final_state"]["vectors"]["reaction_wheel_speed_rad_s"],
                baseline["vectors"]["reaction_wheel_speed_rad_s"],
            )
        )
        assert delta > 1e-7, mode




def test_native_guidance_mode_switch_restart_parity(tmp_path) -> None:
    """Inertial guidance -> true Basilisk hillPoint nadir guidance at 1.8s.

    Both C guidance algorithms run in the native 600ms FSW process. The
    tracking controller's *real* C input subscription changes, and a new OS
    process reconstructs that subscription alongside spacecraft/RW/FSW state.
    This proves an isolated route transition, not the full flight mode manager.
    """
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    from Basilisk.utilities import macros
    from sat_sim.interactive.native_mode_checkpoint import capture_mode_checkpoint
    from sat_sim.interactive.native_guidance_route_pilot import switch_native_guidance
    from sat_sim.interactive.native_closed_loop_evidence import (
        capture_closed_loop_evidence, verify_closed_loop_evidence,
    )

    source = _build(closed_loop=True)
    source["sim"].ConfigureStopTime(macros.sec2nano(1.8))
    source["sim"].ExecuteSimulation()
    # After the 1.8s FSW execution and before its next 2.4s execution:
    # route the actual attTrackingError input to the native Hill reference.
    switch_native_guidance(source, "nadir")
    checkpoint_ns = macros.sec2nano(2.0)
    source["sim"].ConfigureStopTime(checkpoint_ns)
    source["sim"].ExecuteSimulation()
    evidence = capture_closed_loop_evidence(
        sim=source["sim"], processes=source["processes"],
        tasks=source["tasks"], spacecraft=source["sc"],
        controller=source["controller"], allocator=source["allocator"],
        rw_effector=source["rw"],
    )
    verify_closed_loop_evidence(evidence)
    journal = capture_mode_checkpoint(
        checkpoint_ns=checkpoint_ns, current_mode="nadir",
        transitions=[
            {
                "event_id": "startup-inertial", "revision": 1,
                "requested_ns": 0, "resolved_ns": 0,
                "from_mode": "standby", "to_mode": "inertial3d",
                "status": "applied",
            },
            {
                "event_id": "mode-nadir-tc-1", "revision": 2,
                "requested_ns": macros.sec2nano(1.8),
                "resolved_ns": macros.sec2nano(1.8),
                "from_mode": "inertial3d", "to_mode": "nadir",
                "status": "applied",
            },
        ],
    )
    assert journal["resume_supported"] is False
    path = tmp_path / "native-guidance-switch-checkpoint.json"
    path.write_text(json.dumps({
        "evidence": evidence,
        "guidance_mode_checkpoint": journal,
        "last_rw_motor_payload": [
            float(x) for x in source["allocator"].rwMotorTorqueOutMsg.read().motorTorque
        ],
    }, sort_keys=True), encoding="utf-8")

    source["sim"].ConfigureStopTime(macros.sec2nano(5.0))
    source["sim"].ExecuteSimulation()
    expected_rows = _closed_loop_rows(source, after_ns=checkpoint_ns)
    expected_final = capture_state_subset(source["sc"], expected_rw_count=3)
    assert len(expected_rows["fsw_torque"]) >= 3
    results = {}
    for mode in ("guidance_switch", "guidance_missing_switch"):
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--adcs-worker",
             str(path), "5.0", mode],
            capture_output=True, text=True, timeout=45, check=False,
        )
        assert proc.returncode == 0, proc.stdout + "\\n" + proc.stderr
        results[mode] = json.loads(next(
            line.removeprefix("CLOSED_LOOP_RESTART_JSON:")
            for line in proc.stdout.splitlines()
            if line.startswith("CLOSED_LOOP_RESTART_JSON:")
        ))
        assert results[mode]["pid"] != os.getpid()
        assert results[mode]["resume_supported"] is False
        assert results[mode]["end_ns"] == macros.sec2nano(5.0)

    restored = results["guidance_switch"]
    for channel in ("attitude_mrp", "body_rate", "fsw_torque",
                    "motor_torque", "rw_speeds"):
        observed, baseline = restored["telemetry"][channel], expected_rows[channel]
        assert [t for t, _ in observed] == [t for t, _ in baseline], channel
        assert len(observed) > 2, channel
        for row_idx, ((at, values), (_, target)) in enumerate(zip(observed, baseline)):
            assert values == pytest.approx(target, abs=1e-8, rel=1e-11), (
                f"GUIDANCE_RESTART_FIRST_MISMATCH channel={channel} "
                f"row={row_idx} time_ns={at}"
            )
    for name, values in expected_final["vectors"].items():
        assert restored["final_state"]["vectors"][name] == pytest.approx(
            values, abs=1e-8, rel=1e-11
        ), name

    # The physically active route matters: preserving the same dynamics,
    # scheduler, controller integrator, and held motor command is not enough.
    omitted = results["guidance_missing_switch"]
    mismatch = max(
        abs(x - y)
        for (t, out), (u, ref) in zip(
            omitted["telemetry"]["fsw_torque"], expected_rows["fsw_torque"]
        )
        for x, y in zip(out, ref)
    )
    assert mismatch > 1e-7, "Missing native guidance connection went undetected"



def test_native_pending_mode_change_survives_restart(tmp_path) -> None:
    """Journal has a PENDING command at 2s, applied at 2.2s before FSW 2.4s."""
    import json
    import subprocess
    import sys
    from pathlib import Path
    from Basilisk.utilities import macros
    from sat_sim.interactive.native_mode_checkpoint import (
        capture_mode_checkpoint, validate_mode_checkpoint,
    )
    from sat_sim.interactive.native_guidance_route_pilot import switch_native_guidance
    from sat_sim.interactive.native_closed_loop_evidence import (
        capture_closed_loop_evidence,
    )

    source = _build(closed_loop=True)
    checkpoint_ns = macros.sec2nano(2.0)
    source["sim"].ConfigureStopTime(checkpoint_ns)
    source["sim"].ExecuteSimulation()
    evidence = capture_closed_loop_evidence(
        sim=source["sim"], processes=source["processes"],
        tasks=source["tasks"], spacecraft=source["sc"],
        controller=source["controller"], allocator=source["allocator"],
        rw_effector=source["rw"],
    )
    journal = capture_mode_checkpoint(
        checkpoint_ns=checkpoint_ns, current_mode="inertial3d",
        transitions=[
            {
                "event_id": "startup-inertial", "revision": 1,
                "requested_ns": 0, "resolved_ns": 0,
                "from_mode": "standby", "to_mode": "inertial3d",
                "status": "applied",
            },
            {
                "event_id": "queued-nadir-1", "revision": 2,
                "requested_ns": macros.sec2nano(1.8),
                "resolved_ns": None,
                "from_mode": "inertial3d", "to_mode": "nadir",
                "status": "pending",
            },
        ],
    )
    assert validate_mode_checkpoint(journal)["current_mode"] == "inertial3d"
    path = tmp_path / "pending-native-mode-journal.json"
    path.write_text(json.dumps({
        "evidence": evidence,
        "guidance_mode_checkpoint": journal,
        "pending_apply_ns": macros.sec2nano(2.2),
        "last_rw_motor_payload": [
            float(v) for v in source["allocator"].rwMotorTorqueOutMsg.read().motorTorque
        ],
    }, sort_keys=True), encoding="utf-8")

    source["sim"].ConfigureStopTime(macros.sec2nano(2.2))
    source["sim"].ExecuteSimulation()
    switch_native_guidance(source, "nadir")
    source["sim"].ConfigureStopTime(macros.sec2nano(5.0))
    source["sim"].ExecuteSimulation()
    baseline_rows = _closed_loop_rows(source, after_ns=checkpoint_ns)
    baseline_final = capture_state_subset(source["sc"], expected_rw_count=3)

    outputs = {}
    for mode in ("guidance_pending", "guidance_pending_not_applied"):
        process = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--adcs-worker",
             str(path), "5.0", mode],
            capture_output=True, text=True, check=False, timeout=45,
        )
        assert process.returncode == 0, process.stdout + "\\n" + process.stderr
        outputs[mode] = json.loads(next(
            line.removeprefix("CLOSED_LOOP_RESTART_JSON:")
            for line in process.stdout.splitlines()
            if line.startswith("CLOSED_LOOP_RESTART_JSON:")
        ))
        assert outputs[mode]["resume_supported"] is False

    recovered = outputs["guidance_pending"]
    for channel in (
        "attitude_mrp", "body_rate", "fsw_torque", "motor_torque", "rw_speeds",
    ):
        observed, baseline = recovered["telemetry"][channel], baseline_rows[channel]
        assert [t for t, _ in observed] == [t for t, _ in baseline], channel
        for (_, value), (_, reference) in zip(observed, baseline):
            assert value == pytest.approx(reference, abs=1e-8, rel=1e-11), channel
    for name, reference in baseline_final["vectors"].items():
        assert recovered["final_state"]["vectors"][name] == pytest.approx(
            reference, abs=1e-8, rel=1e-11
        ), name

    skipped = outputs["guidance_pending_not_applied"]
    assert max(
        abs(a - b)
        for (_, actual), (_, reference) in zip(
            skipped["telemetry"]["fsw_torque"], baseline_rows["fsw_torque"]
        )
        for a, b in zip(actual, reference)
    ) > 1e-7, "Pending transition was silently ignored"



def test_native_four_rw_closed_loop_cross_process_parity(tmp_path) -> None:
    """R9-A: four real native wheels, two processes, fresh PID, true feedback.

    This *restricted* four-wheel research graph has no full EPS/thermal/comm,
    fault event queue, stochastic sensor state or production command ledger.
    It must never authorize automatic whole-spacecraft restart.
    """
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    from Basilisk.utilities import macros
    from sat_sim.interactive.native_closed_loop_evidence import (
        capture_closed_loop_evidence, verify_closed_loop_evidence, _checksum,
    )

    source = _build(closed_loop=True, wheel_count=4)
    checkpoint_ns, stop_ns = macros.sec2nano(2.), macros.sec2nano(5.)
    source["sim"].ConfigureStopTime(checkpoint_ns)
    source["sim"].ExecuteSimulation()
    evidence = capture_closed_loop_evidence(
        sim=source["sim"], processes=source["processes"],
        tasks=source["tasks"], spacecraft=source["sc"],
        controller=source["controller"], allocator=source["allocator"],
        rw_effector=source["rw"],
    )
    verify_closed_loop_evidence(evidence)
    assert evidence["fsw_integrator"]["cached_rw_count"] == 4
    assert len(evidence["rw_effector_memory"]) == 4
    assert len(evidence["spacecraft_rw_state"]["vectors"]["reaction_wheel_speed_rad_s"]) == 4
    assert len(evidence["last_rw_motor_command_nm"]) == 4
    assert evidence["resume_supported"] is False

    # Strong negative guard even if an attacker recomputes the outer digest:
    # metadata claiming 3RW must be rejected against four genuine C++ wheels.
    import copy
    malformed = copy.deepcopy(evidence)
    malformed["fsw_integrator"]["cached_rw_count"] = 3
    malformed["digest_sha256"] = _checksum({
        k: v for k, v in malformed.items() if k != "digest_sha256"
    })
    with pytest.raises(ValueError, match="NATIVE_STATE_REACTION_WHEEL_COUNT_MISMATCH"):
        verify_closed_loop_evidence(malformed)

    checkpoint_path = tmp_path / "native-four-rw-closed-loop-evidence.json"
    checkpoint_path.write_text(json.dumps({
        "evidence": evidence,
        "last_rw_motor_payload": [
            float(v) for v in source["allocator"].rwMotorTorqueOutMsg.read().motorTorque
        ],
    }, sort_keys=True), encoding="utf-8")

    source["sim"].ConfigureStopTime(stop_ns)
    source["sim"].ExecuteSimulation()
    baseline_state = capture_state_subset(source["sc"], expected_rw_count=4)
    baseline = _closed_loop_rows(source, after_ns=checkpoint_ns)
    assert len(baseline["fsw_torque"]) >= 3
    assert len(baseline["rw_speeds"]) >= 10
    assert all(len(v) == 4 for _, v in baseline["rw_speeds"])
    assert all(len(v) == 4 for _, v in baseline["motor_torque"])
    assert max(abs(x) for _, row in baseline["motor_torque"] for x in row) > 1e-7

    outputs = {}
    for omission in ("complete_pilot", "no_held_rw_command", "no_fsw_memory"):
        run = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--adcs-worker",
             str(checkpoint_path), "5.0", omission],
            capture_output=True, text=True, timeout=45, check=False,
        )
        assert run.returncode == 0, run.stdout + "\n" + run.stderr
        report = json.loads(next(
            ln.removeprefix("CLOSED_LOOP_RESTART_JSON:")
            for ln in run.stdout.splitlines()
            if ln.startswith("CLOSED_LOOP_RESTART_JSON:")
        ))
        assert report["pid"] != os.getpid()
        assert report["end_ns"] == stop_ns
        assert report["checkpoint_ns"] == checkpoint_ns
        assert report["resume_supported"] is False
        outputs[omission] = report

    correct = outputs["complete_pilot"]
    for key in ("attitude_mrp", "body_rate", "fsw_torque", "motor_torque", "rw_speeds"):
        expected, observed = baseline[key], correct["telemetry"][key]
        assert [t for t, _ in observed] == [t for t, _ in expected], key
        for index, ((t, values), (_, reference)) in enumerate(zip(observed, expected)):
            assert len(values) == len(reference)
            assert values == pytest.approx(reference, abs=1e-8, rel=1e-11), (
                f"R9_A_4RW_FIRST_MISMATCH channel={key} frame={index} t_ns={t}"
            )
    for key, vector in baseline_state["vectors"].items():
        assert correct["final_state"]["vectors"][key] == pytest.approx(
            vector, abs=1e-8, rel=1e-11
        ), key

    # Omitting held RW commands or integral FSW memory must produce a
    # measurable *physical* difference, not only a different metadata flag.
    for omission in ("no_held_rw_command", "no_fsw_memory"):
        wrong = outputs[omission]
        rw_delta = max(abs(x - y) for x, y in zip(
            wrong["final_state"]["vectors"]["reaction_wheel_speed_rad_s"],
            baseline_state["vectors"]["reaction_wheel_speed_rad_s"],
        ))
        fsw_delta = max(
            abs(x - y)
            for (_, row), (_, target) in zip(
                wrong["telemetry"]["fsw_torque"], baseline["fsw_torque"],
            )
            for x, y in zip(row, target)
        )
        assert rw_delta > 1e-7 or fsw_delta > 1e-7, (
            omission, rw_delta, fsw_delta,
        )

if __name__ == "__main__":
    import sys
    if len(sys.argv) == 5 and sys.argv[1] == "--adcs-worker":
        _closed_loop_worker(sys.argv[2], float(sys.argv[3]), sys.argv[4])
    else:
        raise SystemExit("native ADCS test-only worker")
