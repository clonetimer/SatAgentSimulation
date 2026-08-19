from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
太阳能帆板故障类型模块

定义太阳能帆板部件可能发生的故障类型。
"""

from enum import Enum


class SolarPanelFaultType(Enum):
    """
    太阳能帆板故障类型枚举

    定义太阳能帆板部件可能发生的各种故障类型。

    枚举值:
        Failure: 太阳能帆板失效
            - 太阳能帆板完全失效，无法发电
            - magnitude 表示失效程度（0-1），1.0为完全失效

        Degradation: 性能退化
            - 太阳能帆板性能下降
            - magnitude 表示性能下降比例（0-1）

        DeploymentFailure: 展开故障
            - 太阳能帆板无法正常展开
            - magnitude 表示展开失败程度（0-1）

    示例:
        # 太阳能帆板完全失效
        fault = FaultSpec(
            fault_type=SolarPanelFaultType.Failure,
            onset_time_s=1000.0,
            duration_s=-1.0,
            magnitude=1.0,  # 完全失效
            target_id="solar_panel_1"
        )
    """
    Failure = "solar_panel_failure"  # 太阳能帆板失效
    Degradation = "solar_panel_degradation"  # 性能退化
    DeploymentFailure = "deployment_failure"  # 展开故障

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class SolarPanelL2FaultType(str, Enum):
    STRING_OPEN = 'string_open'
    SHADING = 'shading'
    REGULATOR_FAULT = 'regulator_fault'


def _effects_for(kind: SolarPanelL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        SolarPanelL2FaultType.STRING_OPEN: (
            ParameterEffect('string_availability', 'multiply', 0.55, 'cell string open removes active cell area'),
            ParameterEffect('output_power_w', 'multiply', 0.55, 'cell string open removes active cell area'),
        ),
        SolarPanelL2FaultType.SHADING: (
            ParameterEffect('output_power_w', 'multiply', 0.35, 'shadowing reduces illuminated area and power'),
        ),
        SolarPanelL2FaultType.REGULATOR_FAULT: (
            ParameterEffect('output_power_w', 'multiply', 0.65, 'regulator fault clamps delivered output power'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class SolarPanelFault(TimeWindowFault):
    """Base L2-light fault for solar_panel."""
    fault_type_enum: SolarPanelL2FaultType = SolarPanelL2FaultType.STRING_OPEN
    start_s: float = 120.0
    end_s: float = 300.0
    severity: float = 1.0
    target_id: str = ""

    def __post_init__(self) -> None:
        self.name = type(self).__name__
        self.fault_type = self.fault_type_enum.value
        self.effects = _effects_for(self.fault_type_enum)
        self.physical_mechanism = PHYSICAL_MECHANISMS[self.fault_type_enum]


PHYSICAL_MECHANISMS = {
    SolarPanelL2FaultType.STRING_OPEN: 'cell string open removes active cell area',
    SolarPanelL2FaultType.SHADING: 'shadowing reduces illuminated area and power',
    SolarPanelL2FaultType.REGULATOR_FAULT: 'regulator fault clamps delivered output power',
}


@dataclass
class SolarPanelStringOpenFault(SolarPanelFault):
    """cell string open removes active cell area."""
    fault_type_enum: SolarPanelL2FaultType = SolarPanelL2FaultType.STRING_OPEN
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class SolarPanelShadingFault(SolarPanelFault):
    """shadowing reduces illuminated area and power."""
    fault_type_enum: SolarPanelL2FaultType = SolarPanelL2FaultType.SHADING
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class SolarPanelRegulatorFaultFault(SolarPanelFault):
    """regulator fault clamps delivered output power."""
    fault_type_enum: SolarPanelL2FaultType = SolarPanelL2FaultType.REGULATOR_FAULT
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[SolarPanelFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        SolarPanelStringOpenFault(),
        SolarPanelShadingFault(),
    ]

__all__ = [
    'SolarPanelFaultType', 'SolarPanelFault', "default_faults",
    'SolarPanelStringOpenFault',
    'SolarPanelShadingFault',
    'SolarPanelRegulatorFaultFault',
]

# --- Local config-level fault application support migrated from root legacy module ---
from dataclasses import replace
from dataclasses import fields as _fault_dc_fields, is_dataclass as _fault_is_dataclass

def _fault_name(spec: FaultSpec) -> str:
    return getattr(spec.fault_type, "name", str(spec.fault_type))

def _fault_value(spec: FaultSpec) -> str:
    return getattr(spec.fault_type, "value", str(spec.fault_type))

def _mag(spec: FaultSpec, default: float = 1.0) -> float:
    try:
        return max(0.0, float(spec.magnitude))
    except Exception:
        return default

def _safe_replace_fault(config, **updates):
    if not updates:
        return config
    if _fault_is_dataclass(config):
        allowed = {f.name for f in _fault_dc_fields(config)}
        filtered = {key: value for key, value in updates.items() if key in allowed}
        if filtered:
            return replace(config, **filtered)
    return config

def _loss_scalar(value, fraction: float):
    return max(0.0, float(value) * (1.0 - max(0.0, float(fraction))))

def _loss_like(value, fraction: float):
    if isinstance(value, tuple):
        return tuple(_loss_scalar(v, fraction) for v in value)
    if isinstance(value, list):
        return [_loss_scalar(v, fraction) for v in value]
    return _loss_scalar(value, fraction)

def _zero_like(value):
    if isinstance(value, tuple):
        return tuple(0.0 for _ in value)
    if isinstance(value, list):
        return [0.0 for _ in value]
    return 0.0

def _increase_scalar(value, fraction: float):
    return max(0.0, float(value) * (1.0 + max(0.0, float(fraction))))

def _increase_like(value, fraction: float):
    if isinstance(value, tuple):
        return tuple(_increase_scalar(v, fraction) for v in value)
    if isinstance(value, list):
        return [_increase_scalar(v, fraction) for v in value]
    return _increase_scalar(value, fraction)

def apply_solar_panel_faults(solar_config, fault_specs: list[FaultSpec]) -> object:
    cfg = solar_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"Failure", "DeploymentFailure"} and hasattr(cfg, "max_power_w"):
            updates["max_power_w"] = 0.0
        if name in {"Degradation", "CellDegradation", "CellFailure", "Shadowing"} and hasattr(cfg, "max_power_w"):
            updates["max_power_w"] = _loss_scalar(cfg.max_power_w, mag)
        if hasattr(cfg, "efficiency") and name in {"Degradation", "CellDegradation"}:
            updates["efficiency"] = _loss_scalar(cfg.efficiency, mag)
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

