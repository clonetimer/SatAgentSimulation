"""IMU component builder module.

Provides both Python model config builders and Basilisk-native component factories.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import ImuConfig

from dataclasses import dataclass, replace
from math import sqrt
from random import Random
from typing import Any, Sequence

from ..dynamic_models import v3
from .degradation import ImuDegradation
from .faults import apply_imu_faults
from .faults import FaultSpec
from .degradation import apply_imu_degradation
from .faults import ImuFaultType


_messaging = None
_sysModel = None

try:
    from Basilisk.architecture import messaging, sysModel
    _messaging = messaging
    _sysModel = sysModel
except ImportError as exc:
    record_runtime_diagnostic(
        code='OPTIONAL_DEPENDENCY_IMPORT_UNAVAILABLE',
        category=DiagnosticCategory.OPTIONAL_DEPENDENCY_PROBE,
        location='src/components/imu/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )




@dataclass(frozen=True)
class ImuMeasurement:
    gyro_rad_s: tuple
    accel_m_s2: tuple


@dataclass(frozen=True)
class ImuBiasState:
    gyro_bias_rad_s: tuple = (0, 0, 0)


@dataclass(frozen=True)
class ImuBiasProfileResult:
    time_s: tuple
    gyro_bias_rad_s: tuple


def basilisk_available() -> bool:
    """Return True when Basilisk messaging/sysModel modules are available."""
    return _messaging is not None and _sysModel is not None


def _vec3(value, default=(0.0, 0.0, 0.0)):
    if value is None:
        return [float(x) for x in default]
    if isinstance(value, (int, float)):
        return [float(value)] * 3
    values = list(value)
    if len(values) != 3:
        raise ValueError("expected a 3-vector")
    return [float(x) for x in values]


def _col3(value, default=(0.0, 0.0, 0.0)):
    return [[x] for x in _vec3(value, default)]


def _matrix3(value):
    values = list(value)
    if len(values) == 3 and all(hasattr(row, "__iter__") for row in values):
        return [[float(x) for x in list(row)[:3]] for row in values]
    if len(values) != 9:
        raise ValueError("expected a 3x3 matrix or 9 flat values")
    return [[float(values[0]), float(values[1]), float(values[2])], [float(values[3]), float(values[4]), float(values[5])], [float(values[6]), float(values[7]), float(values[8])]]


def measure_imu(g, a, c, bias_state=None):
    b = bias_state.gyro_bias_rad_s if bias_state else (0, 0, 0)
    return ImuMeasurement(
        tuple(v3(g)[i] * c.gyro_scale[i] + c.gyro_bias_rad_s[i] + b[i] for i in range(3)),
        tuple(v3(a)[i] * c.accel_scale[i] + c.accel_bias_m_s2[i] for i in range(3)),
    )


def simulate_gyro_bias_profile(s, c, steps, dt_s, seed=None):
    rng = Random(seed)
    t = [0.0]
    b = [s.gyro_bias_rad_s]
    for i in range(steps):
        std = c.gyro_bias_walk_std_rad_s_sqrt_s * sqrt(dt_s)
        s = ImuBiasState(tuple(s.gyro_bias_rad_s[k] + rng.gauss(0, std) for k in range(3)))
        t.append((i + 1) * dt_s)
        b.append(s.gyro_bias_rad_s)
    return ImuBiasProfileResult(tuple(t), tuple(b))


def _build_nominal_imu_config_base_impl(gyro_bias_walk_std_rad_s_sqrt_s: float = 1e-4, degradation: ImuDegradation | None = None, fault_specs: list[FaultSpec] | None = None, **native_kwargs) -> ImuConfig:
    """Build a nominal ImuConfig with default values and optional degradation.

    Args:
        gyro_bias_walk_std_rad_s_sqrt_s: Gyro bias random walk standard deviation in rad/s/√s
        degradation: Optional IMU degradation state to apply

    Returns:
        ImuConfig: Nominal IMU configuration with degradation applied if specified
    """
    config = ImuConfig(
        gyro_bias_walk_std_rad_s_sqrt_s=gyro_bias_walk_std_rad_s_sqrt_s,
        **native_kwargs,
    )
    if degradation is not None:
        config = apply_imu_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_imu_faults(config, fault_specs)
    return config


def apply_imu_config_faults(config: ImuConfig, fault_specs: list[FaultSpec]) -> ImuConfig:
    """Apply fault specifications to IMU configuration.

    Args:
        config: IMU configuration object
        fault_specs: List of fault specifications to apply

    Returns:
        Modified IMU configuration with faults applied
    """
    new_config = config

    for spec in fault_specs:
        if not isinstance(spec.fault_type, ImuFaultType):
            continue

        if spec.fault_type == ImuFaultType.GyroBiasDrift:
            original_bias = new_config.gyro_bias_rad_s
            degraded_bias = tuple(b + float(spec.magnitude) for b in original_bias)
            new_config = replace(new_config, gyro_bias_rad_s=degraded_bias)

        elif spec.fault_type == ImuFaultType.AccelBiasDrift:
            original_bias = new_config.accel_bias_m_s2
            degraded_bias = tuple(b + float(spec.magnitude) for b in original_bias)
            new_config = replace(new_config, accel_bias_m_s2=degraded_bias)

        elif spec.fault_type == ImuFaultType.GyroNoiseIncrease:
            degraded_scale = tuple(
                s * (1.0 + float(spec.magnitude) * 0.1)
                for s in new_config.gyro_scale
            )
            new_config = replace(new_config, gyro_scale=degraded_scale)

        elif spec.fault_type == ImuFaultType.AccelNoiseIncrease:
            degraded_scale = tuple(
                s * (1.0 + float(spec.magnitude) * 0.1)
                for s in new_config.accel_scale
            )
            new_config = replace(new_config, accel_scale=degraded_scale)

        elif spec.fault_type == ImuFaultType.StuckAtZero:
            new_config = replace(
                new_config,
                gyro_bias_rad_s=(0, 0, 0),
                accel_bias_m_s2=(0, 0, 0),
                gyro_scale=(0, 0, 0),
                accel_scale=(0, 0, 0),
            )

    return new_config


def require_basilisk_imu() -> None:
    try:
        from Basilisk.simulation import imuSensor  # noqa: F401
        from Basilisk.architecture import messaging  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk IMU modules are unavailable: {exc}") from exc


def build_imu_sensor(
    model_tag: str = "componentImuSensor",
    sc_state_msg: Any | None = None,
    sigma_gyro_rad_s: float = 1e-4,
    sigma_accel_m_s2: float = 1e-3,
    sigma_gyro_random_walk_rad_s_sqrt_s: float = 1e-5,
    sigma_accel_random_walk_m_s2_sqrt_s: float = 1e-4,
    gyro_bias_rad_s: Sequence[float] | None = None,
    accel_bias_m_s2: Sequence[float] | None = None,
    gyro_scale: Sequence[float] | None = None,
    accel_scale: Sequence[float] | None = None,
    sensor_pos_b_m: Sequence[float] | None = None,
    dcm_pb: Sequence[float] | Sequence[Sequence[float]] | None = None,
    sen_rot_max_rad_s: float | None = None,
    sen_trans_max_m_s2: float | None = None,
    p_matrix_accel: Sequence[float] | Sequence[Sequence[float]] | None = None,
    a_matrix_accel: Sequence[float] | Sequence[Sequence[float]] | None = None,
    p_matrix_gyro: Sequence[float] | Sequence[Sequence[float]] | None = None,
    a_matrix_gyro: Sequence[float] | Sequence[Sequence[float]] | None = None,
    output_buffer_count: int | None = None,
):
    """Create Basilisk ``imuSensor.ImuSensor`` with configurable noise and bias.

    Parameters
    ----------
    model_tag : str, optional
        Model tag for the sensor.
    sc_state_msg : optional
        Spacecraft state message (SCStatesMsg) to subscribe to.
    sigma_gyro_rad_s : float, optional
        Gyroscope noise standard deviation in rad/s (default 1e-4).
    sigma_accel_m_s2 : float, optional
        Accelerometer noise standard deviation in m/s² (default 1e-3).
    sigma_gyro_random_walk_rad_s_sqrt_s : float, optional
        Gyroscope random walk in rad/s/√s (default 1e-5).
    sigma_accel_random_walk_m_s2_sqrt_s : float, optional
        Accelerometer random walk in m/s²/√s (default 1e-4).
    gyro_bias_rad_s : sequence of float, optional
        Initial gyroscope bias in rad/s (3 elements).
    accel_bias_m_s2 : sequence of float, optional
        Initial accelerometer bias in m/s² (3 elements).

    Returns
    -------
    object
        Basilisk imuSensor instance with configured noise and bias.
    """
    require_basilisk_imu()
    from Basilisk.simulation import imuSensor

    imu = imuSensor.ImuSensor()
    imu.ModelTag = model_tag

    imu.setErrorBoundsGyro([float(sigma_gyro_rad_s)] * 3)
    imu.setErrorBoundsAccel([float(sigma_accel_m_s2)] * 3)
    imu.setWalkBoundsGyro([float(sigma_gyro_random_walk_rad_s_sqrt_s)] * 3)
    imu.setWalkBoundsAccel([float(sigma_accel_random_walk_m_s2_sqrt_s)] * 3)

    if gyro_bias_rad_s is not None:
        imu.senRotBias = _col3(gyro_bias_rad_s)
    if accel_bias_m_s2 is not None:
        imu.senTransBias = _col3(accel_bias_m_s2)
    if gyro_scale is not None:
        imu.gyroScale = _col3(gyro_scale, (1.0, 1.0, 1.0))
    if accel_scale is not None:
        imu.accelScale = _col3(accel_scale, (1.0, 1.0, 1.0))
    if sensor_pos_b_m is not None:
        imu.sensorPos_B = _col3(sensor_pos_b_m)
    if dcm_pb is not None:
        imu.dcm_PB = _matrix3(dcm_pb)
    if sen_rot_max_rad_s is not None:
        imu.senRotMax = float(sen_rot_max_rad_s)
    if sen_trans_max_m_s2 is not None:
        imu.senTransMax = float(sen_trans_max_m_s2)
    if p_matrix_accel is not None:
        imu.PMatrixAccel = _matrix3(p_matrix_accel)
    if a_matrix_accel is not None:
        imu.AMatrixAccel = _matrix3(a_matrix_accel)
    if p_matrix_gyro is not None:
        imu.PMatrixGyro = _matrix3(p_matrix_gyro)
    if a_matrix_gyro is not None:
        imu.AMatrixGyro = _matrix3(a_matrix_gyro)
    if output_buffer_count is not None:
        imu.OutputBufferCount = int(output_buffer_count)

    if sc_state_msg is not None:
        imu.scStateInMsg.subscribeTo(sc_state_msg)
    return imu

# Component fault/degradation compatibility wrappers
from .degradation import ImuDegradationRate
from .degradation import compute_degradation_state
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_imu_config_base = _build_nominal_imu_config_base_impl

def build_nominal_imu_config(
    *args,
    degradation: ImuDegradation | None = None,
    degradation_rate: ImuDegradationRate | None = None,
    years_elapsed: float = 0.0,
    fault_specs: list[_ComponentFaultSpec] | None = None,
    **kwargs,
):
    """Build config with canonical degradation-rate, degradation-state and fault support."""
    if "degradation" in kwargs:
        degradation = kwargs.pop("degradation")
    if "degradation_rate" in kwargs:
        degradation_rate = kwargs.pop("degradation_rate")
    if "years_elapsed" in kwargs:
        years_elapsed = kwargs.pop("years_elapsed")
    if "fault_specs" in kwargs:
        fault_specs = kwargs.pop("fault_specs")
    config = _build_nominal_imu_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_imu_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_imu_faults(config, fault_specs)
    return config

