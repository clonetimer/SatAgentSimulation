from __future__ import annotations

# Local component degradation declarations; component-local module is the canonical source.
"""反应轮退化模块。

定义反应轮退化状态类，用于模拟反应轮的性能退化过程。
"""
from dataclasses import dataclass
from typing import Literal

from ..degradation_base import DegradationModel, DegradationRate, DegradationState


@dataclass(frozen=True)
class ReactionWheelDegradation(DegradationState):
    """反应轮退化状态。

    记录反应轮的关键退化参数，包括摩擦增加和轴承磨损。

    Attributes:
        friction_increase_pct: 摩擦增加百分比（0-100）
        bearing_wear_factor: 轴承磨损因子（0-1，越大表示磨损越严重）
    """
    friction_increase_pct: float = 0.0
    bearing_wear_factor: float = 0.0

    @property
    def component_type(self) -> str:
        """返回部件类型标识。

        Returns:
            str: "reaction_wheel"
        """
        return "reaction_wheel"


@dataclass(frozen=True)
class ReactionWheelDegradationRate(DegradationRate):
    """反应轮退化速率。

    定义反应轮各退化参数的变化速率，支持固定和随机两种模式。

    Attributes:
        friction_increase_rate_per_hour: 每小时摩擦增加速率（百分比）
        bearing_wear_rate_per_year: 每年轴承磨损速率
        model: 退化模型类型（固定或随机）
    """
    friction_increase_rate_per_hour: float = 0.001  # 每小时增加 0.001%
    bearing_wear_rate_per_year: float = 0.02  # 每年磨损因子增加 0.02
    model: Literal[DegradationModel.FIXED, DegradationModel.RANDOM] = DegradationModel.FIXED

    @property
    def component_type(self) -> str:
        """返回部件类型标识。

        Returns:
            str: "reaction_wheel"
        """
        return "reaction_wheel"

# L2-light local time-function degradation mechanism declarations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Any, Callable

from ..degradation_base import ParameterEffect, TimeFunctionDegradation


BASE_EFFECTIVE_PARAMS: dict[str, float] = {'max_torque_nm': 0.08, 'wheel_speed_rad_s': 250.0, 'friction_torque_nm': 0.0005, 'speed_sensor_bias_rad_s': 0.0, 'availability': 1.0}


class ReactionWheelL2DegradationType(str, Enum):
    BEARING_FRICTION_GROWTH = 'bearing_friction_growth'
    TORQUE_CONSTANT_DRIFT = 'torque_constant_drift'


def _effects_for(kind: ReactionWheelL2DegradationType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        ReactionWheelL2DegradationType.BEARING_FRICTION_GROWTH: (
            ParameterEffect('friction_torque_nm', 'grow_multiply', 5.0, 'bearing wear grows drag/friction torque'),
            ParameterEffect('max_torque_nm', 'degrade_multiply', 0.15, 'bearing wear grows drag/friction torque'),
        ),
        ReactionWheelL2DegradationType.TORQUE_CONSTANT_DRIFT: (
            ParameterEffect('max_torque_nm', 'degrade_multiply', 0.2, 'motor torque constant drifts lower torque authority'),
        ),
    }
    return effects_by_type[kind]


PHYSICAL_MECHANISMS = {
    ReactionWheelL2DegradationType.BEARING_FRICTION_GROWTH: 'bearing wear grows drag/friction torque',
    ReactionWheelL2DegradationType.TORQUE_CONSTANT_DRIFT: 'motor torque constant drifts lower torque authority',
}


@dataclass
class ReactionWheelL2LightDegradation(TimeFunctionDegradation):
    """Base L2-light degradation for reaction_wheel."""
    degradation_type_enum: ReactionWheelL2DegradationType = ReactionWheelL2DegradationType.BEARING_FRICTION_GROWTH
    start_s: float = 60.0
    rate_per_s: float = 0.0008
    max_fraction: float = 0.6
    severity: float = 1.0
    law: str = "saturating_exponential"

    def __post_init__(self) -> None:
        self.name = type(self).__name__
        self.degradation_type = self.degradation_type_enum.value
        self.effects = _effects_for(self.degradation_type_enum)
        self.physical_mechanism = PHYSICAL_MECHANISMS[self.degradation_type_enum]


@dataclass
class ReactionWheelBearingFrictionGrowthDegradation(ReactionWheelL2LightDegradation):
    """bearing wear grows drag/friction torque."""
    degradation_type_enum: ReactionWheelL2DegradationType = ReactionWheelL2DegradationType.BEARING_FRICTION_GROWTH
    start_s: float = 60.0
    rate_per_s: float = 0.001


@dataclass
class ReactionWheelTorqueConstantDriftDegradation(ReactionWheelL2LightDegradation):
    """motor torque constant drifts lower torque authority."""
    degradation_type_enum: ReactionWheelL2DegradationType = ReactionWheelL2DegradationType.TORQUE_CONSTANT_DRIFT
    start_s: float = 150.0
    rate_per_s: float = 0.0007


NATIVE_FRICTION_BASELINE = {
    "fCoulomb": 5.0e-4,
    "fStatic": 7.5e-4,
    "betaStatic": 10.0,
    "cViscous": 1.0e-6,
}


def apply_native_friction_factor(spec, friction_factor: float):
    """Map an ADCS/component friction factor to Basilisk-native RW fields.

    ``friction_factor <= 0`` preserves the supplied spec. Positive values turn
    on Basilisk wheel friction and scale a documented component baseline.  This
    conversion belongs to the RW component layer; subsystem builders only call
    it while composing their wheel set.
    """

    from dataclasses import replace as _replace

    factor = max(0.0, float(friction_factor))
    if factor <= 0.0:
        return spec
    f_coulomb = max(float(getattr(spec, "fCoulomb", 0.0)), NATIVE_FRICTION_BASELINE["fCoulomb"] * factor)
    f_static = max(float(getattr(spec, "fStatic", 0.0)), NATIVE_FRICTION_BASELINE["fStatic"] * factor)
    beta_static = float(getattr(spec, "betaStatic", -1.0))
    if beta_static <= 0.0:
        beta_static = NATIVE_FRICTION_BASELINE["betaStatic"]
    c_viscous = max(float(getattr(spec, "cViscous", 0.0)), NATIVE_FRICTION_BASELINE["cViscous"] * factor)
    return _replace(
        spec,
        use_rw_friction=True,
        fCoulomb=f_coulomb,
        fStatic=f_static,
        betaStatic=beta_static,
        cViscous=c_viscous,
    )


def apply_native_friction_factor_to_specs(wheel_specs, friction_factor: float):
    """Apply :func:`apply_native_friction_factor` to a wheel-spec collection."""

    return tuple(apply_native_friction_factor(spec, friction_factor) for spec in wheel_specs)


def apply_reaction_wheel_spec_degradation(spec, degradation: ReactionWheelDegradation):
    """Map component degradation state to Basilisk-native ``ReactionWheelSpec``."""

    factor = 0.0
    if float(degradation.friction_increase_pct) > 0.0 or float(degradation.bearing_wear_factor) > 0.0:
        factor = 1.0 + max(0.0, float(degradation.friction_increase_pct)) / 100.0 + 4.0 * max(0.0, float(degradation.bearing_wear_factor))
    degraded = apply_native_friction_factor(spec, factor)
    if float(degradation.bearing_wear_factor) <= 0.0:
        return degraded
    from dataclasses import replace as _replace
    wear = min(1.0, max(0.0, float(degradation.bearing_wear_factor)))
    return _replace(
        degraded,
        u_max_nm=float(degraded.u_max_nm) * max(0.0, 1.0 - 0.5 * wear),
        omega_max_rad_s=float(degraded.omega_max_rad_s) * max(0.0, 1.0 - wear),
    )


def base_effective_params() -> dict[str, float]:
    """Return effective parameters used by L2-light event/time-function runner."""
    return dict(BASE_EFFECTIVE_PARAMS)


def default_degradations() -> list[ReactionWheelL2LightDegradation]:
    """Return representative L2-light time-function degradations."""
    return [
        ReactionWheelBearingFrictionGrowthDegradation(),
        ReactionWheelTorqueConstantDriftDegradation(),
    ]



def _base_effective_output(component: str, params: Mapping[str, Any], t_s: float) -> dict[str, Any]:
    """Generic local evaluator for event/time-function runner traces."""
    availability = float(params.get("availability", params.get("string_availability", 1.0)))
    factors = [
        float(v) for k, v in params.items()
        if (k.endswith("_factor") or k in {"efficiency", "cell_efficiency", "pa_efficiency", "responsivity", "sensitivity_factor"})
        and isinstance(v, (int, float))
    ]
    performance_factor = availability
    for value in factors[:6]:
        performance_factor *= max(0.0, value)
    quality = max(0.0, min(1.5, performance_factor))
    return {
        "component": component,
        "t_s": float(t_s),
        "availability": availability,
        "performance_factor": performance_factor,
        "quality_metric": quality,
        "effective_params": dict(params),
    }

def evaluate_effective_params(component: str, params: Mapping[str, Any], t_s: float) -> dict[str, Any]:
    """Evaluate the component at the current effective parameters.

    This intentionally stays algebraic/time-query based for L2-light; it does
    not integrate internal physical states.
    """
    output = _base_effective_output(component, params, t_s)
    numeric = {k: float(v) for k, v in params.items() if isinstance(v, (int, float))}
    output["observable_metric"] = sum(v for k, v in numeric.items() if not k.endswith("_error") and not k.endswith("_bias"))
    return output


__all__ = [
    "BASE_EFFECTIVE_PARAMS", "NATIVE_FRICTION_BASELINE", "native_runtime_effect_supported", "apply_native_runtime_effect", 'ReactionWheelL2DegradationType', 'ReactionWheelDegradation', "base_effective_params", "default_degradations", "evaluate_effective_params",
    "apply_native_friction_factor", "apply_native_friction_factor_to_specs", "apply_reaction_wheel_spec_degradation",
    'ReactionWheelBearingFrictionGrowthDegradation',
    'ReactionWheelTorqueConstantDriftDegradation',
]

# --- Local config-level degradation application support migrated from root legacy module ---
import random
from dataclasses import replace
from dataclasses import fields as _dc_fields, is_dataclass as _is_dataclass

def _random_factor(model) -> float:
    if model == DegradationModel.RANDOM:
        return random.uniform(0.5, 1.5)
    return 1.0

def _elapsed_multiplier(rate_field_name: str, years_elapsed: float) -> float:
    years = max(0.0, float(years_elapsed))
    if rate_field_name.endswith("_per_day"):
        return years * 365.25
    if rate_field_name.endswith("_per_hour"):
        return years * 365.25 * 24.0
    if rate_field_name.endswith("_per_year"):
        return years
    # Per-burn/per-cycle/per-million-revolution counters are not calendar time.
    # Builders expose the generic years_elapsed scalar for API consistency; for
    # these counters the caller can pass the desired equivalent count.
    return years

def _tokens(name: str) -> set[str]:
    ignored = {"pct", "factor", "rate", "per", "year", "day", "hour", "cycle", "burn", "million", "rev", "kg", "s"}
    return {part for part in name.split("_") if part and part not in ignored}

def _state_kwargs_from_rate(rate, state_cls, years_elapsed: float) -> dict:
    factor = _random_factor(getattr(rate, "model", DegradationModel.FIXED))
    rate_values = {
        key: value
        for key, value in vars(rate).items()
        if key != "model" and isinstance(value, (int, float))
    }
    kwargs = {}
    for f in _dc_fields(state_cls):
        state_name = f.name
        state_tokens = _tokens(state_name)
        candidates = []
        for rate_name, rate_value in rate_values.items():
            rate_tokens = _tokens(rate_name)
            overlap = len(state_tokens & rate_tokens)
            if overlap == len(state_tokens) or overlap == len(rate_tokens):
                candidates.append((overlap, rate_name, rate_value))
        if not candidates:
            continue
        _, rate_name, rate_value = sorted(candidates, key=lambda item: (-item[0], item[1]))[0]
        kwargs[state_name] = float(rate_value) * _elapsed_multiplier(rate_name, years_elapsed) * factor
    return kwargs

def _bounded_fraction(value: float, lower: float = 0.0, upper: float | None = None) -> float:
    value = max(lower, float(value))
    if upper is not None:
        value = min(upper, value)
    return value

def _loss(value, pct: float, floor: float = 0.0):
    return _bounded_fraction(float(value) * (1.0 - float(pct) / 100.0), floor)

def _increase(value, pct: float, floor: float = 0.0):
    return _bounded_fraction(float(value) * (1.0 + float(pct) / 100.0), floor)

def _tuple_loss(values, pct: float):
    return tuple(_loss(v, pct) for v in values)

def _tuple_increase(values, pct: float):
    return tuple(_increase(v, pct) for v in values)

def _safe_replace(config, **updates):
    if not updates:
        return config
    if _is_dataclass(config):
        allowed = {f.name for f in _dc_fields(config)}
        filtered = {key: value for key, value in updates.items() if key in allowed}
        if filtered:
            return replace(config, **filtered)
    return config


def compute_degradation_state(rate, years_elapsed: float = 1.0):
    """Compute this component's concrete degradation state from its local rate class."""
    if isinstance(rate, ReactionWheelDegradationRate):
        return ReactionWheelDegradation(**_state_kwargs_from_rate(rate, ReactionWheelDegradation, years_elapsed))
    raise ValueError(f"不支持的退化速率类型: {type(rate).__name__}")

def apply_reaction_wheel_degradation(rw_config, degradation: ReactionWheelDegradation):
    if hasattr(rw_config, "use_rw_friction") and hasattr(rw_config, "fCoulomb"):
        return apply_reaction_wheel_spec_degradation(rw_config, degradation)
    updates = {}
    if hasattr(rw_config, "damping_nms"):
        updates["damping_nms"] = _tuple_increase(rw_config.damping_nms, degradation.friction_increase_pct)
    if hasattr(rw_config, "max_speed_rad_s"):
        updates["max_speed_rad_s"] = tuple(_bounded_fraction(float(v) * (1.0 - degradation.bearing_wear_factor), 0.0) for v in rw_config.max_speed_rad_s)
    if hasattr(rw_config, "max_motor_torque_nm"):
        updates["max_motor_torque_nm"] = tuple(_bounded_fraction(float(v) * (1.0 - 0.5 * degradation.bearing_wear_factor), 0.0) for v in rw_config.max_motor_torque_nm)
    return _safe_replace(rw_config, **updates)



# --- Basilisk runtime effect application (component-owned) ---
SnapshotGetter = Callable[[str, float], float]


def _rw_entries(rw_target: Any) -> tuple[Any, ...]:
    effector = getattr(rw_target, "effector", rw_target)
    data = getattr(effector, "ReactionWheelData", None)
    if data is None:
        return ()
    try:
        return tuple(data[index] for index in range(len(data)))
    except Exception:
        return ()


def native_runtime_effect_supported(rw_target: Any, effect_parameter: str) -> bool:
    """Return whether a logical RW effect has a writable native target."""

    if str(effect_parameter) not in {"friction_torque_nm", "max_torque_nm", "availability"}:
        return False
    entries = _rw_entries(rw_target)
    if not entries:
        return False
    if str(effect_parameter) == "friction_torque_nm":
        return all(hasattr(item, "fCoulomb") for item in entries)
    return all(hasattr(item, "u_max") for item in entries)


def _effect_value(effect: Any, name: str, default: Any) -> Any:
    if isinstance(effect, Mapping):
        return effect.get(name, default)
    return getattr(effect, name, default)


def _snapshot_value(snapshot_getter: SnapshotGetter, key: str, current: float) -> float:
    return float(snapshot_getter(key, float(current)))


def apply_native_runtime_effect(
    rw_target: Any,
    effect: Any,
    *,
    factor: float = 1.0,
    reverse: bool = False,
    snapshot_prefix: str = "reaction_wheel",
    snapshot_getter: SnapshotGetter,
) -> tuple[bool, dict[str, float], dict[str, float], str]:
    """Apply one logical RW effect to Basilisk ``RWConfigPayload`` entries.

    ``snapshot_getter`` owns lifecycle/recovery storage.  It receives a stable
    native-field key and the current value, and returns the original baseline.
    This keeps event scheduling outside the component while retaining all
    physical parameter mapping inside the component package.
    """

    parameter = str(_effect_value(effect, "parameter", ""))
    operation = str(_effect_value(effect, "operation", ""))
    magnitude = float(_effect_value(effect, "magnitude", 0.0))
    severity = float(_effect_value(effect, "severity", 1.0))
    scaled = magnitude * severity * float(factor)
    entries = _rw_entries(rw_target)
    if not entries or not native_runtime_effect_supported(rw_target, parameter):
        return False, {}, {}, "unsupported component-native RW effect"

    before: dict[str, float] = {}
    after: dict[str, float] = {}

    def update(entry: Any, index: int, attr: str, forward_value: float, *, compose_min: bool = False) -> None:
        key = f"{snapshot_prefix}.ReactionWheelData[{index}].{attr}"
        current = float(getattr(entry, attr))
        baseline = _snapshot_value(snapshot_getter, key, current)
        before[key] = current
        if reverse:
            value = baseline
        else:
            value = float(forward_value)
            if compose_min:
                value = min(current, value)
        setattr(entry, attr, value)
        after[key] = float(getattr(entry, attr))

    for index, entry in enumerate(entries):
        if parameter == "friction_torque_nm":
            f_key = f"{snapshot_prefix}.ReactionWheelData[{index}].fCoulomb"
            f_current = float(getattr(entry, "fCoulomb"))
            f_base = _snapshot_value(snapshot_getter, f_key, f_current)
            if operation == "add":
                f_coulomb = f_base + scaled
            elif operation == "grow_multiply":
                effective_base = max(f_base, float(NATIVE_FRICTION_BASELINE["fCoulomb"]))
                f_coulomb = effective_base * (1.0 + abs(magnitude) * float(factor))
            elif operation == "multiply":
                f_coulomb = max(f_base, float(NATIVE_FRICTION_BASELINE["fCoulomb"])) * max(0.0, scaled)
            else:
                f_coulomb = max(f_base, float(NATIVE_FRICTION_BASELINE["fCoulomb"])) + abs(scaled)

            update(entry, index, "fCoulomb", f_coulomb)
            update(entry, index, "fStatic", max(1.5 * f_coulomb, float(NATIVE_FRICTION_BASELINE["fStatic"])))
            update(entry, index, "betaStatic", float(NATIVE_FRICTION_BASELINE["betaStatic"]))
            update(
                entry,
                index,
                "cViscous",
                max(
                    float(NATIVE_FRICTION_BASELINE["cViscous"]),
                    abs(f_coulomb) * 5.0e-4,
                ),
            )
            continue

        u_key = f"{snapshot_prefix}.ReactionWheelData[{index}].u_max"
        u_current = float(getattr(entry, "u_max"))
        u_base = _snapshot_value(snapshot_getter, u_key, u_current)
        if operation == "multiply":
            u_value = u_base * max(0.0, scaled)
        elif operation == "degrade_multiply":
            u_value = u_base * max(0.0, 1.0 - abs(magnitude) * float(factor))
        elif operation == "override":
            u_value = max(0.0, magnitude)
        else:
            u_value = u_base * max(0.0, 1.0 - abs(scaled))
        update(entry, index, "u_max", u_value, compose_min=not reverse)

    return True, before, after, "component-native RWConfigPayload fields updated"
