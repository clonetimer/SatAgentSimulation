"""R84-R3 genuine Basilisk multi-channel RW/IMU telemetry pilot.

The original R83m spacecraft/FSW/RW plant is retained. The official Basilisk
ImuSensor is a noisy sensor; wheel speed is recorded from the native RW
feedback message as an ideal upper-bound channel (no encoder noise model).
FSW command torque and attitude tracking error are operational channels.
Actual applied torque and hub truth remain QA-only.
"""
from __future__ import annotations

import math
from typing import Any

SCHEMA = "sat-sim.r84r3.native-multichannel-pilot.v1"
STEP_S = 0.2
DURATION_S = 6.0
FAULT_PAUSE_S = 2.4
FIRST_POST_FAULT_SAMPLE_S = 2.6
REFERENCE_START_S = 1.8
REFERENCE_END_S = 3.8
WHEELS = (0, 1, 2)
INITIAL_STATES = (
    (0.0, 0.0, 0.0),
    (0.001, -0.0005, 0.0007),
    (-0.0008, 0.0007, -0.0004),
)
AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
NOMINAL_CAP_NM = 0.020
CAPACITY_NM = {"healthy": 0.020, "loss50": 0.010, "loss75": 0.005}
REFERENCE_ANGLE_DEG = {"zero": 0.0, "pulse05": 0.5, "pulse09": 0.9}
REFERENCE_MODES = ("zero", "pulse05", "pulse09")
CAPACITY_MODES = ("healthy", "loss50", "loss75")
NOISE_SIGMAS = (0.0, 0.00005, 0.0001, 0.0002)
OBSERVATION_FIELDS = tuple(f"imu.raw_gyro_rad_s_{axis}" for axis in "xyz")
TELEMETRY_FIELDS = (
    *OBSERVATION_FIELDS,
    "sensor.adcs.rw.speed_rad_s_0",
    "sensor.adcs.rw.speed_rad_s_1",
    "sensor.adcs.rw.speed_rad_s_2",
    "command.adcs.rw.motor_torque_nm_0",
    "command.adcs.rw.motor_torque_nm_1",
    "command.adcs.rw.motor_torque_nm_2",
    "estimate.adcs.pointing_error_deg",
)
QA_PHYSICAL_FIELDS = tuple(
    [f"fsw.native_reference_mrp_{i}" for i in range(3)]
    + [f"fsw.body_request_nm_{i}" for i in range(3)]
    + [f"rw.native_allocator_request_nm_{i}" for i in range(3)]
    + [f"rw.native_speed_rad_s_{i}" for i in range(3)]
    + ["rw.native_target_applied_torque_nm_QA_ONLY"]
    + [f"hub.sigma_bn_{i}" for i in range(3)]
    + [f"hub.omega_bn_b_rad_s_{i}" for i in range(3)]
)
MAX_REQUEST_NM = 0.020
MAX_WHEEL_SPEED_RAD_S = 900.0
MAX_BODY_RATE_RAD_S = 0.005


def _float3(data: Any) -> list[float]:
    return [float(data[i]) for i in range(3)]


def _reference_mrp(angle_deg: float, wheel: int) -> list[float]:
    value = math.tan(math.radians(angle_deg) / 4.0) if angle_deg else 0.0
    return [value if index == wheel else 0.0 for index in range(3)]


def _finite(values: list[float], code: str) -> None:
    if not all(math.isfinite(value) for value in values):
        raise RuntimeError(code)


def _execute_to(sim: Any, macros: Any, stop_s: float) -> None:
    sim.ConfigureStopTime(macros.sec2nano(stop_s))
    sim.ExecuteSimulation()


def run_native_case(
    *,
    wheel: int,
    initial: int,
    reference_mode: str,
    capacity_mode: str,
    gyro_noise_std_rad_s: float,
    seed: int,
) -> dict[str, Any]:
    """Run one real Basilisk native case and record official raw IMU messages."""
    if type(wheel) is not int or wheel not in WHEELS:
        raise ValueError("R84R3_UNREGISTERED_WHEEL")
    if type(initial) is not int or initial not in range(len(INITIAL_STATES)):
        raise ValueError("R84R3_UNREGISTERED_INITIAL_STATE")
    if reference_mode not in REFERENCE_MODES:
        raise ValueError("R84R3_UNREGISTERED_REFERENCE_MODE")
    if capacity_mode not in CAPACITY_MODES:
        raise ValueError("R84R3_UNREGISTERED_CAPACITY_MODE")
    if type(gyro_noise_std_rad_s) not in (int, float) or not math.isfinite(float(gyro_noise_std_rad_s)):
        raise ValueError("R84R3_INVALID_GYRO_NOISE")
    if float(gyro_noise_std_rad_s) not in NOISE_SIGMAS:
        raise ValueError("R84R3_UNREGISTERED_GYRO_NOISE")
    if type(seed) is not int or not 0 < seed < 2**31:
        raise ValueError("R84R3_INVALID_SIGNED_32BIT_SEED")

    angle_deg = REFERENCE_ANGLE_DEG[reference_mode]
    requested_cap = CAPACITY_NM[capacity_mode]
    degraded = capacity_mode != "healthy"
    noise_sigma = float(gyro_noise_std_rad_s)
    case_id = (
        f"rw{wheel}_initial{initial}_{reference_mode}_{capacity_mode}"
        f"_gyro_{noise_sigma:.5f}_seed_{seed}"
    )

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
        raise RuntimeError(f"R84R3_WRONG_BASILISK_DISTRIBUTION:{distribution_version}")

    sim = SimulationBaseClass.SimBaseClass()
    dynamics = sim.CreateNewProcess("r84r3_native_dynamics")
    fsw = sim.CreateNewProcess("r84r3_native_fsw")
    dynamics_task = "r84r3_dynamics"
    fsw_task = "r84r3_fsw"
    dynamics.addTask(sim.CreateNewTask(dynamics_task, macros.sec2nano(STEP_S)))
    fsw.addTask(sim.CreateNewTask(fsw_task, macros.sec2nano(STEP_S)))

    sc = spacecraft.Spacecraft()
    sc.ModelTag = "r84r3_original_native_spacecraft"
    sc.hub.mHub = 12.0
    sc.hub.IHubPntBc_B = [[10.0, 0.0, 0.0], [0.0, 8.0, 0.0], [0.0, 0.0, 6.0]]
    sc.hub.sigma_BNInit = [[float(value)] for value in INITIAL_STATES[initial]]
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
    wheel_factory.addToSpacecraft("r84r3_native_rw_effector", rw, sc)
    sim.AddModelToTask(dynamics_task, rw, 2)
    rw_config = wheel_factory.getConfigMessage()

    # Preserve R83m's exact FSW feedback chain. The official IMU is not
    # connected to the controller and cannot alter the closed-loop feedback.
    nav = simpleNav.SimpleNav()
    nav.scStateInMsg.subscribeTo(sc.scStateOutMsg)
    sim.AddModelToTask(dynamics_task, nav)

    imu = imuSensor.ImuSensor()
    imu.ModelTag = "r84r3_official_basilisk_imu_observer"
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
    reference.ModelTag = "r84r3_native_inertial3d_reference"
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
    motor.ModelTag = "r84r3_native_rw_allocator"
    motor.controlAxes_B = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    motor.vehControlInMsg.subscribeTo(controller.cmdTorqueOutMsg)
    motor.rwParamsInMsg.subscribeTo(rw_config)
    sim.AddModelToTask(fsw_task, motor)
    rw.rwMotorCmdInMsg.subscribeTo(motor.rwMotorTorqueOutMsg)

    sample = macros.sec2nano(STEP_S)
    imu_rec = imu.sensorOutMsg.recorder(sample)
    reference_rec = reference.attRefOutMsg.recorder(sample)
    tracking_rec = tracking_error.attGuidOutMsg.recorder(sample)
    controller_rec = controller.cmdTorqueOutMsg.recorder(sample)
    motor_rec = motor.rwMotorTorqueOutMsg.recorder(sample)
    rw_speed_rec = rw.rwSpeedOutMsg.recorder(sample)
    rw_actual_rec = rw.rwOutMsgs[wheel].recorder(sample)
    spacecraft_rec = sc.scStateOutMsg.recorder(sample)
    sim.AddModelToTask(dynamics_task, imu_rec)
    sim.AddModelToTask(fsw_task, reference_rec)
    sim.AddModelToTask(fsw_task, tracking_rec)
    sim.AddModelToTask(fsw_task, controller_rec)
    sim.AddModelToTask(fsw_task, motor_rec)
    sim.AddModelToTask(dynamics_task, rw_speed_rec)
    sim.AddModelToTask(dynamics_task, rw_actual_rec)
    sim.AddModelToTask(dynamics_task, spacecraft_rec)

    sim.InitializeSimulation()
    receipts: list[dict[str, Any]] = []

    # Native guidance reference is changed while the scheduler is paused at
    # 1.8 s; the selected wheel axis receives the fixed reference pulse.
    _execute_to(sim, macros, REFERENCE_START_S)
    requested_reference = _reference_mrp(angle_deg, wheel)
    reference.sigma_R0N = requested_reference
    if any(abs(a - b) > 1e-14 for a, b in zip(_float3(reference.sigma_R0N), requested_reference)):
        raise RuntimeError("R84R3_NATIVE_REFERENCE_READBACK_FAILED")
    receipts.append({
        "event": "FSW_REFERENCE_SET",
        "scheduler_pause_s": REFERENCE_START_S,
        "requested_angle_deg": angle_deg,
        "target_wheel_index_QA_ONLY": wheel,
        "native_mrp_readback": _float3(reference.sigma_R0N),
    })

    # The real C++ shared wheel configuration is mutated only at the paused
    # 2.4 s boundary. Healthy is a no-change control with a positive readback.
    _execute_to(sim, macros, FAULT_PAUSE_S)
    target = native_wheels[wheel]
    before = float(target.u_max)
    effector_before = float(rw.ReactionWheelData[wheel].u_max)
    if abs(before - NOMINAL_CAP_NM) > 1e-12 or abs(effector_before - NOMINAL_CAP_NM) > 1e-12:
        raise RuntimeError("R84R3_NATIVE_RW_NOMINAL_CAPACITY_MISMATCH")
    if degraded:
        target.u_max = requested_cap
    after = float(target.u_max)
    effector_after = float(rw.ReactionWheelData[wheel].u_max)
    if abs(after - requested_cap) > 1e-12 or abs(effector_after - requested_cap) > 1e-12:
        raise RuntimeError("R84R3_NATIVE_RW_CPP_CAPACITY_READBACK_FAILED")
    receipts.append({
        "event": "NATIVE_CPP_RW_CAPACITY_VERIFICATION",
        "capacity_changed": degraded,
        "scheduler_pause_s": FAULT_PAUSE_S,
        "target_rw_index_QA_ONLY": wheel,
        "nominal_cap_before_nm": before,
        "effector_cpp_readback_before_nm": effector_before,
        "cap_after_nm": after,
        "effector_cpp_readback_after_nm": effector_after,
        "physical_fault_truth_QA_ONLY": capacity_mode,
        "physical_change_not_feature_edit": True,
    })
    _execute_to(sim, macros, REFERENCE_END_S)

    reference.sigma_R0N = [0.0, 0.0, 0.0]
    if any(abs(value) > 1e-14 for value in _float3(reference.sigma_R0N)):
        raise RuntimeError("R84R3_NATIVE_REFERENCE_RESET_FAILED")
    receipts.append({
        "event": "FSW_REFERENCE_RESET",
        "scheduler_pause_s": REFERENCE_END_S,
        "native_mrp_readback": _float3(reference.sigma_R0N),
    })
    _execute_to(sim, macros, DURATION_S)

    recorder_times = {
        "imu": [float(value) * macros.NANO2SEC for value in imu_rec.times()],
        "reference": [float(value) * macros.NANO2SEC for value in reference_rec.times()],
        "tracking": [float(value) * macros.NANO2SEC for value in tracking_rec.times()],
        "controller": [float(value) * macros.NANO2SEC for value in controller_rec.times()],
        "motor": [float(value) * macros.NANO2SEC for value in motor_rec.times()],
        "rw_speed": [float(value) * macros.NANO2SEC for value in rw_speed_rec.times()],
        "rw_actual": [float(value) * macros.NANO2SEC for value in rw_actual_rec.times()],
        "spacecraft": [float(value) * macros.NANO2SEC for value in spacecraft_rec.times()],
    }
    lengths = {name: len(times) for name, times in recorder_times.items()}
    if set(lengths.values()) != {31}:
        raise RuntimeError(f"R84R3_NATIVE_RECORDER_SAMPLE_COUNT_MISMATCH:{lengths}")
    canonical_times = recorder_times["imu"]
    for name, times in recorder_times.items():
        if len(times) != len(canonical_times) or any(
            abs(a - b) > 1e-12 for a, b in zip(canonical_times, times)
        ):
            raise RuntimeError(f"R84R3_NATIVE_RECORDER_CLOCK_MISMATCH:{name}")

    sensor_rows: list[dict[str, float]] = []
    telemetry_rows: list[dict[str, float]] = []
    qa_rows: list[dict[str, float]] = []
    for index, time_s in enumerate(canonical_times):
        gyro = _float3(imu_rec.AngVelPlatform[index])
        attitude_error_mrp = _float3(tracking_rec.sigma_BR[index])
        pointing_error_deg = 4.0 * math.degrees(math.atan(math.sqrt(sum(value * value for value in attitude_error_mrp))))
        reference_mrp = _float3(reference_rec.sigma_RN[index])
        body_request = _float3(controller_rec.torqueRequestBody[index])
        wheel_request = _float3(motor_rec.motorTorque[index])
        wheel_speed = _float3(rw_speed_rec.wheelSpeeds[index])
        sigma = _float3(spacecraft_rec.sigma_BN[index])
        omega = _float3(spacecraft_rec.omega_BN_B[index])
        applied = float(rw_actual_rec.u_current[index])
        _finite(gyro, "R84R3_NATIVE_IMU_NONFINITE")
        _finite(
            attitude_error_mrp + reference_mrp + body_request + wheel_request + wheel_speed + sigma + omega + [applied, pointing_error_deg],
            "R84R3_NATIVE_PHYSICS_NONFINITE",
        )
        sensor_rows.append({
            "time_s": time_s,
            OBSERVATION_FIELDS[0]: gyro[0],
            OBSERVATION_FIELDS[1]: gyro[1],
            OBSERVATION_FIELDS[2]: gyro[2],
        })
        telemetry_rows.append({
            "time_s": time_s,
            OBSERVATION_FIELDS[0]: gyro[0],
            OBSERVATION_FIELDS[1]: gyro[1],
            OBSERVATION_FIELDS[2]: gyro[2],
            **{f"sensor.adcs.rw.speed_rad_s_{i}": wheel_speed[i] for i in range(3)},
            **{f"command.adcs.rw.motor_torque_nm_{i}": wheel_request[i] for i in range(3)},
            "estimate.adcs.pointing_error_deg": pointing_error_deg,
        })
        qa_rows.append({
            "time_s": time_s,
            "fsw.pointing_error_deg_QA_ONLY": pointing_error_deg,
            **{f"fsw.native_reference_mrp_{i}": reference_mrp[i] for i in range(3)},
            **{f"fsw.body_request_nm_{i}": body_request[i] for i in range(3)},
            **{f"rw.native_allocator_request_nm_{i}": wheel_request[i] for i in range(3)},
            **{f"rw.native_speed_rad_s_{i}": wheel_speed[i] for i in range(3)},
            "rw.native_target_applied_torque_nm_QA_ONLY": applied,
            **{f"hub.sigma_bn_{i}": sigma[i] for i in range(3)},
            **{f"hub.omega_bn_b_rad_s_{i}": omega[i] for i in range(3)},
        })

    max_request = max(
        abs(row[f"rw.native_allocator_request_nm_{i}"])
        for row in qa_rows for i in range(3)
    )
    max_speed = max(
        abs(row[f"rw.native_speed_rad_s_{i}"])
        for row in qa_rows for i in range(3)
    )
    max_body_rate = max(
        abs(row[f"hub.omega_bn_b_rad_s_{i}"])
        for row in qa_rows for i in range(3)
    )
    post_fault = [
        row for row in qa_rows
        if row["time_s"] >= FIRST_POST_FAULT_SAMPLE_S - 1e-12
    ]
    max_post_fault_applied = max(
        abs(row["rw.native_target_applied_torque_nm_QA_ONLY"])
        for row in post_fault
    )
    if max_request > MAX_REQUEST_NM + 1e-10:
        raise RuntimeError("R84R3_PROVISIONAL_REQUEST_ENVELOPE_EXCEEDED")
    if max_speed > MAX_WHEEL_SPEED_RAD_S + 1e-10:
        raise RuntimeError("R84R3_PROVISIONAL_WHEEL_SPEED_ENVELOPE_EXCEEDED")
    if max_body_rate > MAX_BODY_RATE_RAD_S + 1e-10:
        raise RuntimeError("R84R3_PROVISIONAL_BODY_RATE_ENVELOPE_EXCEEDED")
    if max_post_fault_applied > requested_cap + 1e-9:
        raise RuntimeError("R84R3_NATIVE_RW_APPLIED_TORQUE_EXCEEDS_CPP_CAP")

    return {
        "schema": SCHEMA,
        "case_id": case_id,
        "wheel_index_QA_ONLY": wheel,
        "initial_state_index": initial,
        "initial_attitude_mrp": list(INITIAL_STATES[initial]),
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
            "reactionWheelStateEffector", "spacecraft", "simpleNav", "imuSensor.ImuSensor",
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
        "telemetry_rows": telemetry_rows,
        "telemetry_fields": ["time_s", *TELEMETRY_FIELDS],
        "telemetry_provenance": {
            "imu": "official Basilisk ImuSensor.sensorOutMsg.AngVelPlatform with configured native gyro noise",
            "rw_speed": "reactionWheelStateEffector.rwSpeedOutMsg.wheelSpeeds; ideal native state feedback upper bound, no encoder noise or quantization model",
            "rw_command_torque": "native FSW rwMotorTorqueOutMsg.motorTorque command; command channel, not delivered torque",
            "pointing_error": "native attTrackingError.attGuidOutMsg.sigma_BR converted to degrees",
            "excluded_from_model_inputs": {
                "actual_applied_torque": "rwOutMsgs.u_current is physics QA only, not an onboard sensor measurement",
                "effective_max_torque": "C++ actuator capacity truth is QA only",
                "motor_current": "no native electrical current sensor model; I=tau/Kt is a derived command estimate, not measured current",
            },
        },
        "qa_rows": qa_rows,
    }
