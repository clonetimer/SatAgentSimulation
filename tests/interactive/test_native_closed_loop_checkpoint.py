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
