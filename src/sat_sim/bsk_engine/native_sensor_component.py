"""Independent Basilisk-native sensor graph probes for P9-N2.

These are bounded engineering probes, not production TaskSpec adapters yet.
Each sensor receives an explicit truth/environment message and records the
official Basilisk sensor output. No whole-spacecraft run is sliced or reused.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class NativeStarTrackerTrace:
    times_s: tuple[float, ...]
    q_inrtl_to_case: tuple[tuple[float, float, float, float], ...]
    truth_sigma_bn: tuple[float, float, float]
    truth_omega_bn_b_rad_s: tuple[float, float, float]
    model_source: str = "Basilisk.simulation.starTracker.StarTracker"
    input_source: str = "explicit_SCStatesMsg_truth_fixture"
    claim_scope: str = "official_star_tracker_measurement_from_prescribed_spacecraft_truth_without_optical_star_catalog"


def _finite_vector(values: Sequence[float], size: int, name: str) -> tuple[float, ...]:
    if not isinstance(values, (list, tuple)) or len(values) != size:
        raise ValueError(f"{name}_INVALID")
    result = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in result):
        raise ValueError(f"{name}_NONFINITE")
    return result


def run_native_star_tracker_probe(
    *,
    duration_s: float = 2.0,
    sample_s: float = 0.1,
    truth_sigma_bn: Sequence[float] = (0.05, -0.02, 0.01),
    truth_omega_bn_b_rad_s: Sequence[float] = (0.01, -0.005, 0.002),
    noise_std_rad: float = 0.0,
    random_walk_std_rad_s: float = 0.0,
    seed: int = 0,
    dcm_cb: Sequence[float] = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
) -> NativeStarTrackerTrace:
    """Run the upstream StarTracker against an explicit SCStates truth message.

    This graph deliberately does not claim optical scene, star-catalog,
    centroiding, blinding, or FOV simulation. It isolates the official
    Basilisk attitude measurement model and its recorder output.
    """
    if type(duration_s) not in (int, float) or not math.isfinite(float(duration_s)) or not 0.1 <= float(duration_s) <= 120.0:
        raise ValueError("NATIVE_STAR_TRACKER_DURATION_INVALID")
    if type(sample_s) not in (int, float) or not math.isfinite(float(sample_s)) or not 0.001 <= float(sample_s) <= 1.0:
        raise ValueError("NATIVE_STAR_TRACKER_SAMPLE_INVALID")
    duration = float(duration_s)
    sample = float(sample_s)
    if sample > duration or duration / sample > 20000:
        raise ValueError("NATIVE_STAR_TRACKER_GRID_INVALID")
    sigma = _finite_vector(truth_sigma_bn, 3, "NATIVE_STAR_TRACKER_TRUTH_SIGMA")
    omega = _finite_vector(truth_omega_bn_b_rad_s, 3, "NATIVE_STAR_TRACKER_TRUTH_OMEGA")
    dcm = _finite_vector(dcm_cb, 9, "NATIVE_STAR_TRACKER_DCM")
    if type(seed) is not int or not 0 <= seed <= 2147483647:
        raise ValueError("NATIVE_STAR_TRACKER_SEED_INVALID")
    if type(noise_std_rad) not in (int, float) or not math.isfinite(float(noise_std_rad)) or not 0.0 <= float(noise_std_rad) <= 0.1:
        raise ValueError("NATIVE_STAR_TRACKER_NOISE_INVALID")
    if type(random_walk_std_rad_s) not in (int, float) or not math.isfinite(float(random_walk_std_rad_s)) or not 0.0 <= float(random_walk_std_rad_s) <= 0.1:
        raise ValueError("NATIVE_STAR_TRACKER_WALK_INVALID")

    # Import only after validating inputs. Missing Basilisk must fail closed.
    from Basilisk.architecture import messaging
    from Basilisk.utilities import SimulationBaseClass, macros
    from components.star_tracker.builder import build_star_tracker

    truth = messaging.SCStatesMsgPayload()
    truth.sigma_BN = list(sigma)
    truth.omega_BN_B = list(omega)
    truth.r_BN_N = [7_000_000.0, 0.0, 0.0]
    truth.v_BN_N = [0.0, 7_500.0, 0.0]
    truth.omegaDot_BN_B = [0.0, 0.0, 0.0]
    truth.nonConservativeAccelpntB_B = [0.0, 0.0, 0.0]
    truth_msg = messaging.SCStatesMsg().write(truth)

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("P9N2StarTrackerProcess")
    process.addTask(sim.CreateNewTask("P9N2StarTrackerTask", macros.sec2nano(sample)))
    sensor = build_star_tracker(
        model_tag="P9N2OfficialStarTracker",
        sc_state_msg=truth_msg,
        sigma_sigma_c=float(noise_std_rad),
        sigma_omega_c_rad_s=float(random_walk_std_rad_s),
        dcm_cb=dcm,
    )
    if noise_std_rad > 0.0 or random_walk_std_rad_s > 0.0:
        sensor.RNGSeed = seed
    sim.AddModelToTask("P9N2StarTrackerTask", sensor, None, 10)
    recorder = sensor.sensorOutMsg.recorder(macros.sec2nano(sample))
    sim.AddModelToTask("P9N2StarTrackerTask", recorder, None, 5)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(duration))
    sim.ExecuteSimulation()

    times = tuple(float(value) * 1.0e-9 for value in recorder.times())
    quaternions = tuple(tuple(float(value) for value in row) for row in recorder.qInrtl2Case)
    if not times or len(times) != len(quaternions):
        raise RuntimeError("NATIVE_STAR_TRACKER_RECORDER_MISSING")
    if any(len(row) != 4 for row in quaternions):
        raise RuntimeError("NATIVE_STAR_TRACKER_RECORDER_SHAPE_INVALID")
    flat = [value for row in quaternions for value in row]
    if not all(math.isfinite(value) for value in flat):
        raise RuntimeError("NATIVE_STAR_TRACKER_RECORDER_NONFINITE")
    for quaternion in quaternions:
        norm = math.sqrt(sum(value * value for value in quaternion))
        if not 0.99 <= norm <= 1.01:
            raise RuntimeError("NATIVE_STAR_TRACKER_QUATERNION_NOT_NORMALIZED")

    return NativeStarTrackerTrace(
        times_s=times,
        q_inrtl_to_case=quaternions,
        truth_sigma_bn=sigma,
        truth_omega_bn_b_rad_s=omega,
    )


@dataclass(frozen=True)
class NativeImuTrace:
    times_s: tuple[float, ...]
    angular_velocity_platform_rad_s: tuple[tuple[float, float, float], ...]
    acceleration_platform_m_s2: tuple[tuple[float, float, float], ...]
    truth_omega_bn_b_rad_s: tuple[float, float, float]
    truth_specific_force_b_m_s2: tuple[float, float, float]
    model_source: str = "Basilisk.simulation.imuSensor.ImuSensor"
    input_source: str = "explicit_SCStatesMsg_truth_fixture"
    claim_scope: str = "official_imu_gyro_measurement_from_prescribed_spacecraft_truth_without_navigation_or_closed_loop"


def run_native_imu_probe(
    *,
    duration_s: float = 2.0,
    sample_s: float = 0.1,
    truth_sigma_bn: Sequence[float] = (0.0, 0.0, 0.0),
    truth_omega_bn_b_rad_s: Sequence[float] = (0.01, -0.005, 0.002),
    truth_specific_force_b_m_s2: Sequence[float] = (0.2, -0.1, 0.05),
    sensor_pos_b_m: Sequence[float] = (0.0, 0.0, 0.0),
    dcm_pb: Sequence[float] = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
) -> NativeImuTrace:
    """Run official Basilisk ImuSensor against prescribed spacecraft truth.

    The first N2 IMU probe isolates the gyroscope channel with zero stochastic
    noise and a co-located sensor. Accelerometer excitation and bias/noise
    characterization are separate follow-up tests; no synthetic acceleration
    is inferred from this fixed truth fixture.
    """
    if type(duration_s) not in (int, float) or not math.isfinite(float(duration_s)) or not 0.1 <= float(duration_s) <= 120.0:
        raise ValueError("NATIVE_IMU_DURATION_INVALID")
    if type(sample_s) not in (int, float) or not math.isfinite(float(sample_s)) or not 0.001 <= float(sample_s) <= 1.0:
        raise ValueError("NATIVE_IMU_SAMPLE_INVALID")
    duration = float(duration_s)
    sample = float(sample_s)
    if sample > duration or duration / sample > 20000:
        raise ValueError("NATIVE_IMU_GRID_INVALID")
    sigma = _finite_vector(truth_sigma_bn, 3, "NATIVE_IMU_TRUTH_SIGMA")
    omega = _finite_vector(truth_omega_bn_rad_s, 3, "NATIVE_IMU_TRUTH_OMEGA")
    position = _finite_vector(sensor_pos_b_m, 3, "NATIVE_IMU_SENSOR_POSITION")
    dcm = _finite_vector(dcm_pb, 9, "NATIVE_IMU_DCM")
    if any(abs(value) > 1.0e-9 for value in position):
        raise ValueError("NATIVE_IMU_NONZERO_SENSOR_OFFSET_NOT_YET_SUPPORTED")

    from Basilisk.architecture import messaging
    from Basilisk.utilities import SimulationBaseClass, macros
    from components.imu.builder import build_imu_sensor

    truth = messaging.SCStatesMsgPayload()
    truth.sigma_BN = list(sigma)
    truth.omega_BN_B = list(omega)
    truth.r_BN_N = [7_000_000.0, 0.0, 0.0]
    truth.v_BN_N = [0.0, 7_500.0, 0.0]
    truth.omegaDot_BN_B = [0.0, 0.0, 0.0]
    truth.nonConservativeAccelpntB_B = list(specific_force)
    truth_msg = messaging.SCStatesMsg().write(truth)

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("P9N2ImuProcess")
    process.addTask(sim.CreateNewTask("P9N2ImuTask", macros.sec2nano(sample)))
    sensor = build_imu_sensor(
        model_tag="P9N2OfficialImuSensor",
        sc_state_msg=truth_msg,
        sigma_gyro_rad_s=0.0,
        sigma_accel_m_s2=0.0,
        sigma_gyro_random_walk_rad_s_sqrt_s=0.0,
        sigma_accel_random_walk_m_s2_sqrt_s=0.0,
        sensor_pos_b_m=position,
        dcm_pb=dcm,
    )
    sim.AddModelToTask("P9N2ImuTask", sensor, None, 10)
    recorder = sensor.sensorOutMsg.recorder(macros.sec2nano(sample))
    sim.AddModelToTask("P9N2ImuTask", recorder, None, 5)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(duration))
    sim.ExecuteSimulation()

    times = tuple(float(value) * 1.0e-9 for value in recorder.times())
    angular_velocity = tuple(tuple(float(value) for value in row) for row in recorder.AngVelPlatform)
    acceleration = tuple(tuple(float(value) for value in row) for row in recorder.AccelPlatform)
    if not times or len(times) != len(angular_velocity) or len(times) != len(acceleration):
        raise RuntimeError("NATIVE_IMU_RECORDER_MISSING")
    if any(len(row) != 3 for row in angular_velocity) or any(len(row) != 3 for row in acceleration):
        raise RuntimeError("NATIVE_IMU_RECORDER_SHAPE_INVALID")
    if not all(math.isfinite(value) for row in (*angular_velocity, *acceleration) for value in row):
        raise RuntimeError("NATIVE_IMU_RECORDER_NONFINITE")
    # Identity mounting, zero noise, zero bias and zero lever arm make this a
    # direct truth-to-official-sensor consistency check, not a model estimate.
    for row in angular_velocity:
        if any(abs(row[index] - omega[index]) > 1.0e-6 for index in range(3)):
            raise RuntimeError("NATIVE_IMU_GYRO_TRUTH_MISMATCH")
    for row in acceleration:
        if any(abs(row[index] - specific_force[index]) > 1.0e-6 for index in range(3)):
            raise RuntimeError("NATIVE_IMU_ACCEL_TRUTH_MISMATCH")
    return NativeImuTrace(
        times_s=times,
        angular_velocity_platform_rad_s=angular_velocity,
        acceleration_platform_m_s2=acceleration,
        truth_omega_bn_b_rad_s=omega,
        truth_specific_force_b_m_s2=specific_force,
    )


@dataclass(frozen=True)
class NativeMagnetometerTrace:
    times_s: tuple[float, ...]
    magnetic_field_sensor_t: tuple[tuple[float, float, float], ...]
    truth_magnetic_field_n_t: tuple[float, float, float]
    model_source: str = "Basilisk.simulation.magnetometer.Magnetometer"
    input_source: str = "explicit_MagneticFieldMsg_and_SCStatesMsg_fixtures"
    claim_scope: str = "official_magnetometer_measurement_from_prescribed_uniform_inertial_field_without_geomagnetic_environment_model"


def run_native_magnetometer_probe(
    *,
    duration_s: float = 2.0,
    sample_s: float = 0.1,
    truth_magnetic_field_n_t: Sequence[float] = (2.0e-5, -1.0e-5, 3.0e-5),
    truth_sigma_bn: Sequence[float] = (0.0, 0.0, 0.0),
    bias_tesla: Sequence[float] = (0.0, 0.0, 0.0),
    scale_factor: float = 1.0,
) -> NativeMagnetometerTrace:
    """Run official Basilisk Magnetometer against an explicit uniform field.

    The prescribed field is an input fixture, not an Earth magnetic-field
    model. Identity spacecraft attitude and sensor mounting make the expected
    sensor-frame output directly comparable to the input vector.
    """
    if type(duration_s) not in (int, float) or not math.isfinite(float(duration_s)) or not 0.1 <= float(duration_s) <= 120.0:
        raise ValueError("NATIVE_MAG_DURATION_INVALID")
    if type(sample_s) not in (int, float) or not math.isfinite(float(sample_s)) or not 0.001 <= float(sample_s) <= 1.0:
        raise ValueError("NATIVE_MAG_SAMPLE_INVALID")
    duration, sample = float(duration_s), float(sample_s)
    if sample > duration or duration / sample > 20000:
        raise ValueError("NATIVE_MAG_GRID_INVALID")
    field = _finite_vector(truth_magnetic_field_n_t, 3, "NATIVE_MAG_FIELD")
    sigma = _finite_vector(truth_sigma_bn, 3, "NATIVE_MAG_TRUTH_SIGMA")
    bias = _finite_vector(bias_tesla, 3, "NATIVE_MAG_BIAS")
    if type(scale_factor) not in (int, float) or not math.isfinite(float(scale_factor)) or not 0.0 < float(scale_factor) <= 2.0:
        raise ValueError("NATIVE_MAG_SCALE_INVALID")

    from Basilisk.architecture import messaging
    from Basilisk.utilities import SimulationBaseClass, macros
    from components.magnetometer.builder import build_magnetometer

    truth = messaging.SCStatesMsgPayload()
    truth.sigma_BN = list(sigma)
    truth.omega_BN_B = [0.0, 0.0, 0.0]
    truth.r_BN_N = [7_000_000.0, 0.0, 0.0]
    truth.v_BN_N = [0.0, 7_500.0, 0.0]
    truth.omegaDot_BN_B = [0.0, 0.0, 0.0]
    truth.nonConservativeAccelpntB_B = [0.0, 0.0, 0.0]
    state_msg = messaging.SCStatesMsg().write(truth)
    field_payload = messaging.MagneticFieldMsgPayload()
    field_payload.magField_N = list(field)
    field_msg = messaging.MagneticFieldMsg().write(field_payload)

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("P9N2MagnetometerProcess")
    process.addTask(sim.CreateNewTask("P9N2MagnetometerTask", macros.sec2nano(sample)))
    sensor = build_magnetometer(
        model_tag="P9N2OfficialMagnetometer",
        sc_state_msg=state_msg,
        mag_field_msg=field_msg,
        sigma_mag_tesla=0.0,
        sigma_bias_tesla=0.0,
        bias_tesla=bias,
        scale_factor=float(scale_factor),
        mounting_matrix_sb=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    )
    sim.AddModelToTask("P9N2MagnetometerTask", sensor, None, 10)
    recorder = sensor.tamDataOutMsg.recorder(macros.sec2nano(sample))
    sim.AddModelToTask("P9N2MagnetometerTask", recorder, None, 5)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(duration))
    sim.ExecuteSimulation()

    times = tuple(float(value) * 1.0e-9 for value in recorder.times())
    measured = tuple(tuple(float(value) for value in row) for row in recorder.tam_S)
    if not times or len(times) != len(measured):
        raise RuntimeError("NATIVE_MAG_RECORDER_MISSING")
    if any(len(row) != 3 for row in measured):
        raise RuntimeError("NATIVE_MAG_RECORDER_SHAPE_INVALID")
    if not all(math.isfinite(value) for row in measured for value in row):
        raise RuntimeError("NATIVE_MAG_RECORDER_NONFINITE")
    expected = tuple(field[index] * float(scale_factor) + bias[index] for index in range(3))
    for row in measured:
        if any(abs(row[index] - expected[index]) > 1.0e-10 for index in range(3)):
            raise RuntimeError("NATIVE_MAG_FIELD_TRUTH_MISMATCH")
    return NativeMagnetometerTrace(times_s=times, magnetic_field_sensor_t=measured, truth_magnetic_field_n_t=field)


@dataclass(frozen=True)
class NativeCoarseSunSensorTrace:
    times_s: tuple[float, ...]
    output_intensity: tuple[float, ...]
    truth_sun_position_n_m: tuple[float, float, float]
    illumination_factor: float
    model_source: str = "Basilisk.simulation.coarseSunSensor.CoarseSunSensor"
    input_source: str = "explicit_SpicePlanetStateMsg_EclipseMsg_and_SCStatesMsg_fixtures"
    claim_scope: str = "official_coarse_sun_sensor_cosine_response_from_prescribed_sun_and_eclipse_inputs_without_ephemeris_or_orbit_environment"


def run_native_coarse_sun_sensor_probe(
    *,
    duration_s: float = 2.0,
    sample_s: float = 0.1,
    truth_sun_position_n_m: Sequence[float] = (149_597_870_700.0, 0.0, 0.0),
    truth_sigma_bn: Sequence[float] = (0.0, 0.0, 0.0),
    n_hat_b: Sequence[float] = (1.0, 0.0, 0.0),
    fov_rad: float = math.pi,
    illumination_factor: float = 1.0,
) -> NativeCoarseSunSensorTrace:
    """Run official Basilisk CoarseSunSensor with prescribed Sun/Eclipse inputs.

    This isolates the native cosine/FOV response. The Sun position and eclipse
    factor are fixtures; this probe does not claim SPICE ephemeris, orbit
    propagation, occultation geometry or a full solar-array environment.
    """
    if type(duration_s) not in (int, float) or not math.isfinite(float(duration_s)) or not 0.1 <= float(duration_s) <= 120.0:
        raise ValueError("NATIVE_CSS_DURATION_INVALID")
    if type(sample_s) not in (int, float) or not math.isfinite(float(sample_s)) or not 0.001 <= float(sample_s) <= 1.0:
        raise ValueError("NATIVE_CSS_SAMPLE_INVALID")
    duration, sample = float(duration_s), float(sample_s)
    if sample > duration or duration / sample > 20000:
        raise ValueError("NATIVE_CSS_GRID_INVALID")
    sun_position = _finite_vector(truth_sun_position_n_m, 3, "NATIVE_CSS_SUN_POSITION")
    sigma = _finite_vector(truth_sigma_bn, 3, "NATIVE_CSS_TRUTH_SIGMA")
    boresight = _finite_vector(n_hat_b, 3, "NATIVE_CSS_BORESIGHT")
    boresight_norm = math.sqrt(sum(value * value for value in boresight))
    if not 0.999 <= boresight_norm <= 1.001:
        raise ValueError("NATIVE_CSS_BORESIGHT_NOT_UNIT")
    if type(fov_rad) not in (int, float) or not math.isfinite(float(fov_rad)) or not 0.0 < float(fov_rad) <= math.pi:
        raise ValueError("NATIVE_CSS_FOV_INVALID")
    if type(illumination_factor) not in (int, float) or not math.isfinite(float(illumination_factor)) or not 0.0 <= float(illumination_factor) <= 1.0:
        raise ValueError("NATIVE_CSS_ILLUMINATION_INVALID")

    from Basilisk.architecture import messaging
    from Basilisk.utilities import SimulationBaseClass, macros
    from components.sun_sensor.builder import build_coarse_sun_sensor

    truth = messaging.SCStatesMsgPayload()
    truth.sigma_BN = list(sigma)
    truth.omega_BN_B = [0.0, 0.0, 0.0]
    truth.r_BN_N = [7_000_000.0, 0.0, 0.0]
    truth.v_BN_N = [0.0, 7_500.0, 0.0]
    truth.omegaDot_BN_B = [0.0, 0.0, 0.0]
    truth.nonConservativeAccelpntB_B = [0.0, 0.0, 0.0]
    state_msg = messaging.SCStatesMsg().write(truth)

    sun_payload = messaging.SpicePlanetStateMsgPayload()
    sun_payload.PositionVector = list(sun_position)
    sun_payload.VelocityVector = [0.0, 0.0, 0.0]
    sun_payload.PlanetName = "sun"
    sun_msg = messaging.SpicePlanetStateMsg().write(sun_payload)
    eclipse_payload = messaging.EclipseMsgPayload()
    eclipse_payload.illuminationFactor = float(illumination_factor)
    eclipse_msg = messaging.EclipseMsg().write(eclipse_payload)

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("P9N2CoarseSunSensorProcess")
    process.addTask(sim.CreateNewTask("P9N2CoarseSunSensorTask", macros.sec2nano(sample)))
    sensor = build_coarse_sun_sensor(
        model_tag="P9N2OfficialCoarseSunSensor",
        sc_state_msg=state_msg,
        sun_in_msg=sun_msg,
        eclipse_in_msg=eclipse_msg,
        n_hat_b=boresight,
        fov_rad=float(fov_rad),
        scale_factor=1.0,
        bias=0.0,
        noise_std=0.0,
        walk_bounds=-1.0,
        fault_noise_std=0.0,
        k_power=2.0,
    )
    sim.AddModelToTask("P9N2CoarseSunSensorTask", sensor, None, 10)
    recorder = sensor.cssDataOutMsg.recorder(macros.sec2nano(sample))
    sim.AddModelToTask("P9N2CoarseSunSensorTask", recorder, None, 5)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(duration))
    sim.ExecuteSimulation()

    times = tuple(float(value) * 1.0e-9 for value in recorder.times())
    intensity = tuple(float(value) for value in recorder.OutputData)
    if not times or len(times) != len(intensity):
        raise RuntimeError("NATIVE_CSS_RECORDER_MISSING")
    if not all(math.isfinite(value) for value in intensity):
        raise RuntimeError("NATIVE_CSS_RECORDER_NONFINITE")
    if any(value < -1.0e-9 or value > 1.0e6 for value in intensity):
        raise RuntimeError("NATIVE_CSS_OUTPUT_OUT_OF_RANGE")
    return NativeCoarseSunSensorTrace(
        times_s=times,
        output_intensity=intensity,
        truth_sun_position_n_m=sun_position,
        illumination_factor=float(illumination_factor),
    )
