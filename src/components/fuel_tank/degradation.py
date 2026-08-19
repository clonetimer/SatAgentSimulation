from __future__ import annotations

# Local component degradation declarations; component-local module is the canonical source.
"""燃料箱退化模块。

定义燃料箱退化状态类，用于模拟燃料箱的性能退化过程。
"""
from dataclasses import dataclass
from typing import Literal

from ..degradation_base import DegradationModel, DegradationRate, DegradationState


@dataclass(frozen=True)
class FuelTankDegradation(DegradationState):
    """燃料箱退化状态。

    记录燃料箱的关键退化参数，包括燃料泄漏和压力损失。

    Attributes:
        fuel_leak_pct: 燃料泄漏百分比（0-100）
        pressure_loss_pct: 压力损失百分比（0-100）
    """
    fuel_leak_pct: float = 0.0
    pressure_loss_pct: float = 0.0

    @property
    def component_type(self) -> str:
        """返回部件类型标识。

        Returns:
            str: "fuel_tank"
        """
        return "fuel_tank"


@dataclass(frozen=True)
class FuelTankDegradationRate(DegradationRate):
    """燃料箱退化速率。

    定义燃料箱各退化参数的变化速率，支持固定和随机两种模式。

    Attributes:
        fuel_leak_rate_per_day: 每天燃料泄漏率增加值（百分比）
        pressure_loss_rate_per_day: 每天压力损失速率（百分比）
        model: 退化模型类型（固定或随机）
    """
    fuel_leak_rate_per_day: float = 0.001  # 每天泄漏率增加 0.001%
    pressure_loss_rate_per_day: float = 0.0005  # 每天压力损失 0.0005%
    model: Literal[DegradationModel.FIXED, DegradationModel.RANDOM] = DegradationModel.FIXED

    @property
    def component_type(self) -> str:
        """返回部件类型标识。

        Returns:
            str: "fuel_tank"
        """
        return "fuel_tank"

# L2-light local time-function degradation mechanism declarations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Any

from ..degradation_base import ParameterEffect, TimeFunctionDegradation


BASE_EFFECTIVE_PARAMS: dict[str, float] = {'propellant_kg': 5.0, 'tank_pressure_pa': 2000000.0, 'leak_rate_kg_s': 0.0, 'outlet_flow_factor': 1.0}


class FuelTankL2DegradationType(str, Enum):
    PRESSURE_DECAY = 'pressure_decay'
    OUTLET_AGING = 'outlet_aging'


def _effects_for(kind: FuelTankL2DegradationType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        FuelTankL2DegradationType.PRESSURE_DECAY: (
            ParameterEffect('tank_pressure_pa', 'degrade_multiply', 0.25, 'pressurant loss reduces tank pressure over time'),
        ),
        FuelTankL2DegradationType.OUTLET_AGING: (
            ParameterEffect('outlet_flow_factor', 'degrade_multiply', 0.35, 'valve/filter aging reduces outlet flow capability'),
        ),
    }
    return effects_by_type[kind]


PHYSICAL_MECHANISMS = {
    FuelTankL2DegradationType.PRESSURE_DECAY: 'pressurant loss reduces tank pressure over time',
    FuelTankL2DegradationType.OUTLET_AGING: 'valve/filter aging reduces outlet flow capability',
}


@dataclass
class FuelTankL2LightDegradation(TimeFunctionDegradation):
    """Base L2-light degradation for fuel_tank."""
    degradation_type_enum: FuelTankL2DegradationType = FuelTankL2DegradationType.PRESSURE_DECAY
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
class FuelTankPressureDecayDegradation(FuelTankL2LightDegradation):
    """pressurant loss reduces tank pressure over time."""
    degradation_type_enum: FuelTankL2DegradationType = FuelTankL2DegradationType.PRESSURE_DECAY
    start_s: float = 60.0
    rate_per_s: float = 0.001


@dataclass
class FuelTankOutletAgingDegradation(FuelTankL2LightDegradation):
    """valve/filter aging reduces outlet flow capability."""
    degradation_type_enum: FuelTankL2DegradationType = FuelTankL2DegradationType.OUTLET_AGING
    start_s: float = 150.0
    rate_per_s: float = 0.0007


def base_effective_params() -> dict[str, float]:
    """Return effective parameters used by L2-light event/time-function runner."""
    return dict(BASE_EFFECTIVE_PARAMS)


def default_degradations() -> list[FuelTankL2LightDegradation]:
    """Return representative L2-light time-function degradations."""
    return [
        FuelTankPressureDecayDegradation(),
        FuelTankOutletAgingDegradation(),
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
    "BASE_EFFECTIVE_PARAMS", 'FuelTankL2DegradationType', 'FuelTankDegradation', "base_effective_params", "default_degradations", "evaluate_effective_params",
    'FuelTankPressureDecayDegradation',
    'FuelTankOutletAgingDegradation',
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
    if isinstance(rate, FuelTankDegradationRate):
        return FuelTankDegradation(**_state_kwargs_from_rate(rate, FuelTankDegradation, years_elapsed))
    raise ValueError(f"不支持的退化速率类型: {type(rate).__name__}")

def apply_fuel_tank_degradation(ft_config, degradation: FuelTankDegradation):
    updates = {}
    if hasattr(ft_config, "initial_mass_kg"):
        updates["initial_mass_kg"] = _loss(ft_config.initial_mass_kg, degradation.fuel_leak_pct)
    if hasattr(ft_config, "capacity_kg"):
        updates["capacity_kg"] = _loss(ft_config.capacity_kg, degradation.pressure_loss_pct * 0.25)
    return _safe_replace(ft_config, **updates)

