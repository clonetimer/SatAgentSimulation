"""ADCS subsystem schemas."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from components.imu.builder import ImuConfig, ImuBiasState
from components.star_tracker.builder import StarTrackerConfig
from components.sun_sensor.builder import SunSensorConfig
from components.magnetometer.builder import MagnetometerConfig
from components.reaction_wheel import ReactionWheelDynamicsConfig, ReactionWheelState
from components.cmg import SingleGimbalCmgConfig, SingleGimbalCmgState
from components.mtb import MtbConfig, MtbState

Vector3 = tuple[float, float, float]
ObservationName = str
SensorName = str


@dataclass(frozen=True)
class AdcsReference:
    sigma_rn: Vector3 = (0.0, 0.0, 0.0)
    omega_rn_b_rad_s: Vector3 = (0.0, 0.0, 0.0)
    description: str = "inertial hold"


@dataclass(frozen=True)
class AdcsControlGains:
    k_sigma_nm: float = 0.12
    p_rate_nms: float = 0.08
    integral_gain: float = 0.0
    integral_limit: float = 0.0
    torque_limit_nm: float = 0.03


@dataclass(frozen=True)
class AdcsControlConfig:
    gains_by_mode: Mapping[str, AdcsControlGains] = field(
        default_factory=lambda: {
            "safePoint": AdcsControlGains(k_sigma_nm=0.06, p_rate_nms=0.05, torque_limit_nm=0.02),
            "sunPoint": AdcsControlGains(k_sigma_nm=0.08, p_rate_nms=0.05, torque_limit_nm=0.025),
            "inertialPoint": AdcsControlGains(k_sigma_nm=0.12, p_rate_nms=0.08, torque_limit_nm=0.03),
            "payload": AdcsControlGains(k_sigma_nm=0.14, p_rate_nms=0.10, torque_limit_nm=0.03),
            "reboostPoint": AdcsControlGains(k_sigma_nm=0.10, p_rate_nms=0.08, torque_limit_nm=0.025),
            "detumble": AdcsControlGains(k_sigma_nm=0.0, p_rate_nms=0.12, torque_limit_nm=0.02),
        }
    )
    reference_by_mode: Mapping[str, AdcsReference] = field(
        default_factory=lambda: {
            "safePoint": AdcsReference((0.0, 0.0, 0.0), description="coarse safe hold"),
            "sunPoint": AdcsReference((0.0, 0.0, 0.0), description="sun vector hold"),
            "inertialPoint": AdcsReference((0.1, 0.0, 0.0), description="inertial MRP target"),
            "payload": AdcsReference((0.0, 0.1, 0.0), description="payload pointing MRP target"),
            "reboostPoint": AdcsReference((0.0, 0.0, 0.1), description="reboost pointing MRP target"),
            "detumble": AdcsReference((0.0, 0.0, 0.0), description="rate damping"),
        }
    )
    sun_point_gain: float = 0.08
    safe_point_gain: float = 0.05


@dataclass(frozen=True)
class AdcsControlState:
    integral_error: Vector3 = (0.0, 0.0, 0.0)
    time_s: float = 0.0


@dataclass(frozen=True)
class AdcsControlInput:
    dt_s: float
    mode: str
    sigma_bn: Vector3 = (0.0, 0.0, 0.0)
    omega_bn_b_rad_s: Vector3 = (0.0, 0.0, 0.0)
    sun_direction_b: Vector3 = (1.0, 0.0, 0.0)
    valid_attitude: bool = True
    valid_rate: bool = True
    valid_sun: bool = True
    mode_ready: bool = True


@dataclass(frozen=True)
class AdcsControlOutput:
    time_s: float
    mode: str
    control_ready: bool
    inhibition_reason: str | None
    reference_sigma_rn: Vector3
    sigma_br: Vector3
    omega_br_b_rad_s: Vector3
    requested_torque_b_nm: Vector3
    torque_norm_nm: float
    saturated: bool
    reference_description: str


@dataclass(frozen=True)
class AdcsControlProfileResult:
    outputs: tuple[AdcsControlOutput, ...]
    final_state: AdcsControlState


@dataclass(frozen=True)
class AdcsActuatorModeConfig:
    mode_actuator: Mapping[str, str] = field(default_factory=lambda: {
        "safePoint": "rw",
        "sunPoint": "rw",
        "inertialPoint": "rw",
        "payload": "rw",
        "detumble": "mtb",
        "momentumDump": "mtb",
        "cmgPoint": "cmg",
        "reboostPoint": "rw",
    })


@dataclass(frozen=True)
class AdcsActuatorConfig:
    rw: ReactionWheelDynamicsConfig = field(default_factory=ReactionWheelDynamicsConfig)
    mtb: MtbConfig = field(default_factory=MtbConfig)
    cmg: SingleGimbalCmgConfig = field(default_factory=SingleGimbalCmgConfig)
    mode_config: AdcsActuatorModeConfig = field(default_factory=AdcsActuatorModeConfig)
    rw_enabled: bool = True
    mtb_enabled: bool = True
    cmg_enabled: bool = True
    cmg_command_gain: float = 1.0


@dataclass(frozen=True)
class AdcsActuatorState:
    rw: ReactionWheelState = field(default_factory=lambda: ReactionWheelState((0.0, 0.0, 0.0, 0.0)))
    mtb: MtbState = field(default_factory=MtbState)
    cmg: SingleGimbalCmgState = field(default_factory=lambda: SingleGimbalCmgState(10.0, 0.0))


@dataclass(frozen=True)
class AdcsActuatorInput:
    dt_s: float
    mode: str
    requested_torque_b_nm: Vector3 = (0.0, 0.0, 0.0)
    magnetic_field_b_t: Vector3 = (0.0, 0.0, 2.0e-5)
    rw_available: bool = True
    mtb_available: bool = True
    cmg_available: bool = True


@dataclass(frozen=True)
class AdcsActuatorOutput:
    time_s: float
    mode: str
    selected_actuator: str | None
    command_ready: bool
    inhibition_reason: str | None
    requested_torque_b_nm: Vector3
    rw_motor_torque_nm: tuple[float, ...]
    rw_speed_rad_s: tuple[float, ...]
    rw_momentum_norm_nms: float
    mtb_dipole_am2: tuple[float, ...]
    mtb_torque_b_nm: Vector3
    mtb_rejected_parallel_torque_b_nm: Vector3
    cmg_gimbal_rate_rad_s: float
    cmg_torque_b_nm: Vector3
    cmg_gimbal_angle_rad: float
    actuator_command_torque_b_nm: Vector3


@dataclass(frozen=True)
class AdcsActuatorProfileResult:
    outputs: tuple[AdcsActuatorOutput, ...]
    final_state: AdcsActuatorState


@dataclass(frozen=True)
class AdcsSensorConfig:
    imu: ImuConfig = field(default_factory=ImuConfig)
    star_tracker: StarTrackerConfig = field(default_factory=StarTrackerConfig)
    sun_sensor: SunSensorConfig = field(default_factory=SunSensorConfig)
    magnetometer: MagnetometerConfig = field(default_factory=MagnetometerConfig)
    required_observations_by_mode: Mapping[str, tuple[ObservationName, ...]] = field(
        default_factory=lambda: {
            "safePoint": ("rate", "sun"),
            "sunPoint": ("rate", "sun"),
            "inertialPoint": ("attitude", "rate"),
            "payload": ("attitude", "rate"),
            "detumble": ("rate", "magnetic"),
            "momentumDump": ("magnetic",),
            "reboostPoint": ("attitude", "rate"),
        }
    )
    enabled_sensors_by_mode: Mapping[str, tuple[SensorName, ...]] = field(
        default_factory=lambda: {
            "safePoint": ("imu", "sun_sensor", "magnetometer"),
            "sunPoint": ("imu", "sun_sensor", "magnetometer"),
            "inertialPoint": ("imu", "star_tracker", "magnetometer"),
            "payload": ("imu", "star_tracker", "sun_sensor", "magnetometer"),
            "detumble": ("imu", "magnetometer"),
            "momentumDump": ("magnetometer",),
            "reboostPoint": ("imu", "star_tracker"),
        }
    )


@dataclass(frozen=True)
class AdcsSensorState:
    time_s: float = 0.0
    imu_bias_state: ImuBiasState = field(default_factory=ImuBiasState)


@dataclass(frozen=True)
class AdcsSensorStepInput:
    dt_s: float
    mode: str
    sigma_bn: Vector3 = (0.0, 0.0, 0.0)
    omega_bn_b_rad_s: Vector3 = (0.0, 0.0, 0.0)
    accel_b_m_s2: Vector3 = (0.0, 0.0, 0.0)
    sun_direction_b: Vector3 = (1.0, 0.0, 0.0)
    shadow_factor: float = 1.0
    magnetic_field_b_t: Vector3 = (2.0e-5, 0.0, -4.0e-5)
    sensor_available: Mapping[str, bool] = field(
        default_factory=lambda: {
            "imu": True,
            "star_tracker": True,
            "sun_sensor": True,
            "magnetometer": True,
        }
    )


@dataclass(frozen=True)
class AdcsSensorObservation:
    time_s: float
    mode: str
    valid_attitude: bool
    valid_rate: bool
    valid_sun: bool
    valid_magnetic: bool
    sigma_bn: Vector3
    omega_bn_b_rad_s: Vector3
    accel_b_m_s2: Vector3
    sun_direction_b: Vector3
    sun_intensity: float
    magnetic_field_b_t: Vector3
    enabled_sensors: tuple[str, ...]
    available_sensors: tuple[str, ...]
    missing_required_observations: tuple[str, ...]
    mode_ready: bool


@dataclass(frozen=True)
class AdcsSensorProfileResult:
    time_s: tuple[float, ...]
    mode: tuple[str, ...]
    mode_ready: tuple[bool, ...]
    valid_attitude: tuple[bool, ...]
    valid_rate: tuple[bool, ...]
    valid_sun: tuple[bool, ...]
    valid_magnetic: tuple[bool, ...]
    missing_required_observations: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class AdcsCommandChainConfig:
    sensors: AdcsSensorConfig = field(default_factory=AdcsSensorConfig)
    control: AdcsControlConfig = field(default_factory=AdcsControlConfig)
    actuators: AdcsActuatorConfig = field(default_factory=AdcsActuatorConfig)
    zero_torque_when_control_not_ready: bool = True


@dataclass(frozen=True)
class AdcsCommandChainState:
    sensors: AdcsSensorState = field(default_factory=AdcsSensorState)
    control: AdcsControlState = field(default_factory=AdcsControlState)
    actuators: AdcsActuatorState = field(default_factory=AdcsActuatorState)
    time_s: float = 0.0


@dataclass(frozen=True)
class AdcsCommandChainInput:
    dt_s: float
    mode: str
    sigma_bn: Vector3 = (0.0, 0.0, 0.0)
    omega_bn_b_rad_s: Vector3 = (0.0, 0.0, 0.0)
    accel_b_m_s2: Vector3 = (0.0, 0.0, 0.0)
    sun_direction_b: Vector3 = (1.0, 0.0, 0.0)
    shadow_factor: float = 1.0
    magnetic_field_b_t: Vector3 = (0.0, 0.0, 2.0e-5)
    sensor_available: Mapping[str, bool] = field(
        default_factory=lambda: {
            "imu": True,
            "star_tracker": True,
            "sun_sensor": True,
            "magnetometer": True,
        }
    )
    rw_available: bool = True
    mtb_available: bool = True
    cmg_available: bool = True


@dataclass(frozen=True)
class AdcsCommandChainOutput:
    time_s: float
    mode: str
    sensor_observation: AdcsSensorObservation
    control_output: AdcsControlOutput
    actuator_output: AdcsActuatorOutput
    chain_ready: bool
    inhibition_reason: str | None
    requested_torque_b_nm: Vector3
    actuator_command_torque_b_nm: Vector3
    selected_actuator: str | None


@dataclass(frozen=True)
class AdcsCommandChainProfileResult:
    outputs: tuple[AdcsCommandChainOutput, ...]
    final_state: AdcsCommandChainState


@dataclass(frozen=True)
class AdcsConfig:
    rw_duration_s: float = 300.0
    rw_dyn_step_s: float = 0.2
    rw_fsw_step_s: float = 0.2
    rw_sample_s: float = 2.0
    mtb_duration_s: float = 120.0
    mtb_control_step_s: float = 2.0
    mtb_dynamics_step_s: float = 1.0
    mtb_sample_s: float = 4.0
    rw_friction_factor: float = 0.0
    sensor_noise_factor: float = 0.0


@dataclass(frozen=True)
class AdcsState:
    time_s: float = 0.0
    rw_attitude_error_norm: float = 0.0
    rw_rate_error_norm_rad_s: float = 0.0
    mtb_rate_norm_rad_s: float = 0.0


@dataclass(frozen=True)
class AdcsStepResult:
    time_s: float
    runner: str
    attitude_error_norm: float | None
    rate_error_norm_rad_s: float | None
    rw_speed_rad_s: tuple[float, ...] | None
    mtb_rate_norm_rad_s: float | None
    mtb_torque_norm_nm: float | None


@dataclass(frozen=True)
class AdcsProfileResult:
    rw_status: str
    mtb_status: str
    rw_sample_count: int
    mtb_sample_count: int
    rw_initial_attitude_error_norm: float
    rw_final_attitude_error_norm: float
    rw_peak_attitude_error_norm: float
    rw_attitude_error_ratio: float
    rw_pointing_performance_status: str
    mtb_initial_rate_norm_rad_s: float
    mtb_final_rate_norm_rad_s: float
    mtb_rate_ratio: float
    status: str


@dataclass(frozen=True)
class ADCSBasiliskConfig:
    """Basilisk builder configuration for the ADCS subsystem assembly."""

    duration_s: float = 60.0
    step_s: float = 1.0
    sample_s: float = 1.0
    spacecraft_mass_kg: float = 100.0
    spacecraft_inertia_kg_m2: tuple[float, float, float, float, float, float, float, float, float] = (
        10.0, 0.0, 0.0,
        0.0, 10.0, 0.0,
        0.0, 0.0, 10.0,
    )
    include_sensors: bool = True
    include_cmg: bool = True
    rw_motor_torques_nm: tuple[float, ...] = (0.0, 0.0, 0.0)
    mtb_dipoles_am2: tuple[float, ...] = (0.0, 0.0, 0.0)
    magnetic_field_t: tuple[float, float, float] = (2.0e-5, -1.0e-5, 3.0e-5)


@dataclass(frozen=True)
class RwWheelConfig:
    """Subsystem-level selection of component ``ReactionWheelSpec`` fields."""

    model: str = "Honeywell_HR16"
    axis_b: Vector3 = (1.0, 0.0, 0.0)
    max_momentum_nms: float = 50.0
    initial_speed_rpm: float = 100.0
    wheel_js: float = 0.1
    u_max_nm: float = 0.2
    omega_max_rad_s: float = 600.0
    use_min_torque: bool = False
    u_min_nm: float = 0.0
    max_power_w: float = -1.0
    position_b_m: Vector3 = (0.0, 0.0, 0.0)
    label: str | None = None
    use_rw_friction: bool = False
    fCoulomb: float = 0.0
    fStatic: float = 0.0
    betaStatic: float = -1.0
    cViscous: float = 0.0


@dataclass(frozen=True)
class RwOnlyClosedLoopConfig:
    duration_s: float = 300.0
    dyn_step_s: float = 0.1
    fsw_step_s: float = 0.1
    sample_s: float = 1.0
    target_sigma_rn: Vector3 = (0.0, 0.0, 0.0)
    initial_sigma_bn: Vector3 = (0.2, -0.1, 0.15)
    initial_omega_bn_b_rad_s: Vector3 = (0.01, -0.005, 0.02)
    spacecraft_inertia_kg_m2: tuple[float, float, float, float, float, float, float, float, float] = (
        900.0, 0.0, 0.0,
        0.0, 800.0, 0.0,
        0.0, 0.0, 600.0,
    )
    spacecraft_mass_kg: float = 750.0
    control_k: float = 4.0
    control_p: float = 25.0
    control_ki: float = -1.0
    integral_limit: float = -1.0
    wheels: tuple[RwWheelConfig, ...] = (
        RwWheelConfig(axis_b=(1.0, 0.0, 0.0), initial_speed_rpm=100.0),
        RwWheelConfig(axis_b=(0.0, 1.0, 0.0), initial_speed_rpm=200.0),
        RwWheelConfig(axis_b=(0.0, 0.0, 1.0), initial_speed_rpm=-150.0),
    )


@dataclass(frozen=True)
class AdcsClosedLoopModeCase:
    name: str
    mode: str
    description: str
    config: RwOnlyClosedLoopConfig
    max_final_attitude_ratio: float = 0.2
    max_final_rate_ratio: float = 0.2


@dataclass(frozen=True)
class MomentumDumpConfig:
    duration_s: float = 2400.0
    dt_s: float = 2.0
    sample_s: float = 10.0
    rw_config: ReactionWheelDynamicsConfig = ReactionWheelDynamicsConfig(
        num_wheels=3,
        wheel_inertia_kg_m2=(0.1, 0.1, 0.1),
        max_motor_torque_nm=(0.2, 0.2, 0.2),
        max_speed_rad_s=(6000.0, 6000.0, 6000.0),
        damping_nms=(0.0, 0.0, 0.0),
        wheel_axes_B=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    )
    initial_rw_state: ReactionWheelState = ReactionWheelState((120.0, -90.0, 60.0))
    magnetic_field_strength_t: float = 3.0e-5
    magnetic_field_rotation_rate_rad_s: float = 0.0025
    magnetic_field_y_scale: float = 0.55
    unload_gain_1_s: float = 0.006
    max_dipole_am2: float = 150.0
    dipole_lag_tau_s: float = 3.0
    convergence_momentum_ratio: float = 0.40


@dataclass(frozen=True)
class MomentumDumpSample:
    time_s: float
    rw_speeds_rad_s: tuple[float, ...]
    rw_momentum_b_nms: tuple[float, float, float]
    rw_momentum_norm_nms: float
    desired_unload_torque_b_nm: tuple[float, float, float]
    magnetic_field_b_t: tuple[float, float, float]
    commanded_dipole_am2: tuple[float, float, float]
    applied_dipole_am2: tuple[float, float, float]
    mtb_torque_b_nm: tuple[float, float, float]
    rejected_parallel_torque_b_nm: tuple[float, float, float]


@dataclass(frozen=True)
class MomentumDumpResult:
    config: MomentumDumpConfig
    samples: tuple[MomentumDumpSample, ...]
    initial_momentum_norm_nms: float
    final_momentum_norm_nms: float
    momentum_ratio: float
    max_abs_dipole_am2: float
    converged: bool


@dataclass(frozen=True)
class MomentumDumpBasiliskSummary:
    backend: str
    available: bool
    status: str
    modules: tuple[str, ...]
    messages: tuple[str, ...]


@dataclass(frozen=True)
class MagneticDetumbleConfig:
    duration_s: float = 900.0
    dt_s: float = 1.0
    sample_s: float = 2.0
    inertia_kg_m2: tuple[float, float, float] = (6.0, 7.0, 5.0)
    initial_sigma_bn: tuple[float, float, float] = (0.05, -0.03, 0.02)
    initial_omega_bn_b_rad_s: tuple[float, float, float] = (0.035, -0.025, 0.03)
    magnetic_field_n_t: tuple[float, float, float] = (2.0e-5, -1.0e-5, 3.0e-5)
    magnetic_field_rotation_rate_rad_s: float = 0.008
    bdot_gain_am2_per_t_s: float = 6.0e7
    dipole_limit_am2: tuple[float, float, float] = (80.0, 80.0, 80.0)
    dipole_lag_tau_s: float = 2.0


@dataclass(frozen=True)
class MagneticDetumbleState:
    time_s: float
    sigma_bn: tuple[float, float, float]
    omega_bn_b_rad_s: tuple[float, float, float]
    dipole_am2: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class MagneticDetumbleSample:
    time_s: float
    sigma_norm: float
    omega_norm_rad_s: float
    omega_perp_b_norm_rad_s: float
    magnetic_field_b_t: tuple[float, float, float]
    commanded_dipole_am2: tuple[float, float, float]
    dipole_am2: tuple[float, float, float]
    torque_b_nm: tuple[float, float, float]
    torque_dot_b: float


@dataclass(frozen=True)
class MagneticDetumbleResult:
    samples: tuple[MagneticDetumbleSample, ...]
    initial_rate_norm_rad_s: float
    final_rate_norm_rad_s: float
    initial_perp_rate_norm_rad_s: float
    final_perp_rate_norm_rad_s: float
    final_sigma_norm: float
    final_dipole_norm_am2: float


__all__ = [
    "AdcsReference",
    "AdcsControlGains",
    "AdcsControlConfig",
    "AdcsControlState",
    "AdcsControlInput",
    "AdcsControlOutput",
    "AdcsControlProfileResult",
    "AdcsActuatorModeConfig",
    "AdcsActuatorConfig",
    "AdcsActuatorState",
    "AdcsActuatorInput",
    "AdcsActuatorOutput",
    "AdcsActuatorProfileResult",
    "AdcsSensorConfig",
    "AdcsSensorState",
    "AdcsSensorStepInput",
    "AdcsSensorObservation",
    "AdcsSensorProfileResult",
    "AdcsCommandChainConfig",
    "AdcsCommandChainState",
    "AdcsCommandChainInput",
    "AdcsCommandChainOutput",
    "AdcsCommandChainProfileResult",
    "AdcsConfig",
    "AdcsState",
    "AdcsStepResult",
    "AdcsProfileResult",
    "RwWheelConfig",
    "RwOnlyClosedLoopConfig",
    "AdcsClosedLoopModeCase",
    "MomentumDumpConfig",
    "MomentumDumpSample",
    "MomentumDumpResult",
    "MomentumDumpBasiliskSummary",
    "MagneticDetumbleConfig",
    "MagneticDetumbleState",
    "MagneticDetumbleSample",
    "MagneticDetumbleResult",
]
