"""R83l preregistered multi-wheel and moderate/severe loss Basilisk pilot.

EXPLORATORY original Basilisk physics. The two-factor trial manipulates
a genuine shared_ptr RWConfigPayload.u_max at a PAUSED native scheduler
boundary, not an observation label, posthoc telemetry edit, or simplified
Python dynamics.
Native scheduled attitude-reference update is the actual R83j guidance
chain; changing the actuator's physical cap tests torque-authority loss.
No flight or diagnostic certification.

Native dynamics: spacecraft + reactionWheelStateEffector + simpleNav.
Native FSW: inertial3D -> attTrackingError -> mrpFeedback ->
rwMotorTorque -> reactionWheelStateEffector.
There is NO proxy dynamics fallback or posthoc fabricated motor telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

SCHEMA = "sat-sim.r83l.real-native-multiwheel-torque-authority-physics.v1"
FAULT_ONSET_S = 2.4
WHEELS = (0,1,2)
INITIAL_STATES = (
    (0.0, 0.0, 0.0),
    (0.001, -0.0005, 0.0007),
    (-0.0008, 0.0007, -0.0004),
)
REFERENCE_MODES = ("zero","pulse")
CAPACITY_MODES = ("healthy","loss75","loss95")
CAPACITY_FACTOR = {"healthy":1.0,"loss75":0.25,"loss95":0.05}
# The same initial spacecraft condition is reused ACROSS ALL arms in a
# physical group. This creates 9 distinct wheel/initial combinations, NOT
# 54 statistically independent physical systems.
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
        raise ValueError("R83L_NONFINITE_OR_NONNUMERIC_"+label)
    return float(x)


def validate_schedule(schedule: tuple[tuple[float,float], ...] = SCHEDULE):
    if type(schedule) is not tuple or len(schedule)!=3:
        raise ValueError("R83L_UNSUPPORTED_SCHEDULE_LENGTH")
    times = []
    for item in schedule:
        if type(item) is not tuple or len(item)!=2:
            raise ValueError("R83L_INVALID_SCHEDULE_ENTRY")
        t=_finite_number(item[0],"time")
        angle=_finite_number(item[1],"angle")
        if abs(angle)>MAX_REFERENCE_DEG + 1e-12:
            raise ValueError("R83L_REFERENCE_EXCEEDS_BOUND")
        if t<0 or t>DURATION_S-STEP_S or abs(round(t/STEP_S)*STEP_S-t)>1e-9:
            raise ValueError("R83L_UNALIGNED_REFERENCE_TIME")
        times.append(t)
    if (schedule[0] != (0.,0.) or schedule[1][1]!=MAX_REFERENCE_DEG or
            schedule[2][1]!=0. or
            abs(schedule[1][0]-1.8)>1e-9 or
            abs(schedule[2][0]-3.8)>1e-9 or
            not times[0]<times[1]<times[2]):
        raise ValueError("R83L_MUST_USE_PREDECLARED_SAFE_LAB_WAVEFORM")
    return {"schema":SCHEMA,"schedule":[list(x) for x in schedule],
            "scheduler_step_s":STEP_S,
            "reference_limit_deg":MAX_REFERENCE_DEG,
            "wheel_hardware_torque_limit_nm":NATIVE_WHEEL_LIMIT_NM,
            "request_observation_limit_nm":FSW_REQUEST_LIMIT_NM,
            "wheel_speed_observation_limit_rad_s":WHEEL_SPEED_OBSERVATION_LIMIT_RAD_S,
            "safety_independently_certified":False,
            "flight_qualified":False,
            "fault_physical_derating_experiment":True,
            "diagnostic_model_trained":False}


def _reference_mrp(angle_deg: float, wheel: int) -> list[float]:
    # A single-axis MRP setpoint; affects FSW guidance reference, NEVER
    # bypasses native rwMotorTorque allocation to write actuator torque.
    return [math.tan(math.radians(angle_deg)/4.) if i == wheel else 0. for i in range(3)]


def _float3(data):
    return [float(data[i]) for i in range(3)]


def run_native_parameter_case(*, wheel: int, initial: int,
                              reference_mode: str, capacity_mode: str):
    """Run genuine Basilisk physical dynamics with ZERO or scheduled reference.

    Payload reading is native recorder output. An explicit physical response
    check is performed on BOTH arms by the separate experiment comparator.
    """
    if (type(wheel) is not int or wheel not in WHEELS or
            type(initial) is not int or initial not in range(len(INITIAL_STATES)) or
            reference_mode not in REFERENCE_MODES or
            capacity_mode not in CAPACITY_MODES):
        raise ValueError("R83L_UNKNOWN_OR_UNREGISTERED_PHYSICAL_ARM")
    guided=reference_mode=="pulse"
    degraded=capacity_mode!="healthy"
    requested_cap=NATIVE_WHEEL_LIMIT_NM*CAPACITY_FACTOR[capacity_mode]
    label=f"rw{wheel}_initial{initial}_{reference_mode}_{capacity_mode}"
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
    sc.hub.sigma_BNInit=[[float(v)] for v in INITIAL_STATES[initial]]
    sc.hub.omega_BN_BInit=[[0.],[0.],[0.]]
    sc.hub.r_CN_NInit=[7_000_000.,0.,0.]
    sc.hub.v_CN_NInit=[0.,7_500.,0.]
    sim.AddModelToTask(dyn_task,sc,1)

    rw=reactionWheelStateEffector.ReactionWheelStateEffector()
    wheel_factory=simIncludeRW.rwFactory()
    native_wheel_objects=[]
    for i,axis in enumerate(AXES):
        native_wheel_objects.append(wheel_factory.create(
            "Honeywell_HR16", list(axis), maxMomentum=50.,
            Omega=100.*(i+1), u_max=NATIVE_WHEEL_LIMIT_NM,
            label=f"RW{i+1}"))
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
    # C++ RWConfigLogMsgPayload contains the actual u_current recorded by
    # native dynamics, not the allocated motor command.
    rw_actual_rec=rw.rwOutMsgs[wheel].recorder(sample)
    sc_rec=sc.scStateOutMsg.recorder(sample)
    sim.AddModelToTask(fsw_task,ref_rec)
    sim.AddModelToTask(fsw_task,cmd_rec)
    sim.AddModelToTask(fsw_task,motor_rec)
    sim.AddModelToTask(dyn_task,rw_rec)
    sim.AddModelToTask(dyn_task,rw_actual_rec)
    sim.AddModelToTask(dyn_task,sc_rec)

    sim.InitializeSimulation()
    # Exactly two independent factorial interventions:
    # at 1.8s update FSW *reference* (effect visible in 2.0s recorder);
    # at 2.4s derate RW0's true C-backed actuator capacity
    # (effect visible in 2.6s recorder). No hidden test/VAL cases.
    angle=MAX_REFERENCE_DEG if guided else 0.
    intervals=((1.8,0.,False),(FAULT_ONSET_S,angle,False),
               (3.8,angle,True),(DURATION_S,0.,False))
    event_receipts=[]
    for boundary,requested_angle,at_fault_boundary in intervals:
        angle=requested_angle
        if at_fault_boundary:
            rw0=native_wheel_objects[wheel]
            before=float(rw0.u_max)
            if abs(before-NATIVE_WHEEL_LIMIT_NM)>1e-12:
                raise RuntimeError("R83L_ORIGINAL_WHEEL_CAPACITY_WRONG")
            if degraded:
                rw0.u_max=requested_cap
            after=float(rw0.u_max)
            expected=requested_cap
            native_effector_readback=float(rw.ReactionWheelData[wheel].u_max)
            if abs(after-expected)>1e-12 or abs(native_effector_readback-expected)>1e-12:
                raise RuntimeError("R83L_ACTUAL_NATIVE_RW_SHARED_CAPACITY_MUTATION_NOT_EFFECTIVE")
            event_receipts.append({
                "fault_scheduler_pause_s":FAULT_ONSET_S,
                "physical_fault_target_rw_index_QA_ONLY":wheel,
                "fault_actual_native_rw_max_torque_before_nm":before,
                "fault_actual_native_rw_max_torque_after_nm":after,
                "rw_effector_c_backed_readback_nm":native_effector_readback,
                "physical_torque_cap_derated":degraded,
                "real_native_physical_change_not_feature_edit":True})
        # Changing actual C-backed inertial3D model state while paused,
        # BEFORE next FSW step. This is a research-only native guidance
        # model reference setter, NOT motor-message overwrite.
        reference.sigma_R0N=_reference_mrp(angle,wheel)
        readback=_float3(reference.sigma_R0N)
        if any(abs(a-b)>1e-14 for a,b in zip(readback,_reference_mrp(angle,wheel))):
            raise RuntimeError("R83L_NATIVE_FSW_REFERENCE_READBACK_FAILED")
        event_receipts.append({"pause_before_next_segment_s":boundary,
                               "requested_reference_deg":angle,
                               "mrp_native_readback":readback,
                               "is_test_only":True})
        sim.ConfigureStopTime(macros.sec2nano(boundary))
        sim.ExecuteSimulation()
    raw_times=[float(v)*macros.NANO2SEC for v in ref_rec.times()]
    if not raw_times or abs(raw_times[-1]-DURATION_S)>STEP_S+1e-8:
        raise RuntimeError("R83L_NATIVE_FSW_SIMULATION_DID_NOT_ADVANCE")
    lengths=[len(raw_times),len(cmd_rec.times()),len(motor_rec.times()),
             len(rw_rec.times()),len(sc_rec.times()),
             len(rw_actual_rec.times())]
    if len(set(lengths))!=1:
        raise RuntimeError("R83L_NATIVE_SCHEDULER_RECORDER_LENGTH_MISMATCH")
    rows=[]
    for i,t in enumerate(raw_times):
        ref=_float3(ref_rec.sigma_RN[i])
        body=_float3(cmd_rec.torqueRequestBody[i])
        wheel_commands=_float3(motor_rec.motorTorque[i])
        speeds=_float3(rw_rec.wheelSpeeds[i])
        sigma=_float3(sc_rec.sigma_BN[i])
        omega=_float3(sc_rec.omega_BN_B[i])
        actual=float(rw_actual_rec.u_current[i])
        if not all(math.isfinite(v) for v in (*ref,*body,*wheel_commands,*speeds,*sigma,*omega,actual)):
            raise RuntimeError("R83L_NONFINITE_NATIVE_MESSAGE")
        rows.append({"time_s":t,
                     **{f"fsw.native_reference_mrp_{j}":ref[j] for j in range(3)},
                     **{f"fsw.body_request_nm_{j}":body[j] for j in range(3)},
                     **{f"rw.native_allocator_request_nm_{j}":wheel_commands[j] for j in range(3)},
                     **{f"rw.native_speed_rad_s_{j}":speeds[j] for j in range(3)},
                     "rw.native_target_applied_torque_nm_QA_ONLY":actual,
                     **{f"hub.sigma_bn_{j}":sigma[j] for j in range(3)},
                     **{f"hub.omega_bn_b_rad_s_{j}":omega[j] for j in range(3)}})
    return {"schema":SCHEMA,"arm":label,
            "native_independent_condition_id":f"rw{wheel}_initial{initial}",
            "native_reference_context_id":f"rw{wheel}_initial{initial}_{reference_mode}",
            "source_fault_target_index_QA_ONLY":wheel,
            "source_initial_state_index":initial,
            "source_initial_attitude_mrp":list(INITIAL_STATES[initial]),
            "native_reference_mode":reference_mode,
            "native_fault_capacity_mode_QA_ONLY":capacity_mode,
            "execution_engine":"BASILISK_NATIVE_SC_RW_FSW_NO_PROXY",
            "native_module_chain":[
                "inertial3D","attTrackingError","mrpFeedback","rwMotorTorque",
                "reactionWheelStateEffector","spacecraft"],
            "native_scheduler_was_advanced":True,
            "reference_setpoint_event_receipts":event_receipts,
            "physical_fault_mechanism_QA_ONLY":capacity_mode if degraded else "NORMAL",
            "true_native_actuator_capacity_derated":degraded,
            "fault_onset_paused_scheduler_s_QA_ONLY":FAULT_ONSET_S,
            "original_native_rw_hardware_limit_nm":NATIVE_WHEEL_LIMIT_NM,
            "fault_native_rw_capacity_after_nm_QA_ONLY":requested_cap,
            "model_inference_uses_truth":False,
            "reference_is_guidance_not_wheel_motor_telecommand":True,
            "assurance":"RESEARCH_ONLY_NOT_FLIGHT_CERTIFIED",
            "rows":rows}







def run_native_multifactor_source_physics():
    """54 actual C-backed simulations; 9 grouped initial conditions.

    3 independent wheel targets × 3 distinct deterministic physical initial
    conditions × 2 reference contexts × (one healthy + two deratings).
    Exactly 18 healthy cases are shared by both severity comparisons, not
    cloned as independent observations.
    """
    validate_schedule()
    if (len(INITIAL_STATES)!=3 or CAPACITY_FACTOR !=
            {"healthy":1.0,"loss75":0.25,"loss95":0.05}):
        raise RuntimeError("R83L_FROZEN_PHYSICAL_SOURCE_PLAN_CHANGED")
    source={}
    for wheel in WHEELS:
        for initial in range(len(INITIAL_STATES)):
            for reference_mode in REFERENCE_MODES:
                for capacity_mode in CAPACITY_MODES:
                    item=run_native_parameter_case(
                        wheel=wheel, initial=initial,
                        reference_mode=reference_mode,
                        capacity_mode=capacity_mode)
                    key=item["arm"]
                    if key in source:
                        raise RuntimeError("R83L_DUPLICATED_NATIVE_PHYSICAL_ARM")
                    source[key]=item
    if len(source)!=54:
        raise RuntimeError("R83L_ORIGINAL_NATIVE_54_PHYSICAL_CASES_REQUIRED")
    outcomes={}
    for wheel in WHEELS:
        for initial in range(len(INITIAL_STATES)):
            for reference_mode in REFERENCE_MODES:
                context=f"rw{wheel}_initial{initial}_{reference_mode}"
                normal=source[f"{context}_healthy"]["rows"]
                for capacity_mode in ("loss75","loss95"):
                    fault=source[f"{context}_{capacity_mode}"]["rows"]
                    if len(normal)!=31 or len(fault)!=31:
                        raise RuntimeError("R83L_ORIGINAL_NATIVE_31_SAMPLES_REQUIRED")
                    if any(abs(a["time_s"]-b["time_s"])>1e-8 for a,b in zip(normal,fault)):
                        raise RuntimeError("R83L_PHYSICAL_PAIR_CLOCK_DRIFT")
                    s=f"rw.native_speed_rad_s_{wheel}"
                    actual="rw.native_target_applied_torque_nm_QA_ONLY"
                    pre=[(a,b) for a,b in zip(normal,fault)
                         if a["time_s"]<=FAULT_ONSET_S+1e-8]
                    post=[(a,b) for a,b in zip(normal,fault)
                          if a["time_s"]>=FAULT_ONSET_S+STEP_S-1e-8]
                    delta_pre=max(abs(a[s]-b[s]) for a,b in pre)
                    speed_effect=max(abs(a[s]-b[s]) for a,b in post)
                    actual_effect=max(abs(a[actual]-b[actual]) for a,b in post)
                    if delta_pre>1e-8:
                        raise RuntimeError("R83L_FAULT_PHYSICAL_EFFECT_PRECEDES_ONSET")
                    if any(max(abs(a[f"fsw.native_reference_mrp_{j}"] -
                                   b[f"fsw.native_reference_mrp_{j}"])
                               for j in range(3))>1e-12
                           for a,b in zip(normal,fault)):
                        raise RuntimeError("R83L_FAULT_SOURCE_REFERENCE_NOT_MATCHED")
                    if any(max(abs(a[f"rw.native_speed_rad_s_{j}"]-
                                   b[f"rw.native_speed_rad_s_{j}"])
                               for j in range(3))>1e-8
                           for a,b in pre):
                        raise RuntimeError("R83L_NON_TARGET_RW_PRE_FAULT_DRIFT")
                    if abs(source[f"{context}_healthy"][
                            "source_initial_attitude_mrp"][wheel] -
                            INITIAL_STATES[initial][wheel])>1e-15:
                        raise RuntimeError("R83L_PHYSICAL_INITIAL_STATE_DRIFT")
                    cap=NATIVE_WHEEL_LIMIT_NM*CAPACITY_FACTOR[capacity_mode]
                    maximum_actual=max(abs(b[actual]) for a,b in post)
                    if maximum_actual>cap+1e-9:
                        raise RuntimeError("R83L_NATIVE_ACTUATOR_DERATING_NOT_APPLIED")
                    outcomes[context+"_"+capacity_mode]={
                        "context":context,
                        "native_independent_condition_id":f"rw{wheel}_initial{initial}",
                        "source_fault_target_index_QA_ONLY":wheel,
                        "source_initial_state_index":initial,
                        "reference_mode":reference_mode,
                        "capacity_mode":capacity_mode,
                        "original_hardware_limit_nm":NATIVE_WHEEL_LIMIT_NM,
                        "fault_native_cap_after_nm":cap,
                        "pre_event_max_native_rw_speed_difference_rad_s":delta_pre,
                        "post_event_max_native_rw_speed_difference_rad_s":speed_effect,
                        "post_event_max_native_rw_applied_torque_difference_nm":actual_effect,
                        "post_event_fault_native_rw_actual_max_abs_nm":maximum_actual,
                        "actual_physical_speed_manifestation":speed_effect>1e-8,
                        "actual_physical_torque_manifestation":actual_effect>1e-8,
                    }
    all_rows=[r for case in source.values() for r in case["rows"]]
    max_motor_request=max(abs(row[f"rw.native_allocator_request_nm_{j}"])
                          for row in all_rows for j in range(3))
    max_wheel_speed=max(abs(row[f"rw.native_speed_rad_s_{j}"])
                        for row in all_rows for j in range(3))
    max_body_rate=max(abs(row[f"hub.omega_bn_b_rad_s_{j}"])
                      for row in all_rows for j in range(3))
    return {
        "schema":SCHEMA,
        "execution_engine":"GENUINE_BASILISK_REACTIONWHEELSTATEEFFECTOR_AND_FSW",
        "source_native_cases":54,
        "independent_wheel_and_initial_condition_groups":9,
        "reused_healthy_cases":18,
        "paired_fault_vs_nominal_comparisons":36,
        "physical_source_cases":source,
        "native_physical_paired_outcomes":outcomes,
        "source_all_wheels_observed":True,
        "fault_derating_verified_by_real_cpp_state":True,
        "actual_applied_motor_torque_verified_from_rwOutMsgs":True,
        "fault_truth_only_for_offline_physics_QA":True,
        "no_protected_validation_test_final_cases_generated":True,
        "max_original_native_allocator_request_nm":max_motor_request,
        "max_original_native_wheel_speed_rad_s":max_wheel_speed,
        "max_original_native_body_rate_rad_s":max_body_rate,
        "predeclared_provisional_request_speed_envelope_passed":(
            max_motor_request<=FSW_REQUEST_LIMIT_NM+1e-10 and
            max_wheel_speed<=WHEEL_SPEED_OBSERVATION_LIMIT_RAD_S),
        "all_effects_persist_even_if_negative":True,
        "no_machine_learning_trained":True,
        "not_an_independent_hardware_safety_qualification":True,
        "not_a_generalizable_diagnostic_benchmark":True,
        "stable_release_eligible":False,
    }
