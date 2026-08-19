from __future__ import annotations

# Local component degradation declarations; component-local module is the canonical source.
"""地面站退化模块。

定义地面站(GroundStation)的退化状态和退化速率类，用于模拟地面站的性能退化过程。

文献支撑:
- 基于MBSE的卫星总体设计与FMEA方法融合及应用研究[R]. 2025.
- 航天器多因素可靠性-洞察研究[R]. 飞鸽文档, 2024.
"""
from dataclasses import dataclass
from typing import Literal

from ..degradation_base import DegradationModel, DegradationRate, DegradationState


@dataclass(frozen=True)
class GroundStationDegradation(DegradationState):
    """地面站退化状态。

    记录地面站的关键退化参数，包括天线增益下降、接收灵敏度损失和跟踪精度下降。

    Attributes:
        antenna_gain_loss_pct: 天线增益损失百分比（0-100）
        receiver_sensitivity_loss_pct: 接收灵敏度损失百分比（0-100）
        tracking_accuracy_loss_pct: 跟踪精度损失百分比（0-100）
        signal_processing_efficiency_loss_pct: 信号处理效率损失百分比（0-100）
        data_transmission_rate_loss_pct: 数据传输速率损失百分比（0-100）
    """
    antenna_gain_loss_pct: float = 0.0
    receiver_sensitivity_loss_pct: float = 0.0
    tracking_accuracy_loss_pct: float = 0.0
    signal_processing_efficiency_loss_pct: float = 0.0
    data_transmission_rate_loss_pct: float = 0.0

    @property
    def component_type(self) -> str:
        return "ground_station"


@dataclass(frozen=True)
class GroundStationDegradationRate(DegradationRate):
    """地面站退化速率。

    定义地面站各退化参数的变化速率，支持固定和随机两种模式。

    Attributes:
        antenna_gain_loss_rate_per_year: 每天线增益损失速率（百分比）
        receiver_sensitivity_loss_rate_per_year: 每年接收灵敏度损失速率（百分比）
        tracking_accuracy_loss_rate_per_year: 每年跟踪精度损失速率（百分比）
        signal_processing_efficiency_loss_rate_per_year: 每年信号处理效率损失速率（百分比）
        data_transmission_rate_loss_rate_per_year: 每年数据传输速率损失速率（百分比）
    """
    antenna_gain_loss_rate_per_year: float = 0.1
    receiver_sensitivity_loss_rate_per_year: float = 0.15
    tracking_accuracy_loss_rate_per_year: float = 0.05
    signal_processing_efficiency_loss_rate_per_year: float = 0.2
    data_transmission_rate_loss_rate_per_year: float = 0.18
    model: Literal[DegradationModel.FIXED, DegradationModel.RANDOM] = DegradationModel.FIXED

    @property
    def component_type(self) -> str:
        return "ground_station"

# L2-light local time-function degradation mechanism declarations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Any

from ..degradation_base import ParameterEffect, TimeFunctionDegradation


BASE_EFFECTIVE_PARAMS: dict[str, float] = {'g_over_t_db': 12.0, 'tracking_error_deg': 0.1, 'availability': 1.0, 'weather_loss_db': 0.2}


class GroundStationL2DegradationType(str, Enum):
    GOVER_TDRIFT = 'gover_tdrift'
    POINTING_ACCURACY_DRIFT = 'pointing_accuracy_drift'


def _effects_for(kind: GroundStationL2DegradationType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        GroundStationL2DegradationType.GOVER_TDRIFT: (
            ParameterEffect('g_over_t_db', 'add_loss', 2.5, 'receiver/noise temperature drift lowers G/T'),
        ),
        GroundStationL2DegradationType.POINTING_ACCURACY_DRIFT: (
            ParameterEffect('tracking_error_deg', 'grow_add', 1.5, 'mount/calibration drift grows tracking error'),
        ),
    }
    return effects_by_type[kind]


PHYSICAL_MECHANISMS = {
    GroundStationL2DegradationType.GOVER_TDRIFT: 'receiver/noise temperature drift lowers G/T',
    GroundStationL2DegradationType.POINTING_ACCURACY_DRIFT: 'mount/calibration drift grows tracking error',
}


@dataclass
class GroundStationL2LightDegradation(TimeFunctionDegradation):
    """Base L2-light degradation for ground_station."""
    degradation_type_enum: GroundStationL2DegradationType = GroundStationL2DegradationType.GOVER_TDRIFT
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
class GroundStationGOverTDriftDegradation(GroundStationL2LightDegradation):
    """receiver/noise temperature drift lowers G/T."""
    degradation_type_enum: GroundStationL2DegradationType = GroundStationL2DegradationType.GOVER_TDRIFT
    start_s: float = 60.0
    rate_per_s: float = 0.001


@dataclass
class GroundStationPointingAccuracyDriftDegradation(GroundStationL2LightDegradation):
    """mount/calibration drift grows tracking error."""
    degradation_type_enum: GroundStationL2DegradationType = GroundStationL2DegradationType.POINTING_ACCURACY_DRIFT
    start_s: float = 150.0
    rate_per_s: float = 0.0007


def base_effective_params() -> dict[str, float]:
    """Return effective parameters used by L2-light event/time-function runner."""
    return dict(BASE_EFFECTIVE_PARAMS)


def default_degradations() -> list[GroundStationL2LightDegradation]:
    """Return representative L2-light time-function degradations."""
    return [
        GroundStationGOverTDriftDegradation(),
        GroundStationPointingAccuracyDriftDegradation(),
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
    "BASE_EFFECTIVE_PARAMS", 'GroundStationL2DegradationType', 'GroundStationDegradation', "base_effective_params", "default_degradations", "evaluate_effective_params",
    'GroundStationGOverTDriftDegradation',
    'GroundStationPointingAccuracyDriftDegradation',
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
    if isinstance(rate, GroundStationDegradationRate):
        return GroundStationDegradation(**_state_kwargs_from_rate(rate, GroundStationDegradation, years_elapsed))
    raise ValueError(f"不支持的退化速率类型: {type(rate).__name__}")

def apply_ground_station_degradation(gs_config, degradation: GroundStationDegradation):
    range_loss_pct = degradation.antenna_gain_loss_pct + degradation.receiver_sensitivity_loss_pct
    updates = {}
    if hasattr(gs_config, "max_range_m"):
        updates["max_range_m"] = _loss(gs_config.max_range_m, range_loss_pct)
    if hasattr(gs_config, "min_elevation_deg"):
        updates["min_elevation_deg"] = _increase(gs_config.min_elevation_deg, degradation.tracking_accuracy_loss_pct)
    return _safe_replace(gs_config, **updates)

