from __future__ import annotations

# Local component degradation declarations; component-local module is the canonical source.
"""功率负载退化模块。

定义功率负载(PowerSink)的退化状态和退化速率类，用于模拟功率负载的性能退化过程。

文献支撑:
- Rezaee A, Nobahari N, Asgarifar A, et al. Smart Fault Detection in Nanosatellite Electrical Power System[J]. arXiv, 2026.
- NASA. A Hybrid Model and Data Driven Approach for Anomaly Detection in Space Power Systems[R]. ASCEND, 2025.
"""
from dataclasses import dataclass
from typing import Literal

from ..degradation_base import DegradationModel, DegradationRate, DegradationState


@dataclass(frozen=True)
class PowerSinkDegradation(DegradationState):
    """功率负载退化状态。

    记录功率负载的关键退化参数，包括效率下降、功耗增加和热阻增加。

    Attributes:
        efficiency_loss_pct: 效率损失百分比（0-100）
        power_consumption_increase_pct: 功耗增加百分比（0-100）
        thermal_resistance_increase_pct: 热阻增加百分比（0-100）
        reliability_loss_pct: 可靠性损失百分比（0-100）
        performance_degradation_pct: 性能退化百分比（0-100）
    """
    efficiency_loss_pct: float = 0.0
    power_consumption_increase_pct: float = 0.0
    thermal_resistance_increase_pct: float = 0.0
    reliability_loss_pct: float = 0.0
    performance_degradation_pct: float = 0.0

    @property
    def component_type(self) -> str:
        return "power_sink"


@dataclass(frozen=True)
class PowerSinkDegradationRate(DegradationRate):
    """功率负载退化速率。

    定义功率负载各退化参数的变化速率，支持固定和随机两种模式。

    Attributes:
        efficiency_loss_rate_per_year: 每年效率损失速率（百分比）
        power_consumption_increase_rate_per_year: 每年功耗增加速率（百分比）
        thermal_resistance_increase_rate_per_year: 每年热阻增加速率（百分比）
        reliability_loss_rate_per_year: 每年可靠性损失速率（百分比）
        performance_degradation_rate_per_year: 每年性能退化速率（百分比）
    """
    efficiency_loss_rate_per_year: float = 0.2
    power_consumption_increase_rate_per_year: float = 0.15
    thermal_resistance_increase_rate_per_year: float = 0.1
    reliability_loss_rate_per_year: float = 0.3
    performance_degradation_rate_per_year: float = 0.18
    model: Literal[DegradationModel.FIXED, DegradationModel.RANDOM] = DegradationModel.FIXED

    @property
    def component_type(self) -> str:
        return "power_sink"

# L2-light local time-function degradation mechanism declarations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Any

from ..degradation_base import ParameterEffect, TimeFunctionDegradation


BASE_EFFECTIVE_PARAMS: dict[str, float] = {'load_power_w': 20.0, 'efficiency': 0.9, 'surge_power_w': 0.0, 'availability': 1.0}


class PowerSinkL2DegradationType(str, Enum):
    LOAD_DRIFT = 'load_drift'
    EFFICIENCY_DECAY = 'efficiency_decay'


def _effects_for(kind: PowerSinkL2DegradationType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        PowerSinkL2DegradationType.LOAD_DRIFT: (
            ParameterEffect('load_power_w', 'grow_multiply', 0.25, 'electronics aging drifts power demand'),
        ),
        PowerSinkL2DegradationType.EFFICIENCY_DECAY: (
            ParameterEffect('efficiency', 'degrade_multiply', 0.2, 'converter/load efficiency declines'),
        ),
    }
    return effects_by_type[kind]


PHYSICAL_MECHANISMS = {
    PowerSinkL2DegradationType.LOAD_DRIFT: 'electronics aging drifts power demand',
    PowerSinkL2DegradationType.EFFICIENCY_DECAY: 'converter/load efficiency declines',
}


@dataclass
class PowerSinkL2LightDegradation(TimeFunctionDegradation):
    """Base L2-light degradation for power_sink."""
    degradation_type_enum: PowerSinkL2DegradationType = PowerSinkL2DegradationType.LOAD_DRIFT
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
class PowerSinkLoadDriftDegradation(PowerSinkL2LightDegradation):
    """electronics aging drifts power demand."""
    degradation_type_enum: PowerSinkL2DegradationType = PowerSinkL2DegradationType.LOAD_DRIFT
    start_s: float = 60.0
    rate_per_s: float = 0.001


@dataclass
class PowerSinkEfficiencyDecayDegradation(PowerSinkL2LightDegradation):
    """converter/load efficiency declines."""
    degradation_type_enum: PowerSinkL2DegradationType = PowerSinkL2DegradationType.EFFICIENCY_DECAY
    start_s: float = 150.0
    rate_per_s: float = 0.0007


def base_effective_params() -> dict[str, float]:
    """Return effective parameters used by L2-light event/time-function runner."""
    return dict(BASE_EFFECTIVE_PARAMS)


def default_degradations() -> list[PowerSinkL2LightDegradation]:
    """Return representative L2-light time-function degradations."""
    return [
        PowerSinkLoadDriftDegradation(),
        PowerSinkEfficiencyDecayDegradation(),
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
    "BASE_EFFECTIVE_PARAMS", 'PowerSinkL2DegradationType', 'PowerSinkDegradation', "base_effective_params", "default_degradations", "evaluate_effective_params",
    'PowerSinkLoadDriftDegradation',
    'PowerSinkEfficiencyDecayDegradation',
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
    if isinstance(rate, PowerSinkDegradationRate):
        return PowerSinkDegradation(**_state_kwargs_from_rate(rate, PowerSinkDegradation, years_elapsed))
    raise ValueError(f"不支持的退化速率类型: {type(rate).__name__}")

def apply_power_sink_degradation(ps_config, degradation: PowerSinkDegradation):
    updates = {}
    increase_pct = degradation.power_consumption_increase_pct + degradation.efficiency_loss_pct
    if hasattr(ps_config, "base_w"):
        updates["base_w"] = _increase(ps_config.base_w, increase_pct)
    if hasattr(ps_config, "mode_power_w") and ps_config.mode_power_w:
        updates["mode_power_w"] = {key: _increase(value, increase_pct) for key, value in ps_config.mode_power_w.items()}
    return _safe_replace(ps_config, **updates)

