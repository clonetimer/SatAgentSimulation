from __future__ import annotations

# Local component degradation declarations; component-local module is the canonical source.
"""热节点退化模块。

定义热节点(ThermalNode)的退化状态和退化速率类，用于模拟热节点的性能退化过程。

文献支撑:
- 天拓五号卫星推进系统在轨故障诊断与定位方法[J]. 国防科技大学学报, 2024, 46(5): 141-149.
- 航天器多因素可靠性-洞察研究[R]. 飞鸽文档, 2024.
"""
from dataclasses import dataclass
from typing import Literal

from ..degradation_base import DegradationModel, DegradationRate, DegradationState


@dataclass(frozen=True)
class ThermalNodeDegradation(DegradationState):
    """热节点退化状态。

    记录热节点的关键退化参数，包括热阻增加、热容变化和温度传感器精度损失。

    Attributes:
        thermal_resistance_increase_pct: 热阻增加百分比（0-100）
        thermal_capacitance_change_pct: 热容变化百分比（-100-100）
        temperature_sensor_accuracy_loss_pct: 温度传感器精度损失百分比（0-100）
        insulation_efficiency_loss_pct: 绝热效率损失百分比（0-100）
        heat_transfer_coefficient_loss_pct: 传热系数损失百分比（0-100）
    """
    thermal_resistance_increase_pct: float = 0.0
    thermal_capacitance_change_pct: float = 0.0
    temperature_sensor_accuracy_loss_pct: float = 0.0
    insulation_efficiency_loss_pct: float = 0.0
    heat_transfer_coefficient_loss_pct: float = 0.0

    @property
    def component_type(self) -> str:
        return "thermal_node"


@dataclass(frozen=True)
class ThermalNodeDegradationRate(DegradationRate):
    """热节点退化速率。

    定义热节点各退化参数的变化速率，支持固定和随机两种模式。

    Attributes:
        thermal_resistance_increase_rate_per_year: 每年热阻增加速率（百分比）
        thermal_capacitance_change_rate_per_year: 每年热容变化速率（百分比）
        temperature_sensor_accuracy_loss_rate_per_year: 每年温度传感器精度损失速率（百分比）
        insulation_efficiency_loss_rate_per_year: 每年绝热效率损失速率（百分比）
        heat_transfer_coefficient_loss_rate_per_year: 每年传热系数损失速率（百分比）
    """
    thermal_resistance_increase_rate_per_year: float = 0.1
    thermal_capacitance_change_rate_per_year: float = 0.05
    temperature_sensor_accuracy_loss_rate_per_year: float = 0.03
    insulation_efficiency_loss_rate_per_year: float = 0.15
    heat_transfer_coefficient_loss_rate_per_year: float = 0.08
    model: Literal[DegradationModel.FIXED, DegradationModel.RANDOM] = DegradationModel.FIXED

    @property
    def component_type(self) -> str:
        return "thermal_node"

# L2-light local time-function degradation mechanism declarations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Any

from ..degradation_base import ParameterEffect, TimeFunctionDegradation


BASE_EFFECTIVE_PARAMS: dict[str, float] = {'temperature_k': 295.0, 'thermal_resistance_k_w': 2.0, 'heat_capacity_j_k': 800.0, 'heat_input_w': 20.0, 'sensor_bias_k': 0.0}


class ThermalNodeL2DegradationType(str, Enum):
    THERMAL_RESISTANCE_GROWTH = 'thermal_resistance_growth'
    HEAT_CAPACITY_DRIFT = 'heat_capacity_drift'


def _effects_for(kind: ThermalNodeL2DegradationType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        ThermalNodeL2DegradationType.THERMAL_RESISTANCE_GROWTH: (
            ParameterEffect('thermal_resistance_k_w', 'grow_multiply', 0.8, 'interface aging increases thermal resistance'),
        ),
        ThermalNodeL2DegradationType.HEAT_CAPACITY_DRIFT: (
            ParameterEffect('heat_capacity_j_k', 'degrade_multiply', 0.15, 'material/property drift changes heat capacity'),
        ),
    }
    return effects_by_type[kind]


PHYSICAL_MECHANISMS = {
    ThermalNodeL2DegradationType.THERMAL_RESISTANCE_GROWTH: 'interface aging increases thermal resistance',
    ThermalNodeL2DegradationType.HEAT_CAPACITY_DRIFT: 'material/property drift changes heat capacity',
}


@dataclass
class ThermalNodeL2LightDegradation(TimeFunctionDegradation):
    """Base L2-light degradation for thermal_node."""
    degradation_type_enum: ThermalNodeL2DegradationType = ThermalNodeL2DegradationType.THERMAL_RESISTANCE_GROWTH
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
class ThermalNodeThermalResistanceGrowthDegradation(ThermalNodeL2LightDegradation):
    """interface aging increases thermal resistance."""
    degradation_type_enum: ThermalNodeL2DegradationType = ThermalNodeL2DegradationType.THERMAL_RESISTANCE_GROWTH
    start_s: float = 60.0
    rate_per_s: float = 0.001


@dataclass
class ThermalNodeHeatCapacityDriftDegradation(ThermalNodeL2LightDegradation):
    """material/property drift changes heat capacity."""
    degradation_type_enum: ThermalNodeL2DegradationType = ThermalNodeL2DegradationType.HEAT_CAPACITY_DRIFT
    start_s: float = 150.0
    rate_per_s: float = 0.0007


def base_effective_params() -> dict[str, float]:
    """Return effective parameters used by L2-light event/time-function runner."""
    return dict(BASE_EFFECTIVE_PARAMS)


def default_degradations() -> list[ThermalNodeL2LightDegradation]:
    """Return representative L2-light time-function degradations."""
    return [
        ThermalNodeThermalResistanceGrowthDegradation(),
        ThermalNodeHeatCapacityDriftDegradation(),
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
    "BASE_EFFECTIVE_PARAMS", 'ThermalNodeL2DegradationType', 'ThermalNodeDegradation', "base_effective_params", "default_degradations", "evaluate_effective_params",
    'ThermalNodeThermalResistanceGrowthDegradation',
    'ThermalNodeHeatCapacityDriftDegradation',
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
    if isinstance(rate, ThermalNodeDegradationRate):
        return ThermalNodeDegradation(**_state_kwargs_from_rate(rate, ThermalNodeDegradation, years_elapsed))
    raise ValueError(f"不支持的退化速率类型: {type(rate).__name__}")

def apply_thermal_node_degradation(tn_config, degradation: ThermalNodeDegradation):
    updates = {}
    if hasattr(tn_config, "tau_s"):
        updates["tau_s"] = _increase(tn_config.tau_s, degradation.thermal_resistance_increase_pct + degradation.thermal_capacitance_change_pct)
    if hasattr(tn_config, "heat_gain_k_per_w"):
        updates["heat_gain_k_per_w"] = _increase(tn_config.heat_gain_k_per_w, degradation.thermal_resistance_increase_pct + degradation.insulation_efficiency_loss_pct)
    if hasattr(tn_config, "max_temp_k"):
        updates["max_temp_k"] = _loss(tn_config.max_temp_k, degradation.temperature_sensor_accuracy_loss_pct * 0.1)
    return _safe_replace(tn_config, **updates)

