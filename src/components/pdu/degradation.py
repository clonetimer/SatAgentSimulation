from __future__ import annotations

# Local component degradation declarations; component-local module is the canonical source.
"""配电单元退化模块。

定义配电单元(PDU)的退化状态和退化速率类，用于模拟PDU的性能退化过程。

文献支撑:
- Ye Z, Yin J, Zhang T, et al. Fault reconstruction method of high redundancy satellite power distribution unit[J]. IET Power Electronics, 2023, 16(8): 1443-1454.
- 全电推卫星能源全自主管理方法及系统[P]. 中国专利, CN202511344112.
- NASA. A Hybrid Model and Data Driven Approach for Anomaly Detection in Space Power Systems[R]. ASCEND, 2025.
"""
from dataclasses import dataclass
from typing import Literal

from ..degradation_base import DegradationModel, DegradationRate, DegradationState


@dataclass(frozen=True)
class PDUDegradation(DegradationState):
    """配电单元退化状态。

    记录PDU的关键退化参数，包括效率下降、电压降和开关损耗增加。

    Attributes:
        efficiency_loss_pct: 效率损失百分比（0-100）
        voltage_drop_increase_pct: 电压降增加百分比（0-100）
        switch_loss_increase_pct: 开关损耗增加百分比（0-100）
        leakage_current_increase_pct: 漏电流增加百分比（0-100）
        thermal_resistance_increase_pct: 热阻增加百分比（0-100）
        monitoring_accuracy_loss_pct: 监测精度损失百分比（0-100）
    """
    efficiency_loss_pct: float = 0.0
    voltage_drop_increase_pct: float = 0.0
    switch_loss_increase_pct: float = 0.0
    leakage_current_increase_pct: float = 0.0
    thermal_resistance_increase_pct: float = 0.0
    monitoring_accuracy_loss_pct: float = 0.0

    @property
    def component_type(self) -> str:
        return "pdu"


@dataclass(frozen=True)
class PDUDegradationRate(DegradationRate):
    """配电单元退化速率。

    定义PDU各退化参数的变化速率，支持固定和随机两种模式。

    Attributes:
        efficiency_loss_rate_per_year: 每年效率损失速率（百分比）
        voltage_drop_increase_rate_per_year: 每年电压降增加速率（百分比）
        switch_loss_increase_rate_per_year: 每年开关损耗增加速率（百分比）
        leakage_current_increase_rate_per_year: 每年漏电流增加速率（百分比）
        thermal_resistance_increase_rate_per_year: 每年热阻增加速率（百分比）
        monitoring_accuracy_loss_rate_per_year: 每年监测精度损失速率（百分比）
    """
    efficiency_loss_rate_per_year: float = 0.2
    voltage_drop_increase_rate_per_year: float = 0.15
    switch_loss_increase_rate_per_year: float = 0.3
    leakage_current_increase_rate_per_year: float = 0.1
    thermal_resistance_increase_rate_per_year: float = 0.15
    monitoring_accuracy_loss_rate_per_year: float = 0.05
    model: Literal[DegradationModel.FIXED, DegradationModel.RANDOM] = DegradationModel.FIXED

    @property
    def component_type(self) -> str:
        return "pdu"

# L2-light local time-function degradation mechanism declarations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Any

from ..degradation_base import ParameterEffect, TimeFunctionDegradation


BASE_EFFECTIVE_PARAMS: dict[str, float] = {'bus_voltage_v': 28.0, 'path_resistance_ohm': 0.05, 'channel_current_limit_a': 4.0, 'availability': 1.0, 'trip_state': 0.0}


class PduL2DegradationType(str, Enum):
    CONTACT_RESISTANCE_GROWTH = 'contact_resistance_growth'
    VOLTAGE_REGULATION_DRIFT = 'voltage_regulation_drift'


def _effects_for(kind: PduL2DegradationType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        PduL2DegradationType.CONTACT_RESISTANCE_GROWTH: (
            ParameterEffect('path_resistance_ohm', 'grow_multiply', 1.2, 'contact aging increases path resistance'),
            ParameterEffect('bus_voltage_v', 'add_loss', 1.5, 'contact aging increases path resistance'),
        ),
        PduL2DegradationType.VOLTAGE_REGULATION_DRIFT: (
            ParameterEffect('bus_voltage_v', 'add_loss', 1.0, 'regulator drift lowers bus voltage accuracy'),
        ),
    }
    return effects_by_type[kind]


PHYSICAL_MECHANISMS = {
    PduL2DegradationType.CONTACT_RESISTANCE_GROWTH: 'contact aging increases path resistance',
    PduL2DegradationType.VOLTAGE_REGULATION_DRIFT: 'regulator drift lowers bus voltage accuracy',
}


@dataclass
class PduL2LightDegradation(TimeFunctionDegradation):
    """Base L2-light degradation for pdu."""
    degradation_type_enum: PduL2DegradationType = PduL2DegradationType.CONTACT_RESISTANCE_GROWTH
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
class PduContactResistanceGrowthDegradation(PduL2LightDegradation):
    """contact aging increases path resistance."""
    degradation_type_enum: PduL2DegradationType = PduL2DegradationType.CONTACT_RESISTANCE_GROWTH
    start_s: float = 60.0
    rate_per_s: float = 0.001


@dataclass
class PduVoltageRegulationDriftDegradation(PduL2LightDegradation):
    """regulator drift lowers bus voltage accuracy."""
    degradation_type_enum: PduL2DegradationType = PduL2DegradationType.VOLTAGE_REGULATION_DRIFT
    start_s: float = 150.0
    rate_per_s: float = 0.0007


def base_effective_params() -> dict[str, float]:
    """Return effective parameters used by L2-light event/time-function runner."""
    return dict(BASE_EFFECTIVE_PARAMS)


def default_degradations() -> list[PduL2LightDegradation]:
    """Return representative L2-light time-function degradations."""
    return [
        PduContactResistanceGrowthDegradation(),
        PduVoltageRegulationDriftDegradation(),
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
    "BASE_EFFECTIVE_PARAMS", 'PduL2DegradationType', 'PDUDegradation', "base_effective_params", "default_degradations", "evaluate_effective_params",
    'PduContactResistanceGrowthDegradation',
    'PduVoltageRegulationDriftDegradation',
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
    if isinstance(rate, PDUDegradationRate):
        return PDUDegradation(**_state_kwargs_from_rate(rate, PDUDegradation, years_elapsed))
    raise ValueError(f"不支持的退化速率类型: {type(rate).__name__}")

def apply_pdu_degradation(pdu_config, degradation: PDUDegradation):
    updates = {}
    if hasattr(pdu_config, "bus_max_w"):
        updates["bus_max_w"] = _loss(pdu_config.bus_max_w, degradation.efficiency_loss_pct + degradation.switch_loss_increase_pct)
    return _safe_replace(pdu_config, **updates)

