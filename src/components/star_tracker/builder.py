"""star_tracker component builder module.

Provides both Python model config builders and Basilisk-native component factories.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import StarTrackerConfig

from dataclasses import dataclass, replace
from typing import Any

from ..dynamic_models import v3, norm
from .degradation import StarTrackerDegradation
from .faults import apply_star_tracker_faults
from .faults import FaultSpec
from .degradation import apply_star_tracker_degradation
from .faults import StarTrackerFaultType


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
        location='src/components/star_tracker/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )




@dataclass(frozen=True)
class StarTrackerState:
    drift_bias_mrp: tuple = (0, 0, 0)


@dataclass(frozen=True)
class StarTrackerMeasurement:
    valid: bool
    sigma_bn: tuple
    drift_bias_mrp: tuple


@dataclass(frozen=True)
class StarTrackerProfileResult:
    time_s: tuple
    drift_bias_mrp: tuple
    sigma_bn: tuple


def basilisk_available() -> bool:
    """Return True when Basilisk messaging/sysModel modules are available."""
    return _messaging is not None and _sysModel is not None


def step_star_tracker(s, sigma, c, dt_s):
    b = tuple(s.drift_bias_mrp[i] + c.drift_rate_mrp_s[i] * dt_s for i in range(3))
    n = norm(b)
    if n > c.max_drift_norm:
        b = tuple(x * c.max_drift_norm / n for x in b)
    return StarTrackerState(b), StarTrackerMeasurement(True, tuple(v3(sigma)[i] + b[i] for i in range(3)), b)


def measure_star_tracker(sigma, c, dt_s=0.0, state=None):
    return step_star_tracker(state or StarTrackerState(), sigma, c, dt_s)[1]


def simulate_star_tracker_profile(s, c, profile, dt_s):
    t = [0.0]
    b = [s.drift_bias_mrp]
    sig = []
    for i, x in enumerate(profile):
        s, m = step_star_tracker(s, x, c, dt_s)
        t.append((i + 1) * dt_s)
        b.append(s.drift_bias_mrp)
        sig.append(m.sigma_bn)
    return StarTrackerProfileResult(tuple(t), tuple(b), tuple(sig))


def _build_nominal_star_tracker_config_base_impl(
    drift_rate_mrp_s: tuple[float, float, float] = (0.01, 0, 0),
    max_drift_norm: float = 0.1,
    dcm_cb: tuple[float, ...] = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
    degradation: StarTrackerDegradation | None = None,
    fault_specs: list[FaultSpec] | None = None,
) -> StarTrackerConfig:
    """Build a nominal StarTrackerConfig with all parameters defaulted and optional degradation."""
    config = StarTrackerConfig(
        drift_rate_mrp_s=drift_rate_mrp_s,
        max_drift_norm=max_drift_norm,
        dcm_cb=dcm_cb,
    )
    if degradation is not None:
        config = apply_star_tracker_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_star_tracker_faults(config, fault_specs)
    return config


def apply_star_tracker_config_faults(config: StarTrackerConfig, fault_specs: list[FaultSpec]) -> StarTrackerConfig:
    """Apply fault specifications to Star Tracker configuration."""
    new_config = config

    for spec in fault_specs:
        if not isinstance(spec.fault_type, StarTrackerFaultType):
            continue

        if spec.fault_type == StarTrackerFaultType.BiasDrift:
            original_rate = new_config.drift_rate_mrp_s
            degraded_rate = tuple(r + float(spec.magnitude) * 0.01 for r in original_rate)
            new_config = replace(new_config, drift_rate_mrp_s=degraded_rate)

        elif spec.fault_type == StarTrackerFaultType.AccuracyLoss:
            new_config = replace(
                new_config,
                max_drift_norm=new_config.max_drift_norm * (1.0 + float(spec.magnitude) * 0.1),
            )

        elif spec.fault_type == StarTrackerFaultType.FieldOfViewObstruction:
            new_config = replace(
                new_config,
                max_drift_norm=new_config.max_drift_norm * (1.0 + float(spec.magnitude)),
            )

    return new_config


def require_basilisk_star_tracker() -> None:
    try:
        from Basilisk.simulation import starTracker  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk star tracker module is unavailable: {exc}") from exc


def _matrix3(value):
    values = list(value)
    if len(values) == 3 and all(hasattr(row, "__iter__") for row in values):
        return [[float(x) for x in list(row)[:3]] for row in values]
    if len(values) != 9:
        raise ValueError("expected a 3x3 matrix or 9 flat values")
    return [[float(values[0]), float(values[1]), float(values[2])], [float(values[3]), float(values[4]), float(values[5])], [float(values[6]), float(values[7]), float(values[8])]]


def build_star_tracker(
    model_tag: str = "componentStarTracker",
    sc_state_msg: Any | None = None,
    sigma_sigma_c: float = 1e-4,
    sigma_omega_c_rad_s: float = 1e-4,
    dcm_cb: tuple[float, ...] | tuple[tuple[float, float, float], ...] = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
):
    """Create Basilisk ``starTracker.StarTracker`` with configurable parameters.

    Parameters
    ----------
    model_tag : str, optional
        Model tag for the sensor.
    sc_state_msg : optional
        Spacecraft state message (SCStatesMsg) to subscribe to.
    sigma_sigma_c : float, optional
        Attitude noise standard deviation (MRP) (default 1e-4).
    sigma_omega_c_rad_s : float, optional
        Angular rate noise standard deviation in rad/s (default 1e-4).
    dcm_cb : sequence, optional
        Sensor-to-body DCM.  Basilisk StarTracker does not model FOV, star-count
        or availability; those belong to a separately labeled project proxy.

    Returns
    -------
    object
        Basilisk starTracker instance with configured parameters.
    """
    require_basilisk_star_tracker()
    from Basilisk.simulation import starTracker

    st = starTracker.StarTracker()
    st.ModelTag = model_tag

    sigma_sq = float(sigma_sigma_c) ** 2
    st.PMatrix = [[sigma_sq, 0.0, 0.0], [0.0, sigma_sq, 0.0], [0.0, 0.0, sigma_sq]]
    st.walkBounds = [[float(sigma_omega_c_rad_s)], [float(sigma_omega_c_rad_s)], [float(sigma_omega_c_rad_s)]]
    st.dcm_CB = _matrix3(dcm_cb)

    if sc_state_msg is not None:
        st.scStateInMsg.subscribeTo(sc_state_msg)
    return st

# Component fault/degradation compatibility wrappers
from .degradation import StarTrackerDegradationRate
from .degradation import compute_degradation_state
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_star_tracker_config_base = _build_nominal_star_tracker_config_base_impl

def build_nominal_star_tracker_config(
    *args,
    degradation: StarTrackerDegradation | None = None,
    degradation_rate: StarTrackerDegradationRate | None = None,
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
    config = _build_nominal_star_tracker_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_star_tracker_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_star_tracker_faults(config, fault_specs)
    return config

