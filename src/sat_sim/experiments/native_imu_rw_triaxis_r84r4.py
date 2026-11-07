"""R84-R4 genuine Basilisk experiment exciting all three commanded axes.

The native spacecraft/FSW/RW plant follows the R83m source. The official
Basilisk ImuSensor is observer-only. Four alternating 0.9-degree reference
alternating pulses on each axis extend the run to 54 seconds. Axis order and
physical capacity-fault wheel are separate factors. No proxy dynamics or post-hoc noise.
"""
from __future__ import annotations

import math
from typing import Any

SCHEMA = "sat-sim.r84r4.native-triaxis-wheel.v1"
STEP_S = 0.2
DURATION_S = 54.0
FAULT_PAUSE_S = 2.4
FIRST_POST_FAULT_SAMPLE_S = 2.6
WHEELS = (0, 1, 2)
INITIAL_STATES = (
    (0.0, 0.0, 0.0),
    (0.001, -0.0005, 0.0007),
    (-0.0008, 0.0007, -0.0004),
)
AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
NOMINAL_CAP_NM = 0.020
CAPACITY_NM = {"healthy": 0.020, "loss50": 0.010, "loss75": 0.005}
REFERENCE_MODES = ("triaxis_pulse09x4",)
AXIS_ORDERS = ((0, 1, 2), (1, 2, 0), (2, 0, 1))
NOISE_SIGMAS = (0.0, 0.00005, 0.0001, 0.0002)
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
MAX_REQUEST_NM = 0.020
MAX_WHEEL_SPEED_RAD_S = 900.0
MAX_BODY_RATE_RAD_S = 0.005
PULSE_SCHEDULE = (
    (1.8, 0.9, 1),
    (3.8, 0.0, 1),
    (6.2, -0.9, 2),
    (8.2, 0.0, 2),
    (10.6, 0.9, 3),
    (12.6, 0.0, 3),
    (15.0, -0.9, 4),
    (17.0, 0.0, 4),
)


def _float3(data: Any) -> list[float]:
    return [float(data[i]) for i in range(3)]


def _reference_mrp(angle_deg: float, axis: int) -> list[float]:
    value = math.tan(math.radians(angle_deg) / 4.0) if angle_deg else 0.0
    return [value if index == axis else 0.0 for index in range(3)]


def _finite(values: list[float], code: str) -> None:
    if not all(math.isfinite(value) for value in values):
        raise RuntimeError(code)


def _execute_to(sim: Any, macros: Any, stop_s: float) -> None:
    sim.ConfigureStopTime(macros.sec2nano(stop_s))
    sim.ExecuteSimulation()


def run_native_case(
    *,
    wheel: int,
    axis_order_index: int,
    initial: int,
    reference_mode: str,
    capacity_mode: str,
    gyro_noise_std_rad_s: float,
    seed: int,
) -> dict[str, Any]:
    """Run native physics with every reference axis and one hidden fault wheel."""
    if type(wheel) is not int or wheel not in WHEELS:
        raise ValueError("R84R2_UNREGISTERED_WHEEL")
    if type(axis_order_index) is not int or axis_order_index not in range(len(AXIS_ORDERS)):
        raise ValueError("R84R4_UNREGISTERED_AXIS_ORDER")
    if type(initial) is not int or initial not in range(len(INITIAL_STATES)):
        raise ValueError("R84R2_UNREGISTERED_INITIAL_STATE")
    if reference_mode not in REFERENCE_MODES:
        raise ValueError("R84R2_UNREGISTERED_REFERENCE_MODE")
    if capacity_mode not in CAPACITY_NM:
        raise ValueError("R84R2_UNREGISTERED_CAPACITY_MODE")
    if type(gyro_noise_std_rad_s) not in (int, float) or not math.isfinite(float(gyro_noise_std_rad_s)):
        raise ValueError("R84R2_INVALID_GYRO_NOISE")
    if float(gyro_noise_std_rad_s) not in NOISE_SIGMAS:
        raise ValueError("R84R2_UNREGISTERED_GYRO_NOISE")
    if type(seed) is not int or not 0 < seed < 2**31:
        raise ValueError("R84R2_INVALID_SIGNED_32BIT_SEED")

    requested_cap = CAPACITY_NM[capacity_mode]
    degraded = capacity_mode != "healthy"
    noise_sigma = float(gyro_noise_std_rad_s)
    case_id = (
        f"rw{wheel}_order{axis_order_index}_initial{initial}_{reference_mode}_{capacity_mode}"
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
        raise RuntimeError(f"R84R2_WRONG_BASILISK_DISTRIBUTION:{distribution_version}")

    sim = SimulationBaseClass.SimBaseClass()
    dynamics = sim.CreateNewProcess("r84r2_native_dynamics")
    fsw = sim.CreateNewProcess("r84r2_native_fsw")
    dynamics_task = "r84r2_dynamics"
    fsw_task = "r84r2_fsw"
    dynamics.addTask(sim.CreateNewTask(dynamics_task, macros.sec2nano(STEP_S)))
    fsw.addTask(sim.CreateNewTask(fsw_task, macros.sec2nano(STEP_S)))

    sc = spacecraft.Spacecraft()
    sc.ModelTag = "r84r2_original_native_spacecraft"
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
    wheel_factory.addToSpacecraft("r84r2_native_rw_effector", rw, sc)
    sim.AddModelToTask(dynamics_task, rw, 2)
    rw_config = wheel_factory.getConfigMessage()

    nav = simpleNav.SimpleNav()
    nav.scStateInMsg.subscribeTo(sc.scStateOutMsg)
    sim.AddModelToTask(dynamics_task, nav)

    imu = imuSensor.ImuSensor()
    imu.ModelTag = "r84r2_official_basilisk_imu_observer"
    imu.setBodyToPlatformDCM(0.0, 0.0, 0.0)
    imu.sensorPos_B = [0.0, 0.0, 0.0]
    imu.senRotBias = [0.0, 0.0, 0.0]
    imu.RNGSeed = seed
    zero_matrix = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    imu.PMatrixGyro = [
        [noise_sigma, 0.0, 0.0],
        [0.0, noise_sigma, 0.0],
        [0.0, 0.0, noise_sigma],
    ]
    imu.setAMatrixGyro(zero_matrix)
    imu.scStateInMsg.subscribeTo(sc.scStateOutMsg)
    sim.AddModelToTask(dynamics_task, imu, 3)

    reference = inertial3D.inertial3D()
    reference.ModelTag = "r84r2_native_inertial3d_reference"
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
    motor.ModelTag = "r84r2_native_rw_allocator"
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
    rw_actual_rec = rw.rwOutMsgs[wheel].recorder(sample)
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
    events: list[tuple[float, str, float | None, int | None, int | None]] = []
    for order_position, axis in enumerate(AXIS_ORDERS[axis_order_index]):
        for boundary, angle, pulse in PULSE_SCHEDULE:
            events.append((boundary + 18.0 * order_position, "reference", angle, pulse, axis))
    events.append((FAULT_PAUSE_S, "fault", None, None, None))
    events.sort(key=lambda item: item[0])

    for boundary, event_type, angle, pulse, axis in events:
        _execute_to(sim, macros, boundary)
        if event_type == "fault":
            target = native_wheels[wheel]
            before = float(target.u_max)
            effector_before = float(rw.ReactionWheelData[wheel].u_max)
            if abs(before - NOMINAL_CAP_NM) > 1e-12 or abs(effector_before - NOMINAL_CAP_NM) > 1e-12:
                raise RuntimeError("R84R2_NATIVE_RW_NOMINAL_CAPACITY_MISMATCH")
            if degraded:
                target.u_max = requested_cap
            after = float(target.u_max)
            effector_after = float(rw.ReactionWheelData[wheel].u_max)
            if abs(after - requested_cap) > 1e-12 or abs(effector_after - requested_cap) > 1e-12:
                raise RuntimeError("R84R2_NATIVE_RW_CPP_CAPACITY_READBACK_FAILED")
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
        else:
            assert angle is not None and pulse is not None and axis is not None
            reference.sigma_R0N = _reference_mrp(angle, axis)
            expected = _reference_mrp(angle, axis)
            if any(abs(a - b) > 1e-14 for a, b in zip(_float3(reference.sigma_R0N), expected)):
                raise RuntimeError("R84R2_NATIVE_REFERENCE_READBACK_FAILED")
            receipts.append({
                "event": "FSW_REFERENCE_SET_OR_RESET",
                "scheduler_pause_s": boundary,
                "pulse_index_QA_ONLY": pulse,
                "requested_angle_deg": angle,
                "maneuver_axis_index": axis,
                "axis_order_position": AXIS_ORDERS[axis_order_index].index(axis),
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
    if set(lengths.values()) != {271}:
        raise RuntimeError(f"R84R2_NATIVE_RECORDER_SAMPLE_COUNT_MISMATCH:{lengths}")
    canonical_times = recorder_times["imu"]
    for name, times in recorder_times.items():
        if len(times) != len(canonical_times) or any(
            abs(a - b) > 1e-12 for a, b in zip(canonical_times, times)
        ):
            raise RuntimeError(f"R84R2_NATIVE_RECORDER_CLOCK_MISMATCH:{name}")

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
        _finite(gyro, "R84R2_NATIVE_IMU_NONFINITE")
        _finite(
            reference_mrp + body_request + wheel_request + wheel_speed + sigma + omega + [applied],
            "R84R2_NATIVE_PHYSICS_NONFINITE",
        )
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
    post_fault = [row for row in qa_rows if row["time_s"] >= FIRST_POST_FAULT_SAMPLE_S - 1e-12]
    max_post_fault_applied = max(
        abs(row["rw.native_target_applied_torque_nm_QA_ONLY"]) for row in post_fault
    )
    if max_request > MAX_REQUEST_NM + 1e-10:
        raise RuntimeError("R84R2_PROVISIONAL_REQUEST_ENVELOPE_EXCEEDED")
    if max_speed > MAX_WHEEL_SPEED_RAD_S + 1e-10:
        raise RuntimeError("R84R2_PROVISIONAL_WHEEL_SPEED_ENVELOPE_EXCEEDED")
    if max_body_rate > MAX_BODY_RATE_RAD_S + 1e-10:
        raise RuntimeError("R84R2_PROVISIONAL_BODY_RATE_ENVELOPE_EXCEEDED")
    if max_post_fault_applied > requested_cap + 1e-9:
        raise RuntimeError("R84R2_NATIVE_RW_APPLIED_TORQUE_EXCEEDS_CPP_CAP")

    return {
        "schema": SCHEMA,
        "case_id": case_id,
        "wheel_index_QA_ONLY": wheel,
        "axis_order_index": axis_order_index,
        "axis_order": list(AXIS_ORDERS[axis_order_index]),
        "initial_state_index": initial,
        "initial_attitude_mrp": list(INITIAL_STATES[initial]),
        "reference_mode": reference_mode,
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
        "qa_rows": qa_rows,
    }
