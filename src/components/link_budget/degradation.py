from __future__ import annotations

# Local component degradation declarations; component-local module is the canonical source.
"""链路预算退化模块。

定义链路预算(LinkBudget)的退化状态和退化速率类，用于模拟链路预算的性能退化过程。

文献支撑:
- 航天器多因素可靠性-洞察研究[R]. 飞鸽文档, 2024.
- Shao R, You W, Nie Y. Reliability modeling framework of satellite constellation based on three-parameter interval grey number Lz transformation[J]. Scientific Reports, 2025, 15: 21022.
"""
from dataclasses import dataclass
from typing import Literal

from ..degradation_base import DegradationModel, DegradationRate, DegradationState


@dataclass(frozen=True)
class LinkBudgetDegradation(DegradationState):
    """链路预算退化状态。

    记录链路预算的关键退化参数，包括信号衰减增加、噪声系数上升和误码率增加。

    Attributes:
        signal_attenuation_increase_pct: 信号衰减增加百分比（0-100）
        noise_figure_increase_pct: 噪声系数增加百分比（0-100）
        bit_error_rate_increase_pct: 误码率增加百分比（0-100）
        bandwidth_efficiency_loss_pct: 带宽效率损失百分比（0-100）
        link_margin_loss_pct: 链路余量损失百分比（0-100）
    """
    signal_attenuation_increase_pct: float = 0.0
    noise_figure_increase_pct: float = 0.0
    bit_error_rate_increase_pct: float = 0.0
    bandwidth_efficiency_loss_pct: float = 0.0
    link_margin_loss_pct: float = 0.0

    @property
    def component_type(self) -> str:
        return "link_budget"


@dataclass(frozen=True)
class LinkBudgetDegradationRate(DegradationRate):
    """链路预算退化速率。

    定义链路预算各退化参数的变化速率，支持固定和随机两种模式。

    Attributes:
        signal_attenuation_increase_rate_per_year: 每年信号衰减增加速率（百分比）
        noise_figure_increase_rate_per_year: 每年噪声系数增加速率（百分比）
        bit_error_rate_increase_rate_per_year: 每年误码率增加速率（百分比）
        bandwidth_efficiency_loss_rate_per_year: 每年带宽效率损失速率（百分比）
        link_margin_loss_rate_per_year: 每年链路余量损失速率（百分比）
    """
    signal_attenuation_increase_rate_per_year: float = 0.15
    noise_figure_increase_rate_per_year: float = 0.1
    bit_error_rate_increase_rate_per_year: float = 0.2
    bandwidth_efficiency_loss_rate_per_year: float = 0.08
    link_margin_loss_rate_per_year: float = 0.12
    model: Literal[DegradationModel.FIXED, DegradationModel.RANDOM] = DegradationModel.FIXED

    @property
    def component_type(self) -> str:
        return "link_budget"

# L2-light local time-function degradation mechanism declarations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Any

from ..degradation_base import ParameterEffect, TimeFunctionDegradation


BASE_EFFECTIVE_PARAMS: dict[str, float] = {'margin_db': 8.0, 'path_loss_db': 150.0, 'interference_db': 0.0, 'ber': 1e-06}


class LinkBudgetL2DegradationType(str, Enum):
    MARGIN_EROSION = 'margin_erosion'
    NOISE_FIGURE_GROWTH = 'noise_figure_growth'


def _effects_for(kind: LinkBudgetL2DegradationType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        LinkBudgetL2DegradationType.MARGIN_EROSION: (
            ParameterEffect('margin_db', 'add_loss', 3.0, 'long-term RF chain degradation erodes link margin'),
        ),
        LinkBudgetL2DegradationType.NOISE_FIGURE_GROWTH: (
            ParameterEffect('margin_db', 'add_loss', 2.0, 'receiver aging increases noise figure'),
            ParameterEffect('ber', 'grow_multiply', 30.0, 'receiver aging increases noise figure'),
        ),
    }
    return effects_by_type[kind]


PHYSICAL_MECHANISMS = {
    LinkBudgetL2DegradationType.MARGIN_EROSION: 'long-term RF chain degradation erodes link margin',
    LinkBudgetL2DegradationType.NOISE_FIGURE_GROWTH: 'receiver aging increases noise figure',
}


@dataclass
class LinkBudgetL2LightDegradation(TimeFunctionDegradation):
    """Base L2-light degradation for link_budget."""
    degradation_type_enum: LinkBudgetL2DegradationType = LinkBudgetL2DegradationType.MARGIN_EROSION
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
class LinkBudgetMarginErosionDegradation(LinkBudgetL2LightDegradation):
    """long-term RF chain degradation erodes link margin."""
    degradation_type_enum: LinkBudgetL2DegradationType = LinkBudgetL2DegradationType.MARGIN_EROSION
    start_s: float = 60.0
    rate_per_s: float = 0.001


@dataclass
class LinkBudgetNoiseFigureGrowthDegradation(LinkBudgetL2LightDegradation):
    """receiver aging increases noise figure."""
    degradation_type_enum: LinkBudgetL2DegradationType = LinkBudgetL2DegradationType.NOISE_FIGURE_GROWTH
    start_s: float = 150.0
    rate_per_s: float = 0.0007


def base_effective_params() -> dict[str, float]:
    """Return effective parameters used by L2-light event/time-function runner."""
    return dict(BASE_EFFECTIVE_PARAMS)


def default_degradations() -> list[LinkBudgetL2LightDegradation]:
    """Return representative L2-light time-function degradations."""
    return [
        LinkBudgetMarginErosionDegradation(),
        LinkBudgetNoiseFigureGrowthDegradation(),
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
    "BASE_EFFECTIVE_PARAMS", 'LinkBudgetL2DegradationType', 'LinkBudgetDegradation', "base_effective_params", "default_degradations", "evaluate_effective_params",
    'LinkBudgetMarginErosionDegradation',
    'LinkBudgetNoiseFigureGrowthDegradation',
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
    if isinstance(rate, LinkBudgetDegradationRate):
        return LinkBudgetDegradation(**_state_kwargs_from_rate(rate, LinkBudgetDegradation, years_elapsed))
    raise ValueError(f"不支持的退化速率类型: {type(rate).__name__}")

def apply_link_budget_degradation(lb_config, degradation: LinkBudgetDegradation):
    updates = {}
    if hasattr(lb_config, "misc_loss_db"):
        updates["misc_loss_db"] = float(lb_config.misc_loss_db) + degradation.signal_attenuation_increase_pct * 0.1 + degradation.link_margin_loss_pct * 0.1
    if hasattr(lb_config, "noise_temp_k"):
        updates["noise_temp_k"] = _increase(lb_config.noise_temp_k, degradation.noise_figure_increase_pct)
    if hasattr(lb_config, "downlink_eff"):
        updates["downlink_eff"] = min(1.0, _loss(lb_config.downlink_eff, degradation.bandwidth_efficiency_loss_pct + degradation.bit_error_rate_increase_pct))
    return _safe_replace(lb_config, **updates)

