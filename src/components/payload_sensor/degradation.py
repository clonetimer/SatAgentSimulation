from __future__ import annotations

# Local component degradation declarations; component-local module is the canonical source.
"""载荷传感器退化模块。

定义载荷传感器(PayloadSensor)的退化状态和退化速率类，用于模拟载荷传感器的性能退化过程。

文献支撑:
- 航天器多因素可靠性-洞察研究[R]. 飞鸽文档, 2024.
"""
from dataclasses import dataclass
from typing import Literal

from ..degradation_base import DegradationModel, DegradationRate, DegradationState


@dataclass(frozen=True)
class PayloadSensorDegradation(DegradationState):
    """载荷传感器退化状态。

    记录载荷传感器的关键退化参数，包括灵敏度下降、噪声增加和标定漂移。

    Attributes:
        sensitivity_loss_pct: 灵敏度损失百分比（0-100）
        noise_increase_pct: 噪声增加百分比（0-100）
        calibration_drift_pct: 标定漂移百分比（0-100）
        response_time_increase_pct: 响应时间增加百分比（0-100）
        power_efficiency_loss_pct: 功率效率损失百分比（0-100）
    """
    sensitivity_loss_pct: float = 0.0
    noise_increase_pct: float = 0.0
    calibration_drift_pct: float = 0.0
    response_time_increase_pct: float = 0.0
    power_efficiency_loss_pct: float = 0.0

    @property
    def component_type(self) -> str:
        return "payload_sensor"


@dataclass(frozen=True)
class PayloadSensorDegradationRate(DegradationRate):
    """载荷传感器退化速率。

    定义载荷传感器各退化参数的变化速率，支持固定和随机两种模式。

    Attributes:
        sensitivity_loss_rate_per_year: 每年灵敏度损失速率（百分比）
        noise_increase_rate_per_year: 每年噪声增加速率（百分比）
        calibration_drift_rate_per_year: 每年标定漂移速率（百分比）
        response_time_increase_rate_per_year: 每年响应时间增加速率（百分比）
        power_efficiency_loss_rate_per_year: 每年功率效率损失速率（百分比）
    """
    sensitivity_loss_rate_per_year: float = 0.3
    noise_increase_rate_per_year: float = 0.2
    calibration_drift_rate_per_year: float = 0.05
    response_time_increase_rate_per_year: float = 0.1
    power_efficiency_loss_rate_per_year: float = 0.15
    model: Literal[DegradationModel.FIXED, DegradationModel.RANDOM] = DegradationModel.FIXED

    @property
    def component_type(self) -> str:
        return "payload_sensor"

# L2-light local time-function degradation mechanism declarations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Any

from ..degradation_base import ParameterEffect, TimeFunctionDegradation


BASE_EFFECTIVE_PARAMS: dict[str, float] = {'responsivity': 1.0, 'noise_sigma': 1.0, 'bad_pixel_fraction': 0.0, 'availability': 1.0, 'image_quality': 0.95}


class PayloadSensorL2DegradationType(str, Enum):
    RESPONSIVITY_DECAY = 'responsivity_decay'
    NOISE_GROWTH = 'noise_growth'


def _effects_for(kind: PayloadSensorL2DegradationType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        PayloadSensorL2DegradationType.RESPONSIVITY_DECAY: (
            ParameterEffect('responsivity', 'degrade_multiply', 0.25, 'detector responsivity decays over mission time'),
            ParameterEffect('image_quality', 'degrade_multiply', 0.12, 'detector responsivity decays over mission time'),
        ),
        PayloadSensorL2DegradationType.NOISE_GROWTH: (
            ParameterEffect('noise_sigma', 'grow_multiply', 1.0, 'dark current/noise grows with aging'),
            ParameterEffect('bad_pixel_fraction', 'grow_add', 0.05, 'dark current/noise grows with aging'),
        ),
    }
    return effects_by_type[kind]


PHYSICAL_MECHANISMS = {
    PayloadSensorL2DegradationType.RESPONSIVITY_DECAY: 'detector responsivity decays over mission time',
    PayloadSensorL2DegradationType.NOISE_GROWTH: 'dark current/noise grows with aging',
}


@dataclass
class PayloadSensorL2LightDegradation(TimeFunctionDegradation):
    """Base L2-light degradation for payload_sensor."""
    degradation_type_enum: PayloadSensorL2DegradationType = PayloadSensorL2DegradationType.RESPONSIVITY_DECAY
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
class PayloadSensorResponsivityDecayDegradation(PayloadSensorL2LightDegradation):
    """detector responsivity decays over mission time."""
    degradation_type_enum: PayloadSensorL2DegradationType = PayloadSensorL2DegradationType.RESPONSIVITY_DECAY
    start_s: float = 60.0
    rate_per_s: float = 0.001


@dataclass
class PayloadSensorNoiseGrowthDegradation(PayloadSensorL2LightDegradation):
    """dark current/noise grows with aging."""
    degradation_type_enum: PayloadSensorL2DegradationType = PayloadSensorL2DegradationType.NOISE_GROWTH
    start_s: float = 150.0
    rate_per_s: float = 0.0007


def base_effective_params() -> dict[str, float]:
    """Return effective parameters used by L2-light event/time-function runner."""
    return dict(BASE_EFFECTIVE_PARAMS)


def default_degradations() -> list[PayloadSensorL2LightDegradation]:
    """Return representative L2-light time-function degradations."""
    return [
        PayloadSensorResponsivityDecayDegradation(),
        PayloadSensorNoiseGrowthDegradation(),
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
    "BASE_EFFECTIVE_PARAMS", 'PayloadSensorL2DegradationType', 'PayloadSensorDegradation', "base_effective_params", "default_degradations", "evaluate_effective_params",
    'PayloadSensorResponsivityDecayDegradation',
    'PayloadSensorNoiseGrowthDegradation',
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
    if isinstance(rate, PayloadSensorDegradationRate):
        return PayloadSensorDegradation(**_state_kwargs_from_rate(rate, PayloadSensorDegradation, years_elapsed))
    raise ValueError(f"不支持的退化速率类型: {type(rate).__name__}")

def apply_payload_sensor_degradation(ps_config, degradation: PayloadSensorDegradation):
    updates = {}
    if hasattr(ps_config, "nominal_data_rate_bps"):
        updates["nominal_data_rate_bps"] = _loss(ps_config.nominal_data_rate_bps, degradation.sensitivity_loss_pct)
    if hasattr(ps_config, "min_quality_score"):
        updates["min_quality_score"] = min(1.0, _increase(ps_config.min_quality_score, degradation.noise_increase_pct + degradation.calibration_drift_pct))
    if hasattr(ps_config, "max_pointing_error_deg"):
        updates["max_pointing_error_deg"] = _loss(ps_config.max_pointing_error_deg, degradation.calibration_drift_pct)
    return _safe_replace(ps_config, **updates)

