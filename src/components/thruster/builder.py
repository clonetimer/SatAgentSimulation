"""Basilisk helpers for the thruster component."""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .faults import apply_thruster_faults
from .faults import FaultSpec

from dataclasses import dataclass
from typing import Any, Sequence

from .model import (
    G0,
    apply_thruster_config_faults,
    build_nominal_thruster_command_config,
    build_nominal_thruster_config,
    build_nominal_thruster_physical_config,
    compute_thruster_pulse,
    shape_thruster_on_time,
    simulate_pulse_train,
)
from .schemas import (
    ThrusterCommandConfig,
    ThrusterPhysicalConfig,
    ThrusterPulseResult,
    ThrusterPulseTrainResult,
)


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
        location='src/components/thruster/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )


@dataclass(frozen=True)
class ThrusterDynamicEffectorBuild:
    effector: Any
    factory: Any
    thruster_refs: tuple[Any, ...]


def basilisk_available() -> bool:
    return _messaging is not None and _sysModel is not None


def require_basilisk_thruster() -> None:
    try:
        from Basilisk.utilities import simIncludeThruster  # noqa: F401
        from Basilisk.simulation import thrusterDynamicEffector  # noqa: F401
        from Basilisk.architecture import messaging  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk thruster modules are unavailable: {exc}") from exc


def _seq_get(seq: Sequence[Any] | None, idx: int, default: Any = None) -> Any:
    if seq is None:
        return default
    try:
        if idx < len(seq):
            return seq[idx]
    except TypeError:
        return default
    return default


def _coeff_list(value: Any) -> list[float]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        raise TypeError("thruster blowdown coefficients must be a numeric sequence")
    try:
        return [float(x) for x in value]
    except TypeError:
        return []


def _create_kwargs_from_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Translate project thruster spec dictionaries to verified Basilisk 2.11.0 kwargs."""

    kwargs: dict[str, Any] = {
        "MaxThrust": float(spec["max_thrust_n"]),
        "steadyIsp": float(spec["isp_s"]),
        "MinOnTime": float(spec.get("min_on_time_s", 0.0)),
        "useMinPulseTime": bool(spec.get("use_min_pulse_time", float(spec.get("min_on_time_s", 0.0)) > 0.0)),
    }
    optional_float_map = {
        "area_nozzle_m2": "areaNozzle",
        "thruster_mag_disp": "thrusterMagDisp",
        "cutoff_frequency_rad_s": "cutoffFrequency",
        "max_swirl_torque_nm": "MaxSwirlTorque",
    }
    for project_key, native_key in optional_float_map.items():
        if project_key in spec and spec[project_key] is not None:
            kwargs[native_key] = float(spec[project_key])
    for project_key, native_key in (
        ("thr_blowdown_coeff", "thrBlowDownCoeff"),
        ("isp_blowdown_coeff", "ispBlowDownCoeff"),
    ):
        coeffs = _coeff_list(spec.get(project_key))
        if coeffs:
            kwargs[native_key] = coeffs
    if spec.get("label"):
        kwargs["label"] = str(spec["label"])
    return kwargs


def build_thruster_dynamic_effector_bundle(model_tag: str, thruster_specs: list[dict]) -> ThrusterDynamicEffectorBuild:
    require_basilisk_thruster()
    from Basilisk.simulation import thrusterDynamicEffector
    from Basilisk.utilities import simIncludeThruster

    factory = simIncludeThruster.thrusterFactory()
    for spec in thruster_specs:
        factory.create(
            str(spec.get("type", "MOOG_Monarc_1")),
            [float(x) for x in spec["location"]],
            [float(x) for x in spec["direction"]],
            **_create_kwargs_from_spec(spec),
        )
    effector = thrusterDynamicEffector.ThrusterDynamicEffector()
    effector.ModelTag = model_tag
    refs = tuple(factory.thrusterList.values())
    for thruster in refs:
        effector.addThruster(thruster)
    return ThrusterDynamicEffectorBuild(effector=effector, factory=factory, thruster_refs=refs)


def build_thruster_specs_from_configs(
    command_config: ThrusterCommandConfig,
    physical_config: ThrusterPhysicalConfig,
) -> list[dict[str, Any]]:
    """Build typed per-thruster native spec dictionaries from component schemas."""

    specs: list[dict[str, Any]] = []
    count = int(command_config.num_thrusters)
    for idx in range(count):
        thrust = _seq_get(physical_config.thrust_n, idx, _seq_get(physical_config.thrust_n, -1, 1.0))
        isp = _seq_get(physical_config.isp_s, idx, _seq_get(physical_config.isp_s, -1, 200.0))
        direction = _seq_get(physical_config.directions_b, idx, (1.0, 0.0, 0.0))
        location = _seq_get(physical_config.lever_arms_b_m, idx, (0.0, 0.0, 0.0))
        label = _seq_get(physical_config.labels, idx, None)
        if not label:
            label = f"T{idx + 1}"
        spec: dict[str, Any] = {
            "location": [float(x) for x in location],
            "direction": [float(x) for x in direction],
            "max_thrust_n": float(thrust),
            "isp_s": float(isp),
            "min_on_time_s": float(command_config.min_pulse_s),
            "use_min_pulse_time": bool(command_config.use_min_pulse_time),
            "label": str(label)[:5],
        }
        for attr in (
            "area_nozzle_m2",
            "thruster_mag_disp",
            "cutoff_frequency_rad_s",
            "max_swirl_torque_nm",
            "thr_blowdown_coeff",
            "isp_blowdown_coeff",
        ):
            value = _seq_get(getattr(physical_config, attr, ()), idx, None)
            if value is not None:
                spec[attr] = value
        specs.append(spec)
    return specs


def build_thruster_dynamic_effector(model_tag: str, thruster_specs: list[dict]):
    return build_thruster_dynamic_effector_bundle(model_tag, thruster_specs).effector


def write_thruster_on_time_message(on_time_s):
    require_basilisk_thruster()
    from Basilisk.architecture import messaging

    payload = messaging.THRArrayOnTimeCmdMsgPayload()
    values = list(payload.OnTimeRequest)
    for idx, value in enumerate(on_time_s):
        if idx < len(values):
            values[idx] = max(0.0, float(value))
    payload.OnTimeRequest = values
    return messaging.THRArrayOnTimeCmdMsg().write(payload)


def attach_thruster_to_spacecraft(bundle: ThrusterDynamicEffectorBuild, spacecraft: object) -> None:
    spacecraft.addDynamicEffector(bundle.effector)
