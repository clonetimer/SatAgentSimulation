"""sun_sensor component builder module.

Provides both Python model config builders and Basilisk-native component factories.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import SunSensorConfig

from dataclasses import dataclass, replace
from typing import Any

from ..dynamic_models import v3, norm, unit
from .degradation import SunSensorDegradation
from .faults import apply_sun_sensor_faults
from .faults import FaultSpec
from .degradation import apply_sun_sensor_degradation
from .faults import SunSensorFaultType


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
        location='src/components/sun_sensor/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )




@dataclass(frozen=True)
class SunSensorMeasurement:
    valid: bool
    sun_direction_b: tuple
    intensity: float


@dataclass(frozen=True)
class SunSensorProfileResult:
    valid: tuple
    intensity: tuple


def basilisk_available() -> bool:
    """Return True when Basilisk messaging/sysModel modules are available."""
    return _messaging is not None and _sysModel is not None


def _vec3(value, default=(0.0, 0.0, 0.0)):
    if value is None:
        return [float(x) for x in default]
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


def measure_sun_sensor(sun, shadow, c):
    n = norm(v3(sun))
    inten = n * max(0, min(1, shadow))
    return SunSensorMeasurement(
        inten >= c.min_intensity and n > 0,
        unit(sun) if n > 0 and inten >= c.min_intensity else (0, 0, 0),
        inten,
    )


def simulate_sun_sensor_profile(suns, shadows, c):
    vals = [measure_sun_sensor(s, shadows[i] if i < len(shadows) else 1, c) for i, s in enumerate(suns)]
    return SunSensorProfileResult(tuple(v.valid for v in vals), tuple(v.intensity for v in vals))


def _build_nominal_sun_sensor_config_base_impl(min_intensity: float = 1e-6, degradation: SunSensorDegradation | None = None, fault_specs: list[FaultSpec] | None = None, **native_kwargs) -> SunSensorConfig:
    """Build a nominal SunSensorConfig with all parameters defaulted and optional degradation."""
    config = SunSensorConfig(min_intensity=min_intensity, **native_kwargs)
    if degradation is not None:
        config = apply_sun_sensor_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_sun_sensor_faults(config, fault_specs)
    return config


def apply_sun_sensor_config_faults(config: SunSensorConfig, fault_specs: list[FaultSpec]) -> SunSensorConfig:
    """Apply fault specifications to Sun Sensor configuration."""
    new_config = config

    for spec in fault_specs:
        if not isinstance(spec.fault_type, SunSensorFaultType):
            continue

        if spec.fault_type == SunSensorFaultType.NoiseIncrease:
            new_config = replace(
                new_config,
                noise_std=new_config.noise_std * (1.0 + float(spec.magnitude)),
            )

        elif spec.fault_type == SunSensorFaultType.BiasDrift:
            new_config = replace(
                new_config,
                accuracy=new_config.accuracy * (1.0 - float(spec.magnitude) * 0.1),
            )

        elif spec.fault_type == SunSensorFaultType.Saturation:
            new_config = replace(
                new_config,
                min_intensity=new_config.min_intensity * (1.0 + float(spec.magnitude)),
            )

    return new_config


def require_basilisk_coarse_sun_sensor() -> None:
    try:
        from Basilisk.simulation import coarseSunSensor  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk coarse sun sensor module is unavailable: {exc}") from exc


def build_coarse_sun_sensor(
    model_tag: str = "componentCoarseSunSensor",
    sc_state_msg: Any | None = None,
    sun_in_msg: Any | None = None,
    eclipse_in_msg: Any | None = None,
    albedo_in_msg: Any | None = None,
    n_hat_b: tuple[float, float, float] = (1.0, 0.0, 0.0),
    fov_rad: float = 1.5707963267948966,
    scale_factor: float = 1.0,
    bias: float = 0.0,
    noise_std: float = 0.0,
    walk_bounds: float = -1.0,
    fault_noise_std: float = 0.5,
    max_output: float = 1.0e6,
    min_output: float = 0.0,
    k_power: float = 2.0,
    css_group_id: int = -1,
    fault_state: int | None = None,
    sensor_pos_b_m: tuple[float, float, float] = (0.0, 0.0, 0.0),
    sensor_pos_pb_b_m: tuple[float, float, float] = (0.0, 0.0, 0.0),
    b2p321_angles_rad: tuple[float, float, float] = (0.0, 0.0, 0.0),
    dcm_pb: tuple[float, ...] | tuple[tuple[float, float, float], ...] = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
):
    """Create Basilisk ``coarseSunSensor.CoarseSunSensor`` and optionally wire inputs.

    Parameters
    ----------
    model_tag : str, optional
        Model tag for the sensor.
    sc_state_msg : optional
        Spacecraft state message (SCStatesMsg) to subscribe to.
    sun_in_msg : optional
        Sun position message (SpicePlanetStateMsg) to subscribe to.
    eclipse_in_msg : optional
        Eclipse/shadow message (EclipseMsg) to subscribe to.

    Returns
    -------
    object
        Basilisk coarseSunSensor instance with subscribed messages.
    """
    require_basilisk_coarse_sun_sensor()
    from Basilisk.simulation import coarseSunSensor

    css = coarseSunSensor.CoarseSunSensor()
    css.ModelTag = model_tag
    css.nHat_B = _col3(n_hat_b, (1.0, 0.0, 0.0))
    css.fov = float(fov_rad)
    css.scaleFactor = float(scale_factor)
    css.senBias = float(bias)
    css.senNoiseStd = float(noise_std)
    css.walkBounds = float(walk_bounds)
    css.faultNoiseStd = float(fault_noise_std)
    css.maxOutput = float(max_output)
    css.minOutput = float(min_output)
    css.kPower = float(k_power)
    css.CSSGroupID = int(css_group_id)
    css.r_B = _col3(sensor_pos_b_m)
    css.r_PB_B = _col3(sensor_pos_pb_b_m)
    css.B2P321Angles = _vec3(b2p321_angles_rad)
    css.dcm_PB = _matrix3(dcm_pb)
    if fault_state is not None:
        css.faultState = int(fault_state)
    if sc_state_msg is not None:
        css.stateInMsg.subscribeTo(sc_state_msg)
    if sun_in_msg is not None:
        css.sunInMsg.subscribeTo(sun_in_msg)
    if eclipse_in_msg is not None and hasattr(css, "sunEclipseInMsg"):
        css.sunEclipseInMsg.subscribeTo(eclipse_in_msg)
    if albedo_in_msg is not None and hasattr(css, "albedoInMsg"):
        css.albedoInMsg.subscribeTo(albedo_in_msg)
    return css

# Component fault/degradation compatibility wrappers
from .degradation import SunSensorDegradationRate
from .degradation import compute_degradation_state
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_sun_sensor_config_base = _build_nominal_sun_sensor_config_base_impl

def build_nominal_sun_sensor_config(
    *args,
    degradation: SunSensorDegradation | None = None,
    degradation_rate: SunSensorDegradationRate | None = None,
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
    config = _build_nominal_sun_sensor_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_sun_sensor_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_sun_sensor_faults(config, fault_specs)
    return config

