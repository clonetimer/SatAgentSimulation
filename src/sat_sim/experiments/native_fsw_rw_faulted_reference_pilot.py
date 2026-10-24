"""R83k faulted FSW guidance, real native actuator derating pilot.

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

SCHEMA = "sat-sim.r83k.native-fsw-faulted-rw-capability-pilot.v1"
FAULT_ONSET_S = 2.4
FAULTED_RW_INDEX = 0
FAULTED_RW_CAP_NM = 0.0005
FAULT_MECHANISM = "RW0_NATIVE_MAX_MOTOR_TORQUE_REDUCED_97P5_PERCENT"
TRIAL_ARMS = ("zero_healthy","step_healthy","zero_derated","step_derated")
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


def run_native_faulted_reference_arm(arm: str):
    """Run genuine Basilisk physical dynamics with ZERO or scheduled reference.

    Payload reading is native recorder output. An explicit physical response
    check is performed on BOTH arms by the separate experiment comparator.
    """
    if arm not in TRIAL_ARMS:
        raise ValueError("R83K_UNKNOWN_PREREGISTERED_TRIAL_ARM")
    guided=arm.startswith("step_")
    degraded=arm.endswith("_derated")
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
    rw_actual_rec=rw.rwOutMsgs[FAULTED_RW_INDEX].recorder(sample)
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
            rw0=native_wheel_objects[FAULTED_RW_INDEX]
            before=float(rw0.u_max)
            if abs(before-NATIVE_WHEEL_LIMIT_NM)>1e-12:
                raise RuntimeError("R83K_ORIGINAL_WHEEL_CAPACITY_WRONG")
            if degraded:
                rw0.u_max=FAULTED_RW_CAP_NM
            after=float(rw0.u_max)
            expected=FAULTED_RW_CAP_NM if degraded else NATIVE_WHEEL_LIMIT_NM
            native_effector_readback=float(rw.ReactionWheelData[FAULTED_RW_INDEX].u_max)
            if abs(after-expected)>1e-12 or abs(native_effector_readback-expected)>1e-12:
                raise RuntimeError("R83K_ACTUAL_NATIVE_RW_SHARED_CAPACITY_MUTATION_NOT_EFFECTIVE")
            event_receipts.append({
                "fault_scheduler_pause_s":FAULT_ONSET_S,
                "physical_fault_target_rw_index_QA_ONLY":FAULTED_RW_INDEX,
                "fault_actual_native_rw_max_torque_before_nm":before,
                "fault_actual_native_rw_max_torque_after_nm":after,
                "rw_effector_c_backed_readback_nm":native_effector_readback,
                "physical_torque_cap_derated":degraded,
                "real_native_physical_change_not_feature_edit":True})
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
             len(rw_rec.times()),len(sc_rec.times()),
             len(rw_actual_rec.times())]
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
        actual=float(rw_actual_rec.u_current[i])
        if not all(math.isfinite(v) for v in (*ref,*body,*wheel,*speeds,*sigma,*omega,actual)):
            raise RuntimeError("R83J_NONFINITE_NATIVE_MESSAGE")
        rows.append({"time_s":t,
                     **{f"fsw.native_reference_mrp_{j}":ref[j] for j in range(3)},
                     **{f"fsw.body_request_nm_{j}":body[j] for j in range(3)},
                     **{f"rw.native_allocator_request_nm_{j}":wheel[j] for j in range(3)},
                     **{f"rw.native_speed_rad_s_{j}":speeds[j] for j in range(3)},
                     "rw.native_target_applied_torque_nm_QA_ONLY":actual,
                     **{f"hub.sigma_bn_{j}":sigma[j] for j in range(3)},
                     **{f"hub.omega_bn_b_rad_s_{j}":omega[j] for j in range(3)}})
    return {"schema":SCHEMA,"arm":arm,
            "execution_engine":"BASILISK_NATIVE_SC_RW_FSW_NO_PROXY",
            "native_module_chain":[
                "inertial3D","attTrackingError","mrpFeedback","rwMotorTorque",
                "reactionWheelStateEffector","spacecraft"],
            "native_scheduler_was_advanced":True,
            "reference_setpoint_event_receipts":event_receipts,
            "physical_fault_mechanism_QA_ONLY":FAULT_MECHANISM if degraded else "NORMAL",
            "true_native_actuator_capacity_derated":degraded,
            "fault_onset_paused_scheduler_s_QA_ONLY":FAULT_ONSET_S,
            "original_native_rw_hardware_limit_nm":NATIVE_WHEEL_LIMIT_NM,
            "fault_native_rw_capacity_after_nm_QA_ONLY":FAULTED_RW_CAP_NM if degraded else NATIVE_WHEEL_LIMIT_NM,
            "model_inference_uses_truth":False,
            "reference_is_guidance_not_wheel_motor_telecommand":True,
            "assurance":"RESEARCH_ONLY_NOT_FLIGHT_CERTIFIED",
            "rows":rows}




def run_native_faulted_reference_factorial():
    """4 real, same-initial-state native physics runs; no diagnostic model."""
    validate_schedule()
    arms={name:run_native_faulted_reference_arm(name) for name in TRIAL_ARMS}
    lengths={name:len(result["rows"]) for name,result in arms.items()}
    if set(lengths.values())!={31}:
        raise RuntimeError("R83K_NATIVE_4ARM_RECORD_LENGTH_INVALID")
    pairs={}
    for prefix in ("zero","step"):
        healthy=arms[prefix+"_healthy"]["rows"]
        fault=arms[prefix+"_derated"]["rows"]
        if any(abs(a["time_s"]-b["time_s"])>1e-8 for a,b in zip(healthy,fault)):
            raise RuntimeError("R83K_ORIGINAL_PHYSICAL_TIMELINES_MISMATCH")
        before=[(a,b) for a,b in zip(healthy,fault) if a["time_s"]<=FAULT_ONSET_S+1e-8]
        after=[(a,b) for a,b in zip(healthy,fault) if a["time_s"]>=FAULT_ONSET_S+STEP_S-1e-8]
        speed=lambda row:row["rw.native_speed_rad_s_0"]
        torque=lambda row:row["rw.native_target_applied_torque_nm_QA_ONLY"]
        ref=lambda row:row["fsw.native_reference_mrp_0"]
        pre_speed=max(abs(speed(a)-speed(b)) for a,b in before)
        post_speed=max(abs(speed(a)-speed(b)) for a,b in after)
        post_torque=max(abs(torque(a)-torque(b)) for a,b in after)
        if pre_speed>1e-8:
            raise RuntimeError("R83K_PAIRED_FAULT_BEFORE_ONSET_LEAKAGE")
        if any(abs(ref(a)-ref(b))>1e-12 for a,b in zip(healthy,fault)):
            raise RuntimeError("R83K_FAULTED_FSW_REFERENCE_MISMATCH")
        fault_max=max(abs(row["rw.native_target_applied_torque_nm_QA_ONLY"])
                      for row in fault if row["time_s"]>=FAULT_ONSET_S+STEP_S-1e-8)
        if fault_max>FAULTED_RW_CAP_NM+1e-9:
            raise RuntimeError("R83K_NATIVE_PHYSICAL_RW_TORQUE_LIMIT_NOT_APPLIED")
        pairs[prefix]={
            "source_true_fault_cap_nm":FAULTED_RW_CAP_NM,
            "pre_onset_max_native_target_speed_difference_rad_s":pre_speed,
            "post_onset_max_native_target_speed_difference_rad_s":post_speed,
            "post_onset_max_native_target_applied_torque_difference_nm":post_torque,
            "fault_native_applied_torque_max_abs_nm":fault_max,
            "fault_effect_observed_in_true_native_speed":post_speed>1e-8,
            "fault_effect_observed_in_true_native_actuator_torque":post_torque>1e-8,
        }
    all_rows=(row for arm in arms.values() for row in arm["rows"])
    rows=list(all_rows)
    max_command=max(abs(row[f"rw.native_allocator_request_nm_{j}"])
                    for row in rows for j in range(3))
    max_speed=max(abs(row[f"rw.native_speed_rad_s_{j}"])
                  for row in rows for j in range(3))
    return {
        "schema":SCHEMA,
        "actual_engine":"GENUINE_BASILISK_RWMOTORTORQUE_AND_REACTIONWHEELSTATEEFFECTOR",
        "physical_source_arms":arms,
        "physical_pair_outcomes":pairs,
        "physical_native_rw_capacity_write_readback_done":True,
        "reference_nonzero_change_detected":any(
            abs(row["fsw.native_reference_mrp_0"])>1e-7
            for row in arms["step_healthy"]["rows"]),
        "source_native_trials":4,
        "reference_and_fault_are_separate_native_interventions":True,
        "max_requested_native_wheel_torque_nm":max_command,
        "max_native_wheel_speed_rad_s":max_speed,
        "provisional_research_command_and_speed_bounds_passed":(
            max_command<=FSW_REQUEST_LIMIT_NM+1e-10 and
            max_speed<=WHEEL_SPEED_OBSERVATION_LIMIT_RAD_S),
        "independent_hardware_safety_certified":False,
        "healthy_vs_fault_model_trained":False,
        "diagnostic_separability_proven":False,
        "held_out_validation_test_final_data_used":False,
        "stable_release_eligible":False,
    }
