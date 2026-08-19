from __future__ import annotations

# Local component degradation declarations; component-local module is the canonical source.
"""加热器退化模块。

定义加热器退化状态类，用于模拟加热器的性能退化过程。
"""
from dataclasses import dataclass
from typing import Literal

from ..degradation_base import DegradationModel, DegradationRate, DegradationState


@dataclass(frozen=True)
class HeaterDegradation(DegradationState):
    """加热器退化状态。

    记录加热器的关键退化参数，包括效率损失。

    Attributes:
        efficiency_loss_pct: 效率损失百分比（0-100）
        response_time_increase_pct: 响应时间增加百分比（0-100）
    """
    efficiency_loss_pct: float = 0.0
    response_time_increase_pct: float = 0.0

    @property
    def component_type(self) -> str:
        """返回部件类型标识。

        Returns:
            str: "heater"
        """
        return "heater"


@dataclass(frozen=True)
class HeaterDegradationRate(DegradationRate):
    """加热器退化速率。

    定义加热器各退化参数的变化速率，支持固定和随机两种模式。

    Attributes:
        efficiency_loss_rate_per_day: 每天效率损失速率（百分比）
        response_time_increase_rate_per_year: 每年响应时间增加速率（百分比）
        model: 退化模型类型（固定或随机）
    """
    efficiency_loss_rate_per_day: float = 0.001  # 每天效率损失 0.001%
    response_time_increase_rate_per_year: float = 0.5  # 每年响应时间增加 0.5%
    model: Literal[DegradationModel.FIXED, DegradationModel.RANDOM] = DegradationModel.FIXED

    @property
    def component_type(self) -> str:
        """返回部件类型标识。

        Returns:
            str: "heater"
        """
        return "heater"

# L2-light local time-function degradation mechanism declarations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Any

from ..degradation_base import ParameterEffect, TimeFunctionDegradation


BASE_EFFECTIVE_PARAMS: dict[str, float] = {'heater_power_w': 15.0, 'efficiency': 0.92, 'commanded_on': 1.0, 'thermal_overshoot_w': 0.0}


class HeaterL2DegradationType(str, Enum):
    HEATING_EFFICIENCY_DECAY = 'heating_efficiency_decay'
    RESISTANCE_DRIFT = 'resistance_drift'


def _effects_for(kind: HeaterL2DegradationType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        HeaterL2DegradationType.HEATING_EFFICIENCY_DECAY: (
            ParameterEffect('efficiency', 'degrade_multiply', 0.3, 'heater element aging lowers heat conversion efficiency'),
        ),
        HeaterL2DegradationType.RESISTANCE_DRIFT: (
            ParameterEffect('heater_power_w', 'degrade_multiply', 0.2, 'resistance drift reduces delivered heater power'),
        ),
    }
    return effects_by_type[kind]


PHYSICAL_MECHANISMS = {
    HeaterL2DegradationType.HEATING_EFFICIENCY_DECAY: 'heater element aging lowers heat conversion efficiency',
    HeaterL2DegradationType.RESISTANCE_DRIFT: 'resistance drift reduces delivered heater power',
}


@dataclass
class HeaterL2LightDegradation(TimeFunctionDegradation):
    """Base L2-light degradation for heater."""
    degradation_type_enum: HeaterL2DegradationType = HeaterL2DegradationType.HEATING_EFFICIENCY_DECAY
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
class HeaterHeatingEfficiencyDecayDegradation(HeaterL2LightDegradation):
    """heater element aging lowers heat conversion efficiency."""
    degradation_type_enum: HeaterL2DegradationType = HeaterL2DegradationType.HEATING_EFFICIENCY_DECAY
    start_s: float = 60.0
    rate_per_s: float = 0.001


@dataclass
class HeaterResistanceDriftDegradation(HeaterL2LightDegradation):
    """resistance drift reduces delivered heater power."""
    degradation_type_enum: HeaterL2DegradationType = HeaterL2DegradationType.RESISTANCE_DRIFT
    start_s: float = 150.0
    rate_per_s: float = 0.0007


def base_effective_params() -> dict[str, float]:
    """Return effective parameters used by L2-light event/time-function runner."""
    return dict(BASE_EFFECTIVE_PARAMS)


def default_degradations() -> list[HeaterL2LightDegradation]:
    """Return representative L2-light time-function degradations."""
    return [
        HeaterHeatingEfficiencyDecayDegradation(),
        HeaterResistanceDriftDegradation(),
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
    "BASE_EFFECTIVE_PARAMS", 'HeaterL2DegradationType', 'HeaterDegradation', "base_effective_params", "default_degradations", "evaluate_effective_params",
    'HeaterHeatingEfficiencyDecayDegradation',
    'HeaterResistanceDriftDegradation',
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
    if isinstance(rate, HeaterDegradationRate):
        return HeaterDegradation(**_state_kwargs_from_rate(rate, HeaterDegradation, years_elapsed))
    raise ValueError(f"不支持的退化速率类型: {type(rate).__name__}")

def apply_heater_degradation(heater_config, degradation: HeaterDegradation):
    updates = {}
    if hasattr(heater_config, "max_power_w"):
        updates["max_power_w"] = _loss(heater_config.max_power_w, degradation.efficiency_loss_pct)
    if hasattr(heater_config, "hysteresis_k"):
        updates["hysteresis_k"] = _increase(heater_config.hysteresis_k, degradation.response_time_increase_pct)
    return _safe_replace(heater_config, **updates)

