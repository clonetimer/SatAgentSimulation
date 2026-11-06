"""R84-R0 genuine Basilisk IMU observer-only / reaction-wheel fault pilot.

This module executes exactly eight preregistered unique physical arms plus
one deterministic seeded replay control. It uses the original R83m plant,
FSW, scheduler and C-backed RW derating mechanism. The official Basilisk
ImuSensor is an observer only; simpleNav remains the FSW feedback source.

No proxy dynamics, post-hoc noise, model training or inference feature export.
"""
from __future__ import annotations

import math
from typing import Any

SCHEMA = "sat-sim.r84.native-imu-rw-observability.v1"
STEP_S = 0.2
DURATION_S = 6.0
FAULT_PAUSE_S = 2.4
FIRST_POST_FAULT_SAMPLE_S = 2.6
REFERENCE_START_S = 1.8
REFERENCE_END_S = 3.8
WHEEL = 0
INITIAL_MRP = (0.0, 0.0, 0.0)
AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
NOMINAL_CAP_NM = 0.020
FAULT_CAP_NM = 0.005
NOISE_SEED = 331
NOISE_SIGMAS = (0.0, 0.0001)
REFERENCE_MODES = ("zero", "pulse09")
CAPACITY_MODES = ("healthy", "loss75")
OBSERVATION_FIELDS = tuple(f"imu.raw_gyro_rad_s_{axis}" for axis in "xyz")
QA_PHYSICAL_FIELDS = tuple(
    [f"fsw.native_reference_mrp_{i}" for i in range(3)]
    + [f"fsw.body_request_nm_{i}" for i in range(3)]
    + [f"rw.native_allocator_request_nm_{i}" for i in range(3)]
    + [f"rw.native_speed_rad_s_{i}" for i in range(3)]
    + ["rw.native_target_applied_torque_nm_QA_ONLY"]
    + [f"hub.sigma_bn_{i}" for i in range(3)]
    + [f"hub.omega_bn_b_rad_s_{i}" for i in range(3)]
)


def _float3(data: Any) -> list[float]:
    return [float(data[i]) for i in range(3)]


def _reference_mrp(angle_deg: float) -> list[float]:
    import math as _math
    return [math.tan(_math.radians(angle_deg) / 4.0), 0.0, 0.0] if angle_deg else [0.0, 0.0, 0.0]


def _finite(values: list[float], code: str) -> None:
    if not all(math.isfinite(value) for value in values):
        raise RuntimeError(code)


def _execute_to(sim: Any, macros: Any, stop_s: float) -> None:
    sim.ConfigureStopTime(macros.sec2nano(stop_s))
    sim.ExecuteSimulation()


def run_native_case(
    *,
    reference_mode: str,
    capacity_mode: str,
    gyro_noise_std_rad_s: float,
    seed: int = NOISE_SEED,
) -> dict[str, Any]:
    """Execute one genuine native Basilisk physical arm and record raw messages."""
    if reference_mode not in REFERENCE_MODES:
        raise ValueError("R84_UNKNOWN_REFERENCE_MODE")
    if capacity_mode not in CAPACITY_MODES:
        raise ValueError("R84_UNKNOWN_CAPACITY_MODE")
    if type(gyro_noise_std_rad_s) not in (int, float) or not math.isfinite(float(gyro_noise_std_rad_s)):
        raise ValueError("R84_INVALID_GYRO_NOISE")
    if float(gyro_noise_std_rad_s) not in NOISE_SIGMAS:
        raise ValueError("R84_UNREGISTERED_GYRO_NOISE")
    if type(seed) is not int or seed != NOISE_SEED:
        raise ValueError("R84_UNREGISTERED_NOISE_SEED")

    angle_deg = 0.0 if reference_mode == "zero" else 0.9
    requested_cap = NOMINAL_CAP_NM if capacity_mode == "healthy" else FAULT_CAP_NM
    degraded = capacity_mode == "loss75"
    noise_sigma = float(gyro_noise_std_rad_s)
    case_id = f"rw0_initial0_{reference_mode}_{capacity_mode}_gyro_{noise_sigma:.4f}"

    import Basilisk
    from importlib.metadata import version
    from Basilisk.architecture import messaging
    from Basilisk.fswAlgorithms import inertial3D, attTrackingError, mrpFeedback, rwMotorTorque
    from Basilisk.simulation import (
        spacecraft,
        reactionWheelStateEffector,
        simpleNav,
        imuSensor,
    )
    from Basilisk.utilities import SimulationBaseClass, macros, simIncludeRW

    distribution_version = version("bsk")
    if distribution_version != "2.11.0+satfix2":
        raise RuntimeError(f"R84_WRONG_BASILISK_DISTRIBUTION:{distribution_version}")

    sim = SimulationBaseClass.SimBaseClass()
    dynamics = sim.CreateNewProcess("r84_native_dynamics")
    fsw = sim.CreateNewProcess("r84_native_fsw")
    dynamics_task = "r84_dynamics"
    fsw_task = "r84_fsw"
    dynamics.addTask(sim.CreateNewTask(dynamics_task, macros.sec2nano(STEP_S)))
    fsw.addTask(sim.CreateNewTask(fsw_task, macros.sec2nano(STEP_S)))

    sc = spacecraft.Spacecraft()
    sc.ModelTag = "r84_original_native_spacecraft"
    sc.hub.mHub = 12.0
    sc.hub.IHubPntBc_B = [[10.0, 0.0, 0.0], [0.0, 8.0, 0.0], [0.0, 0.0, 6.0]]
    sc.hub.sigma_BNInit = [[float(value)] for value in INITIAL_MRP]
    sc.hub.omega_BN_BInit = [[0.0], [0.0], [0.0]]
    sc.hub.r_CN_NInit = [7_000_000.0, 0.0, 0.0]
    sc.hub.v_CN_NInit = [0.0, 7_500.0, 0.0]
    sim.AddModelToTask(dynamics_task, sc, 1)

    rw = reactionWheelStateEffector.ReactionWheelStateEffector()
    wheel_factory = simIncludeRW.rwFactory()
    native_wheels = []
    for index, axis in enumerate(AXES):
        native_wheels.append(
            wheel_factory.create(
                "Honeywell_HR16",
                list(axis),
                maxMomentum=50.0,
                Omega=100.0 * (index + 1),
                u_max=NOMINAL_CAP_NM,
                label=f"RW{index + 1}",
            )
        )
    wheel_factory.addToSpacecraft("r84_original_rw_effector", rw, sc)
    sim.AddModelToTask(dynamics_task, rw, 2)
    rw_config = wheel_factory.getConfigMessage()

    # Preserve the exact R83m FSW feedback source and scheduling. IMU is
    # connected to the spacecraft only as a parallel observer, never to FSW.
    nav = simpleNav.SimpleNav()
    nav.scStateInMsg.subscribeTo(sc.scStateOutMsg)
    sim.AddModelToTask(dynamics_task, nav)

    imu = imuSensor.ImuSensor()
    imu.ModelTag = "r84_official_basilisk_imu_observer"
    imu.setBodyToPlatformDCM(0.0, 0.0, 0.0)
    imu.sensorPos_B = [0.0, 0.0, 0.0]
    imu.senRotBias = [0.0, 0.0, 0.0]
    imu.RNGSeed = seed
    zero_matrix = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    gyro_matrix = [
        [noise_sigma, 0.0, 0.0],
        [0.0, noise_sigma, 0.0],
        [0.0, 0.0, noise_sigma],
    ]
    imu.PMatrixGyro = gyro_matrix
    imu.setAMatrixGyro(zero_matrix)
    imu.scStateInMsg.subscribeTo(sc.scStateOutMsg)
    sim.AddModelToTask(dynamics_task, imu, 3)

    reference = inertial3D.inertial3D()
    reference.ModelTag = "r84_original_native_inertial3d_reference"
    reference.sigma_R0N = [0.0, 0.0, 0.0]
    sim.AddModelToTask(fsw_task, reference)

    tracking_error = attTrackingError.attTrackingError()
    tracking_error.attRefInMsg.subscribeTo(reference.attRefOutMsg)
    tracking_error.attNavInMsg.subscribeTo(nav.attOutMsg)
    sim.AddModelToTask(fsw_task, tracking_error)

    controller = mrpFeedback.mrpFeedback()
    controller.K = 2.0
    controller.P = 5.0
    controller.Ki = -1.0
    controller.integralLimit = -1.0
    controller.guidInMsg.subscribeTo(tracking_error.attGuidOutMsg)
    vehicle_payload = messaging.VehicleConfigMsgPayload()
    vehicle_payload.ISCPntB_B = [10.0, 0.0, 0.0, 0.0, 8.0, 0.0, 0.0, 0.0, 6.0]
    vehicle_payload.massSC = 12.0
    vehicle_message = messaging.VehicleConfigMsg().write(vehicle_payload)
    controller.vehConfigInMsg.subscribeTo(vehicle_message)
    controller.rwParamsInMsg.subscribeTo(rw_config)
    controller.rwSpeedsInMsg.subscribeTo(rw.rwSpeedOutMsg)
    sim.AddModelToTask(fsw_task, controller)

    motor = rwMotorTorque.rwMotorTorque()
    motor.ModelTag = "r84_original_native_rw_allocator"
    motor.controlAxes_B = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    motor.vehControlInMsg.subscribeTo(controller.cmdTorqueOutMsg)
    motor.rwParamsInMsg.subscribeTo(rw_config)
    sim.AddModelToTask(fsw_task, motor)
    rw.rwMotorCmdInMsg.subscribeTo(motor.rwMotorTorqueOutMsg)

    sample = macros.sec2nano(STEP_S)
    imu_rec = imu.sensorOutMsg.recorder(sample)
    reference_rec = reference.attRefOutMsg.recorder(sample)
    controller_rec = controller.cmdTorqueOutMsg.recorder(sample)
    motor_rec = motor.rwMotorTorqueOutMsg.recorder(sample)
    rw_speed_rec = rw.rwSpeedOutMsg.recorder(sample)
    rw_actual_rec = rw.rwOutMsgs[WHEEL].recorder(sample)
    spacecraft_rec = sc.scStateOutMsg.recorder(sample)
    sim.AddModelToTask(dynamics_task, imu_rec)
    sim.AddModelToTask(fsw_task, reference_rec)
    sim.AddModelToTask(fsw_task, controller_rec)
    sim.AddModelToTask(fsw_task, motor_rec)
    sim.AddModelToTask(dynamics_task, rw_speed_rec)
    sim.AddModelToTask(dynamics_task, rw_actual_rec)
    sim.AddModelToTask(dynamics_task, spacecraft_rec)

    sim.InitializeSimulation()
    receipts: list[dict[str, Any]] = []

    # The scheduler is paused at each event boundary. The reference pulse is
    # set at t=1.8 s and first appears in the 2.0 s recorder. At t=2.4 s,
    # the C-backed RW u_max is changed before the next native integration
    # segment; the first post-fault sample is 2.6 s. The pulse is removed
    # while paused at t=3.8 s and first disappears in the 4.0 s sample.
    _execute_to(sim, macros, REFERENCE_START_S)
    reference.sigma_R0N = _reference_mrp(angle_deg)
    if any(abs(a - b) > 1e-14 for a, b in zip(_float3(reference.sigma_R0N), _reference_mrp(angle_deg))):
        raise RuntimeError("R84_NATIVE_REFERENCE_READBACK_FAILED")
    receipts.append({
        "event": "FSW_REFERENCE_SET",
        "scheduler_pause_s": REFERENCE_START_S,
        "requested_angle_deg": angle_deg,
        "native_mrp_readback": _float3(reference.sigma_R0N),
    })
    _execute_to(sim, macros, FAULT_PAUSE_S)

    target = native_wheels[WHEEL]
    before = float(target.u_max)
    effector_before = float(rw.ReactionWheelData[WHEEL].u_max)
    if abs(before - NOMINAL_CAP_NM) > 1e-12 or abs(effector_before - NOMINAL_CAP_NM) > 1e-12:
        raise RuntimeError("R84_NATIVE_RW_NOMINAL_CAPACITY_MISMATCH")
    if degraded:
        target.u_max = requested_cap
    after = float(target.u_max)
    effector_after = float(rw.ReactionWheelData[WHEEL].u_max)
    if abs(after - requested_cap) > 1e-12 or abs(effector_after - requested_cap) > 1e-12:
        raise RuntimeError("R84_NATIVE_RW_CPP_CAPACITY_READBACK_FAILED")
    receipts.append({
        "event": "NATIVE_CPP_RW_TORQUE_AUTHORITY_CHANGE",
        "scheduler_pause_s": FAULT_PAUSE_S,
        "target_rw_index_QA_ONLY": WHEEL,
        "nominal_cap_before_nm": before,
        "effector_cpp_readback_before_nm": effector_before,
        "cap_after_nm": after,
        "effector_cpp_readback_after_nm": effector_after,
        "physical_fault_truth_QA_ONLY": "loss75" if degraded else "healthy",
        "physical_change_not_feature_edit": True,
    })
    _execute_to(sim, macros, REFERENCE_END_S)

    reference.sigma_R0N = [0.0, 0.0, 0.0]
    if any(abs(value) > 1e-14 for value in _float3(reference.sigma_R0N)):
        raise RuntimeError("R84_NATIVE_REFERENCE_RESET_FAILED")
    receipts.append({
        "event": "FSW_REFERENCE_RESET",
        "scheduler_pause_s": REFERENCE_END_S,
        "native_mrp_readback": _float3(reference.sigma_R0N),
    })
    _execute_to(sim, macros, DURATION_S)

    recorder_times = {
        "imu": [float(value) * macros.NANO2SEC for value in imu_rec.times()],
        "reference": [float(value) * macros.NANO2SEC for value in reference_rec.times()],
        "controller": [float(value) * macros.NANO2SEC for value in controller_rec.times()],
        "motor": [float(value) * macros.NANO2SEC for value in motor_rec.times()],
        "rw_speed": [float(value) * macros.NANO2SEC for value in rw_speed_rec.times()],
        "rw_actual": [float(value) * macros.NANO2SEC for value in rw_actual_rec.times()],
        "spacecraft": [float(value) * macros.NANO2SEC for value in spacecraft_rec.times()],
    }
    lengths = {name: len(times) for name, times in recorder_times.items()}
    if set(lengths.values()) != {31}:
        raise RuntimeError(f"R84_NATIVE_RECORDER_SAMPLE_COUNT_MISMATCH:{lengths}")
    canonical_times = recorder_times["imu"]
    for name, times in recorder_times.items():
        if len(times) != len(canonical_times) or any(
            abs(a - b) > 1e-12 for a, b in zip(canonical_times, times)
        ):
            raise RuntimeError(f"R84_NATIVE_RECORDER_CLOCK_MISMATCH:{name}")

    sensor_rows: list[dict[str, float]] = []
    qa_rows: list[dict[str, float]] = []
    for index, time_s in enumerate(canonical_times):
        gyro = _float3(imu_rec.AngVelPlatform[index])
        reference_mrp = _float3(reference_rec.sigma_RN[index])
        body_request = _float3(controller_rec.torqueRequestBody[index])
        wheel_request = _float3(motor_rec.motorTorque[index])
        wheel_speed = _float3(rw_speed_rec.wheelSpeeds[index])
        sigma = _float3(spacecraft_rec.sigma_BN[index])
        omega = _float3(spacecraft_rec.omega_BN_B[index])
        applied = float(rw_actual_rec.u_current[index])
        _finite(gyro, "R84_NATIVE_IMU_NONFINITE")
        _finite(reference_mrp + body_request + wheel_request + wheel_speed + sigma + omega + [applied],
                "R84_NATIVE_PHYSICS_NONFINITE")
        sensor_rows.append({
            "time_s": time_s,
            OBSERVATION_FIELDS[0]: gyro[0],
            OBSERVATION_FIELDS[1]: gyro[1],
            OBSERVATION_FIELDS[2]: gyro[2],
        })
        qa_rows.append({
            "time_s": time_s,
            **{f"fsw.native_reference_mrp_{i}": reference_mrp[i] for i in range(3)},
            **{f"fsw.body_request_nm_{i}": body_request[i] for i in range(3)},
            **{f"rw.native_allocator_request_nm_{i}": wheel_request[i] for i in range(3)},
            **{f"rw.native_speed_rad_s_{i}": wheel_speed[i] for i in range(3)},
            "rw.native_target_applied_torque_nm_QA_ONLY": applied,
            **{f"hub.sigma_bn_{i}": sigma[i] for i in range(3)},
            **{f"hub.omega_bn_b_rad_s_{i}": omega[i] for i in range(3)},
        })

    # Physical observer envelopes are predeclared research bounds only.
    max_request = max(abs(row[f"rw.native_allocator_request_nm_{i}"])
                      for row in qa_rows for i in range(3))
    max_speed = max(abs(row[f"rw.native_speed_rad_s_{i}"])
                    for row in qa_rows for i in range(3))
    max_body_rate = max(abs(row[f"hub.omega_bn_b_rad_s_{i}"])
                        for row in qa_rows for i in range(3))
    post_fault = [row for row in qa_rows if row["time_s"] >= FIRST_POST_FAULT_SAMPLE_S - 1e-12]
    max_post_fault_applied = max(abs(row["rw.native_target_applied_torque_nm_QA_ONLY"])
                                 for row in post_fault)
    if max_request > 0.020 + 1e-10:
        raise RuntimeError("R84_PROVISIONAL_REQUEST_ENVELOPE_EXCEEDED")
    if max_speed > 900.0 + 1e-10:
        raise RuntimeError("R84_PROVISIONAL_WHEEL_SPEED_ENVELOPE_EXCEEDED")
    if max_body_rate > 0.005 + 1e-10:
        raise RuntimeError("R84_PROVISIONAL_BODY_RATE_ENVELOPE_EXCEEDED")
    if max_post_fault_applied > requested_cap + 1e-9:
        raise RuntimeError("R84_NATIVE_RW_APPLIED_TORQUE_EXCEEDS_CPP_CAP")

    return {
        "schema": SCHEMA,
        "case_id": case_id,
        "reference_mode": reference_mode,
        "reference_angle_deg": angle_deg,
        "capacity_mode_QA_ONLY": capacity_mode,
        "requested_native_capacity_nm_QA_ONLY": requested_cap,
        "gyro_white_noise_std_rad_s": noise_sigma,
        "rng_seed": seed,
        "distribution": distribution_version,
        "basilisk_module_version": getattr(Basilisk, "__version__", "unknown"),
        "execution_engine": "GENUINE_BASILISK_NATIVE_SC_RW_FSW_IMU_NO_PROXY",
        "native_module_chain": [
            "inertial3D", "attTrackingError", "mrpFeedback", "rwMotorTorque",
            "reactionWheelStateEffector", "spacecraft", "simpleNav",
            "imuSensor.ImuSensor",
        ],
        "controller_feedback_source": "simpleNav; IMU is observer-only",
        "imu_source_message": "imu.sensorOutMsg.AngVelPlatform",
        "imu_connected_to": "sc.scStateOutMsg",
        "noise_model": "official ImuSensor.PMatrixGyro with setAMatrixGyro(0)",
        "fault_onset_paused_scheduler_s_QA_ONLY": FAULT_PAUSE_S,
        "first_post_fault_native_sample_s": FIRST_POST_FAULT_SAMPLE_S,
        "native_event_receipts": receipts,
        "recorder_sample_count": lengths,
        "max_abs_allocator_request_nm": max_request,
        "max_abs_wheel_speed_rad_s": max_speed,
        "max_abs_body_rate_rad_s": max_body_rate,
        "max_post_fault_applied_torque_abs_nm_QA_ONLY": max_post_fault_applied,
        "sensor_rows": sensor_rows,
        "qa_rows": qa_rows,
    }
