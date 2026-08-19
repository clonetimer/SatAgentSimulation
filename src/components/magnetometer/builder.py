"""magnetometer component builder module.

Provides both Python model config builders and Basilisk-native component factories.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import MagnetometerConfig

from dataclasses import dataclass, replace
from typing import Any, Sequence

from ..dynamic_models import v3
from .degradation import MagnetometerDegradation
from .faults import apply_magnetometer_faults
from .faults import FaultSpec
from .degradation import apply_magnetometer_degradation
from .faults import MagnetometerFaultType


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
        location='src/components/magnetometer/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )




@dataclass(frozen=True)
class MagnetometerMeasurement:
    magnetic_field_t: tuple
    valid: bool = True


def _mat(m):
    return [list(r) for r in (m or ((1, 0, 0), (0, 1, 0), (0, 0, 1)))]


def _mul(m, v):
    return [sum(m[i][j] * v[j] for j in range(3)) for i in range(3)]


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
    if value is None:
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    values = list(value)
    if len(values) == 3 and all(hasattr(row, "__iter__") for row in values):
        return [[float(x) for x in list(row)[:3]] for row in values]
    if len(values) != 9:
        raise ValueError("expected a 3x3 matrix or 9 flat values")
    return [[float(values[0]), float(values[1]), float(values[2])], [float(values[3]), float(values[4]), float(values[5])], [float(values[6]), float(values[7]), float(values[8])]]


def measure_magnetic_field_sensor(b, c):
    v = v3(b)
    scale = getattr(c, "axis_scale", (1.0, 1.0, 1.0))
    return tuple(v[i] * scale[i] + c.bias_t[i] for i in range(3))


def measure_magnetic_field_body(b, c, output_frame='sensor'):
    return measure_magnetic_field_sensor(_mul(_mat(c.mounting_matrix_sb), v3(b)), c)


def measure_magnetic_field(b, c):
    return measure_magnetic_field_sensor(b, c)


def basilisk_available() -> bool:
    """Return True when Basilisk messaging/sysModel modules are available."""
    return _messaging is not None and _sysModel is not None


def _build_nominal_magnetometer_config_base_impl(
    axis_scale: tuple = (1.0, 1.0, 1.0),
    scale: tuple | None = None,
    scale_factor: float = 1.0,
    fault_specs: list[FaultSpec] | None = None,
    bias_t: tuple = (0.0, 0.0, 0.0),
    mounting_matrix_sb: tuple | None = None,
    clip_t: float | tuple | None = None,
    noise_std_t: float | tuple = 0.0,
    walk_bounds_t: float | tuple = 0.0,
    noise_seed: int | None = None,
    degradation: MagnetometerDegradation | None = None,
) -> MagnetometerConfig:
    """Build a nominal MagnetometerConfig with default values and optional degradation."""
    config = MagnetometerConfig(
        axis_scale=tuple(scale if scale is not None else axis_scale),
        scale_factor=float(scale_factor),
        bias_t=bias_t,
        mounting_matrix_sb=mounting_matrix_sb,
        clip_t=clip_t,
        noise_std_t=noise_std_t,
        walk_bounds_t=walk_bounds_t,
        noise_seed=noise_seed,
    )
    if degradation is not None:
        config = apply_magnetometer_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_magnetometer_faults(config, fault_specs)
    return config


def apply_magnetometer_config_faults(config: MagnetometerConfig, fault_specs: list[FaultSpec]) -> MagnetometerConfig:
    """Apply fault specifications to Magnetometer configuration."""
    new_config = config

    for spec in fault_specs:
        if not isinstance(spec.fault_type, MagnetometerFaultType):
            continue

        if spec.fault_type == MagnetometerFaultType.BiasDrift:
            original_bias = new_config.bias_t
            degraded_bias = tuple(b + float(spec.magnitude) for b in original_bias)
            new_config = replace(new_config, bias_t=degraded_bias)

        elif spec.fault_type == MagnetometerFaultType.NoiseIncrease:
            new_config = replace(
                new_config,
                noise_std_nT=new_config.noise_std_nT * (1.0 + float(spec.magnitude)),
            )

        elif spec.fault_type == MagnetometerFaultType.ScaleFactorError:
            degraded_scale = tuple(
                s * (1.0 + float(spec.magnitude) * 0.01)
                for s in new_config.axis_scale
            )
            new_config = replace(new_config, axis_scale=degraded_scale, scale_factor=new_config.scale_factor * (1.0 + float(spec.magnitude) * 0.01))

    return new_config


def require_basilisk_magnetometer() -> None:
    try:
        from Basilisk.simulation import magnetometer  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk magnetometer module is unavailable: {exc}") from exc


def build_magnetometer(
    model_tag: str = "componentMagnetometer",
    sc_state_msg: Any | None = None,
    mag_field_msg: Any | None = None,
    sigma_mag_tesla: float | Sequence[float] = 1e-9,
    sigma_bias_tesla: float | Sequence[float] = 1e-8,
    bias_tesla: Sequence[float] | None = None,
    scale_factor: float | Sequence[float] | None = None,
    mounting_matrix_sb: Sequence[float] | Sequence[Sequence[float]] | None = None,
    clip_t: float | tuple[float, float] | None = None,
    max_output_t: float | None = None,
    min_output_t: float | None = None,
    stuck_value_t: Sequence[float] | None = None,
    spike_probability: float | Sequence[float] | None = None,
    spike_amount_t: float | Sequence[float] | None = None,
    fault_state_axis: int | None = None,
):
    """Create Basilisk ``magnetometer.Magnetometer`` with configurable parameters.

    Parameters
    ----------
    model_tag : str, optional
        Model tag for the sensor.
    sc_state_msg : optional
        Spacecraft state message (SCStatesMsg) to subscribe to.
    mag_field_msg : optional
        Magnetic field message (MagneticFieldMsg) to subscribe to.
    sigma_mag_tesla : float, optional
        Measurement noise standard deviation in Tesla (default 1e-9).
    sigma_bias_tesla : float, optional
        Bias instability standard deviation in Tesla (default 1e-8).
    bias_tesla : sequence of float, optional
        Initial bias in Tesla (3 elements).
    scale_factor : sequence of float, optional
        Scale factor errors (3 elements, default 1.0 for each axis).

    Returns
    -------
    object
        Basilisk Magnetometer instance with configured parameters.
    """
    require_basilisk_magnetometer()
    from Basilisk.simulation import magnetometer

    tam = magnetometer.Magnetometer()
    tam.ModelTag = model_tag

    tam.senNoiseStd = _col3(sigma_mag_tesla)
    tam.walkBounds = _col3(sigma_bias_tesla)

    if bias_tesla is not None:
        tam.senBias = _col3(bias_tesla)
    if scale_factor is not None:
        if isinstance(scale_factor, (int, float)):
            tam.scaleFactor = float(scale_factor)
        else:
            values = list(scale_factor)
            if len(values) != 1 and len(set(float(x) for x in values[:3])) != 1:
                raise ValueError("Basilisk Magnetometer.scaleFactor is scalar; use a scalar or equal-axis sequence")
            tam.scaleFactor = float(values[0])
    if mounting_matrix_sb is not None:
        tam.dcm_SB = _matrix3(mounting_matrix_sb)
    if clip_t is not None:
        if isinstance(clip_t, tuple):
            tam.minOutput = float(clip_t[0])
            tam.maxOutput = float(clip_t[1])
        else:
            tam.minOutput = -abs(float(clip_t))
            tam.maxOutput = abs(float(clip_t))
    if max_output_t is not None:
        tam.maxOutput = float(max_output_t)
    if min_output_t is not None:
        tam.minOutput = float(min_output_t)
    if stuck_value_t is not None:
        tam.stuckValue = _col3(stuck_value_t)
    if spike_probability is not None:
        tam.spikeProbability = _col3(spike_probability)
    if spike_amount_t is not None:
        tam.spikeAmount = _col3(spike_amount_t)
    if fault_state_axis is not None and hasattr(tam, "setFaultState"):
        tam.setFaultState(int(fault_state_axis))

    if sc_state_msg is not None:
        tam.stateInMsg.subscribeTo(sc_state_msg)
    if mag_field_msg is not None:
        tam.magInMsg.subscribeTo(mag_field_msg)
    return tam

# Component fault/degradation compatibility wrappers
from .degradation import MagnetometerDegradationRate
from .degradation import compute_degradation_state
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_magnetometer_config_base = _build_nominal_magnetometer_config_base_impl

def build_nominal_magnetometer_config(
    *args,
    degradation: MagnetometerDegradation | None = None,
    degradation_rate: MagnetometerDegradationRate | None = None,
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
    config = _build_nominal_magnetometer_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_magnetometer_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_magnetometer_faults(config, fault_specs)
    return config

