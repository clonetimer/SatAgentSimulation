from __future__ import annotations

# Local component degradation declarations; component-local module is the canonical source.
"""控制力矩陀螺退化模块。

定义控制力矩陀螺(CMG)的退化状态和退化速率类，用于模拟CMG的性能退化过程。

文献支撑:
- Inampudi R, Gordeuk J. Simulation of Malfunctions for the ISS Double-Gimbal Control Moment Gyroscope[C]. AIAA, 2015.
- Shen Q, Yue C, Yu X, et al. Fault Modeling, Estimation, and Fault-Tolerant Steering Logic Design for Single-Gimbal Control Moment Gyro[J]. IEEE Transactions on Control Systems Technology, 2020.
- Yue C, Shen Q, Cao X, et al. Fault Modeling of General Momentum Exchange Devices in Spacecraft Attitude Control Systems[J]. Journal of the Franklin Institute, 2020.
"""
from dataclasses import dataclass
from typing import Literal

from ..degradation_base import DegradationModel, DegradationRate, DegradationState


@dataclass(frozen=True)
class CMGDegradation(DegradationState):
    """控制力矩陀螺退化状态。

    记录CMG的关键退化参数，包括轴承磨损、力矩衰减和电机效率下降。

    Attributes:
        bearing_friction_increase_pct: 轴承摩擦增加百分比（0-100）
        torque_output_loss_pct: 力矩输出损失百分比（0-100）
        spin_motor_efficiency_loss_pct: 飞轮电机效率损失百分比（0-100）
        gimbal_motor_efficiency_loss_pct: 框架电机效率损失百分比（0-100）
        power_consumption_increase_pct: 功耗增加百分比（0-100）
        thermal_resistance_increase_pct: 热阻增加百分比（0-100）
    """
    bearing_friction_increase_pct: float = 0.0
    torque_output_loss_pct: float = 0.0
    spin_motor_efficiency_loss_pct: float = 0.0
    gimbal_motor_efficiency_loss_pct: float = 0.0
    power_consumption_increase_pct: float = 0.0
    thermal_resistance_increase_pct: float = 0.0

    @property
    def component_type(self) -> str:
        return "cmg"


@dataclass(frozen=True)
class CMGDegradationRate(DegradationRate):
    """控制力矩陀螺退化速率。

    定义CMG各退化参数的变化速率，支持固定和随机两种模式。

    Attributes:
        bearing_friction_rate_per_million_rev: 每百万转轴承摩擦增加速率（百分比）
        torque_loss_rate_per_million_rev: 每百万转力矩损失速率（百分比）
        spin_motor_efficiency_loss_rate_per_year: 每年飞轮电机效率损失速率（百分比）
        gimbal_motor_efficiency_loss_rate_per_cycle: 每周期框架电机效率损失速率（百分比）
        power_consumption_increase_rate_per_year: 每年功耗增加速率（百分比）
        thermal_resistance_increase_rate_per_year: 每年热阻增加速率（百分比）
    """
    bearing_friction_rate_per_million_rev: float = 0.1
    torque_loss_rate_per_million_rev: float = 0.05
    spin_motor_efficiency_loss_rate_per_year: float = 0.5
    gimbal_motor_efficiency_loss_rate_per_cycle: float = 0.01
    power_consumption_increase_rate_per_year: float = 0.3
    thermal_resistance_increase_rate_per_year: float = 0.2
    model: Literal[DegradationModel.FIXED, DegradationModel.RANDOM] = DegradationModel.FIXED

    @property
    def component_type(self) -> str:
        return "cmg"

# L2-light local time-function degradation mechanism declarations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Any

from ..degradation_base import ParameterEffect, TimeFunctionDegradation


BASE_EFFECTIVE_PARAMS: dict[str, float] = {'gimbal_rate_limit_rad_s': 0.8, 'wheel_momentum_nms': 15.0, 'friction_torque_nm': 0.002, 'torque_authority_factor': 1.0}


class CmgL2DegradationType(str, Enum):
    GIMBAL_FRICTION_GROWTH = 'gimbal_friction_growth'
    MOMENTUM_LOSS = 'momentum_loss'


def _effects_for(kind: CmgL2DegradationType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        CmgL2DegradationType.GIMBAL_FRICTION_GROWTH: (
            ParameterEffect('friction_torque_nm', 'grow_multiply', 1.5, 'gimbal bearing wear increases friction'),
            ParameterEffect('gimbal_rate_limit_rad_s', 'degrade_multiply', 0.25, 'gimbal bearing wear increases friction'),
        ),
        CmgL2DegradationType.MOMENTUM_LOSS: (
            ParameterEffect('wheel_momentum_nms', 'degrade_multiply', 0.2, 'wheel drag slowly reduces available momentum'),
        ),
    }
    return effects_by_type[kind]


PHYSICAL_MECHANISMS = {
    CmgL2DegradationType.GIMBAL_FRICTION_GROWTH: 'gimbal bearing wear increases friction',
    CmgL2DegradationType.MOMENTUM_LOSS: 'wheel drag slowly reduces available momentum',
}


@dataclass
class CmgL2LightDegradation(TimeFunctionDegradation):
    """Base L2-light degradation for cmg."""
    degradation_type_enum: CmgL2DegradationType = CmgL2DegradationType.GIMBAL_FRICTION_GROWTH
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
class CmgGimbalFrictionGrowthDegradation(CmgL2LightDegradation):
    """gimbal bearing wear increases friction."""
    degradation_type_enum: CmgL2DegradationType = CmgL2DegradationType.GIMBAL_FRICTION_GROWTH
    start_s: float = 60.0
    rate_per_s: float = 0.001


@dataclass
class CmgMomentumLossDegradation(CmgL2LightDegradation):
    """wheel drag slowly reduces available momentum."""
    degradation_type_enum: CmgL2DegradationType = CmgL2DegradationType.MOMENTUM_LOSS
    start_s: float = 150.0
    rate_per_s: float = 0.0007


def base_effective_params() -> dict[str, float]:
    """Return effective parameters used by L2-light event/time-function runner."""
    return dict(BASE_EFFECTIVE_PARAMS)


def default_degradations() -> list[CmgL2LightDegradation]:
    """Return representative L2-light time-function degradations."""
    return [
        CmgGimbalFrictionGrowthDegradation(),
        CmgMomentumLossDegradation(),
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
    "BASE_EFFECTIVE_PARAMS", 'CmgL2DegradationType', 'CMGDegradation', "base_effective_params", "default_degradations", "evaluate_effective_params",
    'CmgGimbalFrictionGrowthDegradation',
    'CmgMomentumLossDegradation',
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
    if isinstance(rate, CMGDegradationRate):
        return CMGDegradation(**_state_kwargs_from_rate(rate, CMGDegradation, years_elapsed))
    raise ValueError(f"不支持的退化速率类型: {type(rate).__name__}")

def apply_cmg_degradation(cmg_config, degradation: CMGDegradation):
    updates = {}
    if hasattr(cmg_config, "gimbal_rate_limit_rad_s") and cmg_config.gimbal_rate_limit_rad_s is not None:
        updates["gimbal_rate_limit_rad_s"] = _loss(cmg_config.gimbal_rate_limit_rad_s, degradation.bearing_friction_increase_pct + degradation.gimbal_motor_efficiency_loss_pct)
    if hasattr(cmg_config, "wheel_speed_limit_rad_s") and cmg_config.wheel_speed_limit_rad_s is not None:
        updates["wheel_speed_limit_rad_s"] = _loss(cmg_config.wheel_speed_limit_rad_s, degradation.spin_motor_efficiency_loss_pct)
    if hasattr(cmg_config, "wheel_speed_rad_s"):
        updates["wheel_speed_rad_s"] = _loss(cmg_config.wheel_speed_rad_s, degradation.torque_output_loss_pct)
    return _safe_replace(cmg_config, **updates)



# --- Basilisk runtime degradation application (component-owned) ---


def _iter_vscmg_data(target: object) -> list[Any]:
    data = getattr(target, "VSCMGData", None)
    if data is None and hasattr(target, "effector"):
        data = getattr(target.effector, "VSCMGData", None)
    if data is None:
        return []
    try:
        count = len(data)
    except Exception:
        return []
    out: list[Any] = []
    for idx in range(int(count)):
        try:
            out.append(data[idx])
        except Exception:
            continue
    return out


def _set_native_attr(obj: object, attr: str, value: Any, *, set_attr=None) -> bool:
    if obj is None or not hasattr(obj, attr):
        return False
    if set_attr is not None:
        set_attr(obj, attr, value)
    else:
        setattr(obj, attr, value)
    return True

def apply_runtime_cmg_degradation(target: object, *, friction_growth: float = 0.0, torque_loss_fraction: float = 0.0, set_attr=None) -> tuple[dict[str, object], ...]:
    """Apply component-owned VSCMG degradation to native payload fields."""
    data = _iter_vscmg_data(target)
    mutations: list[dict[str, object]] = []
    for idx, unit in enumerate(data):
        changed: list[str] = []
        if friction_growth > 0.0:
            for attr in ("wheelLinearFrictionRatio", "gimbalLinearFrictionRatio"):
                base = float(getattr(unit, attr, 0.0))
                value = base + friction_growth if base <= 0.0 else base * (1.0 + friction_growth)
                if _set_native_attr(unit, attr, value, set_attr=set_attr):
                    changed.append(attr)
        if torque_loss_fraction > 0.0:
            factor = max(0.0, 1.0 - float(torque_loss_fraction))
            for attr in ("u_s_max", "u_g_max"):
                if hasattr(unit, attr) and _set_native_attr(unit, attr, float(getattr(unit, attr)) * factor, set_attr=set_attr):
                    changed.append(attr)
        if changed:
            mutations.append({"component": "cmg", "index": idx, "changed": tuple(changed)})
    return tuple(mutations)

