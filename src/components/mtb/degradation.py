from __future__ import annotations

# Local component degradation declarations; component-local module is the canonical source.
"""磁力矩器退化模块。

定义磁力矩器(MTB)的退化状态和退化速率类，用于模拟MTB的性能退化过程。

文献支撑:
- 基于磁力矩器的卫星姿态容错控制方法[P]. 中国专利, CN202511413583.
- Khan A, et al. Reconfigurable Asymmetric Embedded Magnetorquers for Attitude Control of Nanosatellites[J]. IEEE Transactions on Aerospace and Electronic Systems, 2021.
- Mohammadi A, et al. Adaptive Attitude Control of a Satellite by Considering Magnetorquer Faults[J]. ISA Transactions, 2022.
"""
from dataclasses import dataclass
from typing import Literal

from ..degradation_base import DegradationModel, DegradationRate, DegradationState
from ..fault_spec import FaultSpec


@dataclass(frozen=True)
class MTBDegradation(DegradationState):
    """磁力矩器退化状态。

    记录磁力矩器的关键退化参数，包括线圈电阻增加、退磁和功率效率下降。

    Attributes:
        coil_resistance_increase_pct: 线圈电阻增加百分比（0-100）
        magnetic_flux_loss_pct: 磁通量损失百分比（0-100）
        power_efficiency_loss_pct: 功率效率损失百分比（0-100）
        maximum_torque_loss_pct: 最大力矩损失百分比（0-100）
        temperature_coefficient_drift_pct: 温度系数漂移百分比（0-100）
    """
    coil_resistance_increase_pct: float = 0.0
    magnetic_flux_loss_pct: float = 0.0
    power_efficiency_loss_pct: float = 0.0
    maximum_torque_loss_pct: float = 0.0
    temperature_coefficient_drift_pct: float = 0.0

    @property
    def component_type(self) -> str:
        return "mtb"


@dataclass(frozen=True)
class MTBDegradationRate(DegradationRate):
    """磁力矩器退化速率。

    定义磁力矩器各退化参数的变化速率，支持固定和随机两种模式。

    Attributes:
        coil_resistance_increase_rate_per_year: 每年线圈电阻增加速率（百分比）
        magnetic_flux_loss_rate_per_year: 每年磁通量损失速率（百分比）
        power_efficiency_loss_rate_per_year: 每年功率效率损失速率（百分比）
        maximum_torque_loss_rate_per_year: 每年最大力矩损失速率（百分比）
        temperature_coefficient_drift_rate_per_year: 每年温度系数漂移速率（百分比）
    """
    coil_resistance_increase_rate_per_year: float = 0.2
    magnetic_flux_loss_rate_per_year: float = 0.1
    power_efficiency_loss_rate_per_year: float = 0.15
    maximum_torque_loss_rate_per_year: float = 0.1
    temperature_coefficient_drift_rate_per_year: float = 0.05
    model: Literal[DegradationModel.FIXED, DegradationModel.RANDOM] = DegradationModel.FIXED

    @property
    def component_type(self) -> str:
        return "mtb"

# L2-light local time-function degradation mechanism declarations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Any

from ..degradation_base import ParameterEffect, TimeFunctionDegradation


BASE_EFFECTIVE_PARAMS: dict[str, float] = {'dipole_limit_am2': 0.25, 'coil_resistance_ohm': 12.0, 'current_factor': 1.0, 'magnetic_moment_factor': 1.0}


class MtbL2DegradationType(str, Enum):
    COIL_RESISTANCE_GROWTH = 'coil_resistance_growth'
    MAGNETIC_MOMENT_DRIFT = 'magnetic_moment_drift'


def _effects_for(kind: MtbL2DegradationType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        MtbL2DegradationType.COIL_RESISTANCE_GROWTH: (
            ParameterEffect('coil_resistance_ohm', 'grow_multiply', 0.5, 'winding aging increases resistance'),
            ParameterEffect('current_factor', 'degrade_multiply', 0.15, 'winding aging increases resistance'),
        ),
        MtbL2DegradationType.MAGNETIC_MOMENT_DRIFT: (
            ParameterEffect('magnetic_moment_factor', 'degrade_multiply', 0.25, 'core demagnetization reduces moment efficiency'),
        ),
    }
    return effects_by_type[kind]


PHYSICAL_MECHANISMS = {
    MtbL2DegradationType.COIL_RESISTANCE_GROWTH: 'winding aging increases resistance',
    MtbL2DegradationType.MAGNETIC_MOMENT_DRIFT: 'core demagnetization reduces moment efficiency',
}


@dataclass
class MtbL2LightDegradation(TimeFunctionDegradation):
    """Base L2-light degradation for mtb."""
    degradation_type_enum: MtbL2DegradationType = MtbL2DegradationType.COIL_RESISTANCE_GROWTH
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
class MtbCoilResistanceGrowthDegradation(MtbL2LightDegradation):
    """winding aging increases resistance."""
    degradation_type_enum: MtbL2DegradationType = MtbL2DegradationType.COIL_RESISTANCE_GROWTH
    start_s: float = 60.0
    rate_per_s: float = 0.001


@dataclass
class MtbMagneticMomentDriftDegradation(MtbL2LightDegradation):
    """core demagnetization reduces moment efficiency."""
    degradation_type_enum: MtbL2DegradationType = MtbL2DegradationType.MAGNETIC_MOMENT_DRIFT
    start_s: float = 150.0
    rate_per_s: float = 0.0007


def base_effective_params() -> dict[str, float]:
    """Return effective parameters used by L2-light event/time-function runner."""
    return dict(BASE_EFFECTIVE_PARAMS)


def default_degradations() -> list[MtbL2LightDegradation]:
    """Return representative L2-light time-function degradations."""
    return [
        MtbCoilResistanceGrowthDegradation(),
        MtbMagneticMomentDriftDegradation(),
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
    "BASE_EFFECTIVE_PARAMS", 'MtbL2DegradationType', 'MTBDegradation', "base_effective_params", "default_degradations", "evaluate_effective_params",
    'MtbCoilResistanceGrowthDegradation',
    'MtbMagneticMomentDriftDegradation', 'apply_runtime_mtb_degradation',
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
    if isinstance(rate, MTBDegradationRate):
        return MTBDegradation(**_state_kwargs_from_rate(rate, MTBDegradation, years_elapsed))
    raise ValueError(f"不支持的退化速率类型: {type(rate).__name__}")

def apply_mtb_degradation(mtb_config, degradation: MTBDegradation):
    updates = {}
    if hasattr(mtb_config, "dipole_limit_am2"):
        updates["dipole_limit_am2"] = _tuple_loss(mtb_config.dipole_limit_am2, degradation.magnetic_flux_loss_pct + degradation.maximum_torque_loss_pct)
    if hasattr(mtb_config, "lag_tau_s"):
        updates["lag_tau_s"] = _increase(mtb_config.lag_tau_s, degradation.coil_resistance_increase_pct + degradation.power_efficiency_loss_pct)
    return _safe_replace(mtb_config, **updates)



# --- Basilisk runtime degradation application (component-owned) ---


# --- Basilisk runtime degradation application (component-owned) ---
def apply_runtime_mtb_degradation(target: object, *, derate_fraction: float, set_attr=None) -> tuple[dict[str, object], ...]:
    spec = FaultSpec("dipole_saturation", 0.0, -1.0, float(derate_fraction), "mtb.primary")
    from .faults import apply_runtime_mtb_fault
    return apply_runtime_mtb_fault(target, spec, set_attr=set_attr)

