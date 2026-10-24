"""R83j native FSW scheduled-guidance excitation laboratory.

Research ONLY: changes a bounded Basilisk inertial3D MRP reference at scheduled
scheduler boundaries, on the SAME initialized Basilisk dynamics/FSW/RW graph.
This is NOT an external wheel motor-current telecommand, an injected wheel
fault, a qualified safe maneuver, an on-orbit actuator controller, or a
replacement for the R83f/R83g diagnosis refusals.

Native dynamics: spacecraft + reactionWheelStateEffector + simpleNav.
Native FSW: inertial3D -> attTrackingError -> mrpFeedback ->
rwMotorTorque -> reactionWheelStateEffector.
There is NO proxy dynamics fallback or posthoc fabricated motor telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

SCHEMA = "sat-sim.r83j.native-fsw-bounded-reference-excitation-lab.v1"
NATIVE_WHEEL_LIMIT_NM = 0.02
FSW_REQUEST_LIMIT_NM = 0.02
WHEEL_SPEED_OBSERVATION_LIMIT_RAD_S = 900.
MAX_REFERENCE_DEG = 0.5
STEP_S = 0.2
DURATION_S = 6.
# Changes to the pre-existing native inertial3D reference are made while the
# simulator is paused, after executing to a boundary, before the next task.
SCHEDULE = ((0., 0.), (1.8, 0.5), (3.8, 0.))
AXES = ((1.,0.,0.),(0.,1.,0.),(0.,0.,1.))


def _finite_number(x: Any, label: str) -> float:
    if type(x) not in (int, float) or not math.isfinite(x):
        raise ValueError("R83J_NONFINITE_OR_NONNUMERIC_"+label)
    return float(x)


def validate_schedule(schedule: tuple[tuple[float,float], ...] = SCHEDULE):
    if type(schedule) is not tuple or len(schedule)!=3:
        raise ValueError("R83J_UNSUPPORTED_SCHEDULE_LENGTH")
    times = []
    for item in schedule:
        if type(item) is not tuple or len(item)!=2:
            raise ValueError("R83J_INVALID_SCHEDULE_ENTRY")
        t=_finite_number(item[0],"time")
        angle=_finite_number(item[1],"angle")
        if abs(angle)>MAX_REFERENCE_DEG + 1e-12:
            raise ValueError("R83J_REFERENCE_EXCEEDS_BOUND")
        if t<0 or t>DURATION_S-STEP_S or abs(round(t/STEP_S)*STEP_S-t)>1e-9:
            raise ValueError("R83J_UNALIGNED_REFERENCE_TIME")
        times.append(t)
    if (schedule[0] != (0.,0.) or schedule[1][1]!=MAX_REFERENCE_DEG or
            schedule[2][1]!=0. or
            abs(schedule[1][0]-1.8)>1e-9 or
            abs(schedule[2][0]-3.8)>1e-9 or
            not times[0]<times[1]<times[2]):
        raise ValueError("R83J_MUST_USE_PREDECLARED_SAFE_LAB_WAVEFORM")
    return {"schema":SCHEMA,"schedule":[list(x) for x in schedule],
            "scheduler_step_s":STEP_S,
            "reference_limit_deg":MAX_REFERENCE_DEG,
            "wheel_hardware_torque_limit_nm":NATIVE_WHEEL_LIMIT_NM,
            "request_observation_limit_nm":FSW_REQUEST_LIMIT_NM,
            "wheel_speed_observation_limit_rad_s":WHEEL_SPEED_OBSERVATION_LIMIT_RAD_S,
            "safety_independently_certified":False,
            "flight_qualified":False,
            "no_rw_fault_injected":True,
            "diagnostic_model_trained":False}


def _reference_mrp(angle_deg: float) -> list[float]:
    # A single-axis MRP setpoint; affects FSW guidance reference, NEVER
    # bypasses native rwMotorTorque allocation to write actuator torque.
    return [math.tan(math.radians(angle_deg)/4.),0.,0.]


def _float3(data):
    return [float(data[i]) for i in range(3)]


def run_native_fsw_reference_arm(arm: str):
    """Run genuine Basilisk physical dynamics with ZERO or scheduled reference.

    Payload reading is native recorder output. An explicit physical response
    check is performed on BOTH arms by the separate experiment comparator.
    """
    if arm not in ("reference","scheduled"):
        raise ValueError("R83J_UNKNOWN_REFERENCE_ARM")
    validate_schedule()

    from Basilisk.architecture import messaging
    from Basilisk.fswAlgorithms import inertial3D, attTrackingError, mrpFeedback, rwMotorTorque
    from Basilisk.simulation import spacecraft, reactionWheelStateEffector, simpleNav
    from Basilisk.utilities import SimulationBaseClass, macros, simIncludeRW

    sim=SimulationBaseClass.SimBaseClass()
    dyn=sim.CreateNewProcess("r83j_native_dynamics")
    fsw=sim.CreateNewProcess("r83j_native_fsw")
    dyn_task="r83j_dynamics"
    fsw_task="r83j_fsw"
    dyn.addTask(sim.CreateNewTask(dyn_task,macros.sec2nano(STEP_S)))
    fsw.addTask(sim.CreateNewTask(fsw_task,macros.sec2nano(STEP_S)))

    sc=spacecraft.Spacecraft()
    sc.ModelTag="r83j_native_spacecraft"
    sc.hub.mHub=12.
    sc.hub.IHubPntBc_B=[[10.,0.,0.],[0.,8.,0.],[0.,0.,6.]]
    sc.hub.sigma_BNInit=[[0.],[0.],[0.]]
    sc.hub.omega_BN_BInit=[[0.],[0.],[0.]]
    sc.hub.r_CN_NInit=[7_000_000.,0.,0.]
    sc.hub.v_CN_NInit=[0.,7_500.,0.]
    sim.AddModelToTask(dyn_task,sc,1)

    rw=reactionWheelStateEffector.ReactionWheelStateEffector()
    wheel_factory=simIncludeRW.rwFactory()
    for i,axis in enumerate(AXES):
        wheel_factory.create(
            "Honeywell_HR16", list(axis), maxMomentum=50.,
            Omega=100.*(i+1), u_max=NATIVE_WHEEL_LIMIT_NM,
            label=f"R83J_RW_{i+1}")
    wheel_factory.addToSpacecraft("r83j_rw_effector",rw,sc)
    sim.AddModelToTask(dyn_task,rw,2)
    config=wheel_factory.getConfigMessage()

    nav=simpleNav.SimpleNav()
    nav.scStateInMsg.subscribeTo(sc.scStateOutMsg)
    sim.AddModelToTask(dyn_task,nav)

    reference=inertial3D.inertial3D()
    reference.ModelTag="r83j_native_inertial3d_scheduled_reference"
    reference.sigma_R0N=[0.,0.,0.]
    sim.AddModelToTask(fsw_task,reference)
    error=attTrackingError.attTrackingError()
    error.attRefInMsg.subscribeTo(reference.attRefOutMsg)
    error.attNavInMsg.subscribeTo(nav.attOutMsg)
    sim.AddModelToTask(fsw_task,error)
    controller=mrpFeedback.mrpFeedback()
    controller.K=2.
    controller.P=5.
    controller.Ki=-1.
    controller.integralLimit=-1.
    controller.guidInMsg.subscribeTo(error.attGuidOutMsg)
    payload=messaging.VehicleConfigMsgPayload()
    payload.ISCPntB_B=[10.,0.,0.,0.,8.,0.,0.,0.,6.]
    payload.massSC=12.
    vehicle_msg=messaging.VehicleConfigMsg().write(payload)
    controller.vehConfigInMsg.subscribeTo(vehicle_msg)
    controller.rwParamsInMsg.subscribeTo(config)
    controller.rwSpeedsInMsg.subscribeTo(rw.rwSpeedOutMsg)
    sim.AddModelToTask(fsw_task,controller)

    motor=rwMotorTorque.rwMotorTorque()
    motor.ModelTag="r83j_native_allocator"
    motor.controlAxes_B=[1.,0.,0.,0.,1.,0.,0.,0.,1.]
    motor.vehControlInMsg.subscribeTo(controller.cmdTorqueOutMsg)
    motor.rwParamsInMsg.subscribeTo(config)
    sim.AddModelToTask(fsw_task,motor)
    rw.rwMotorCmdInMsg.subscribeTo(motor.rwMotorTorqueOutMsg)

    sample=macros.sec2nano(STEP_S)
    ref_rec=reference.attRefOutMsg.recorder(sample)
    cmd_rec=controller.cmdTorqueOutMsg.recorder(sample)
    motor_rec=motor.rwMotorTorqueOutMsg.recorder(sample)
    rw_rec=rw.rwSpeedOutMsg.recorder(sample)
    sc_rec=sc.scStateOutMsg.recorder(sample)
    sim.AddModelToTask(fsw_task,ref_rec)
    sim.AddModelToTask(fsw_task,cmd_rec)
    sim.AddModelToTask(fsw_task,motor_rec)
    sim.AddModelToTask(dyn_task,rw_rec)
    sim.AddModelToTask(dyn_task,sc_rec)

    sim.InitializeSimulation()
    if arm=="scheduled":
        intervals=((1.8,0.),(3.8,MAX_REFERENCE_DEG),(DURATION_S,0.))
    else:
        intervals=((1.8,0.),(3.8,0.),(DURATION_S,0.))
    event_receipts=[]
    for boundary,angle in intervals:
        # Changing actual C-backed inertial3D model state while paused,
        # BEFORE next FSW step. This is a research-only native guidance
        # model reference setter, NOT motor-message overwrite.
        reference.sigma_R0N=_reference_mrp(angle)
        readback=_float3(reference.sigma_R0N)
        if any(abs(a-b)>1e-14 for a,b in zip(readback,_reference_mrp(angle))):
            raise RuntimeError("R83J_NATIVE_FSW_REFERENCE_READBACK_FAILED")
        event_receipts.append({"pause_before_next_segment_s":boundary,
                               "requested_reference_deg":angle,
                               "mrp_native_readback":readback,
                               "is_test_only":True})
        sim.ConfigureStopTime(macros.sec2nano(boundary))
        sim.ExecuteSimulation()
    raw_times=[float(v)*macros.NANO2SEC for v in ref_rec.times()]
    if not raw_times or abs(raw_times[-1]-DURATION_S)>STEP_S+1e-8:
        raise RuntimeError("R83J_NATIVE_FSW_SIMULATION_DID_NOT_ADVANCE")
    lengths=[len(raw_times),len(cmd_rec.times()),len(motor_rec.times()),
             len(rw_rec.times()),len(sc_rec.times())]
    if len(set(lengths))!=1:
        raise RuntimeError("R83J_NATIVE_SCHEDULER_RECORDER_LENGTH_MISMATCH")
    rows=[]
    for i,t in enumerate(raw_times):
        ref=_float3(ref_rec.sigma_RN[i])
        body=_float3(cmd_rec.torqueRequestBody[i])
        wheel=_float3(motor_rec.motorTorque[i])
        speeds=_float3(rw_rec.wheelSpeeds[i])
        sigma=_float3(sc_rec.sigma_BN[i])
        omega=_float3(sc_rec.omega_BN_B[i])
        if not all(math.isfinite(v) for v in (*ref,*body,*wheel,*speeds,*sigma,*omega)):
            raise RuntimeError("R83J_NONFINITE_NATIVE_MESSAGE")
        rows.append({"time_s":t,
                     **{f"fsw.native_reference_mrp_{j}":ref[j] for j in range(3)},
                     **{f"fsw.body_request_nm_{j}":body[j] for j in range(3)},
                     **{f"rw.native_allocator_request_nm_{j}":wheel[j] for j in range(3)},
                     **{f"rw.native_speed_rad_s_{j}":speeds[j] for j in range(3)},
                     **{f"hub.sigma_bn_{j}":sigma[j] for j in range(3)},
                     **{f"hub.omega_bn_b_rad_s_{j}":omega[j] for j in range(3)}})
    return {"schema":SCHEMA,"arm":arm,
            "execution_engine":"BASILISK_NATIVE_SC_RW_FSW_NO_PROXY",
            "native_module_chain":[
                "inertial3D","attTrackingError","mrpFeedback","rwMotorTorque",
                "reactionWheelStateEffector","spacecraft"],
            "native_scheduler_was_advanced":True,
            "reference_setpoint_event_receipts":event_receipts,
            "reference_is_guidance_not_wheel_motor_telecommand":True,
            "assurance":"RESEARCH_ONLY_NOT_FLIGHT_CERTIFIED",
            "rows":rows}


def compare_native_fsw_reference_experiment():
    """Fail closed if scheduled FSW guidance never reaches native RW physics."""
    validate_schedule()
    arms={arm:run_native_fsw_reference_arm(arm)
          for arm in ("reference","scheduled")}
    a,b=(arms[key]["rows"] for key in ("reference","scheduled"))
    if (len(a)!=len(b) or len(a)<20 or
            any(abs(x["time_s"]-y["time_s"])>1e-8 for x,y in zip(a,b))):
        raise RuntimeError("R83J_NATIVE_PAIRED_SCHEDULER_TIMELINE_INVALID")
    before=[(x,y) for x,y in zip(a,b) if x["time_s"]<=1.8+1e-8]
    after=[(x,y) for x,y in zip(a,b) if 2.2<=x["time_s"]<=3.8+1e-8]
    if len(before)<5 or len(after)<4:
        raise RuntimeError("R83J_NATIVE_PAIRED_REFERENCE_WINDOWS_MISSING")
    speed=lambda r: r["rw.native_speed_rad_s_0"]
    motor=lambda r:r["rw.native_allocator_request_nm_0"]
    ref=lambda r:r["fsw.native_reference_mrp_0"]
    pre_speed=max(abs(speed(x)-speed(y)) for x,y in before)
    post_speed=max(abs(speed(x)-speed(y)) for x,y in after)
    post_request=max(abs(motor(x)-motor(y)) for x,y in after)
    post_ref=max(abs(ref(x)-ref(y)) for x,y in after)
    if pre_speed>1e-8:
        raise RuntimeError("R83J_NATIVE_BASELINE_NOT_MATCHED_BEFORE_EVENT")
    if post_ref<1e-5 or post_request<1e-8 or post_speed<1e-8:
        raise RuntimeError("R83J_NATIVE_GUIDANCE_NOT_COUPLED_TO_RW_PHYSICS")
    max_requested=max(abs(r[f"rw.native_allocator_request_nm_{j}"])
                      for arm in arms.values() for r in arm["rows"] for j in range(3))
    max_speed=max(abs(r[f"rw.native_speed_rad_s_{j}"])
                  for arm in arms.values() for r in arm["rows"] for j in range(3))
    max_rate=max(abs(r[f"hub.omega_bn_b_rad_s_{j}"])
                 for arm in arms.values() for r in arm["rows"] for j in range(3))
    limits_pass=max_requested<=FSW_REQUEST_LIMIT_NM+1e-10 and max_speed<=WHEEL_SPEED_OBSERVATION_LIMIT_RAD_S
    return {"schema":SCHEMA,"research_question":"bounded native scheduled FSW reference reached actual RW speed",
            "arms":arms,
            "source_physics_real_native":True,
            "pre_event_speed_abs_difference":pre_speed,
            "post_event_max_rw_speed_difference_rad_s":post_speed,
            "post_event_max_motor_command_difference_nm":post_request,
            "post_event_max_native_reference_mrp_difference":post_ref,
            "max_motor_allocator_request_nm_all_rw":max_requested,
            "max_wheel_speed_rad_s_all_rw":max_speed,
            "max_body_rate_rad_s_all_axes":max_rate,
            "limits_within_provisional_lab_envelope":limits_pass,
            "independent_hardware_safety_certified":False,
            "fault_diagnosis_generalization_measured":False,
            "fault_injections_performed":False,
            "no_automatic_stable_promotion":True}
