from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
散热器故障类型模块

定义散热器(Radiator)部件可能发生的故障类型。

文献支撑:
- 天拓五号卫星推进系统在轨故障诊断与定位方法[J]. 国防科技大学学报, 2024, 46(5): 141-149.
- 航天器多因素可靠性-洞察研究[R]. 飞鸽文档, 2024.
"""

from enum import Enum


class RadiatorFaultType(Enum):
    """
    散热器故障类型枚举

    散热器故障主要包括散热能力下降、堵塞和漏热等模式。

    枚举值:
        HeatRejectionDegradation: 散热能力下降
            - 散热器散热效率降低
            - magnitude 表示散热能力下降比例（0-1）

        SurfaceContamination: 表面污染
            - 散热器表面被污染物覆盖
            - magnitude 表示污染面积比例（0-1）

        Blockage: 堵塞
            - 热管或散热通道堵塞
            - magnitude 表示堵塞程度（0-1）

        Leakage: 漏热
            - 散热器密封失效导致热量泄漏
            - magnitude 表示漏热比例（0-1）

        FinsDamage: 散热片损坏
            - 散热片变形或损坏
            - magnitude 表示损坏面积比例（0-1）

        ThermalInterfaceDegradation: 热界面退化
            - 散热器与设备之间的热接触变差
            - magnitude 表示接触热阻增加倍数

        ActuatorFailure: 执行机构故障
            - 可展开散热器展开机构故障
            - magnitude 表示故障严重程度（0-1）

        TemperatureSensorFailure: 温度传感器失效
            - 温度传感器读数错误或失效
            - magnitude 表示失效传感器数量比例（0-1）

        FrostFormation: 结霜
            - 散热器表面结霜影响散热
            - magnitude 表示结霜面积比例（0-1）
    """
    HeatRejectionDegradation = "heat_rejection_degradation"
    SurfaceContamination = "surface_contamination"
    Blockage = "blockage"
    Leakage = "leakage"
    FinsDamage = "fins_damage"
    ThermalInterfaceDegradation = "thermal_interface_degradation"
    ActuatorFailure = "actuator_failure"
    TemperatureSensorFailure = "temperature_sensor_failure"
    FrostFormation = "frost_formation"

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class RadiatorL2FaultType(str, Enum):
    SURFACE_CONTAMINATION = 'surface_contamination'
    DEPLOY_FAILURE = 'deploy_failure'
    BLOCKED_VIEW = 'blocked_view'


def _effects_for(kind: RadiatorL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        RadiatorL2FaultType.SURFACE_CONTAMINATION: (
            ParameterEffect('emissivity', 'multiply', 0.65, 'contamination lowers emissivity and raises absorptivity'),
            ParameterEffect('absorptivity', 'multiply', 1.5, 'contamination lowers emissivity and raises absorptivity'),
            ParameterEffect('rejection_factor', 'multiply', 0.7, 'contamination lowers emissivity and raises absorptivity'),
        ),
        RadiatorL2FaultType.DEPLOY_FAILURE: (
            ParameterEffect('effective_area_m2', 'multiply', 0.35, 'deployment fault reduces effective radiator area'),
            ParameterEffect('rejection_factor', 'multiply', 0.4, 'deployment fault reduces effective radiator area'),
        ),
        RadiatorL2FaultType.BLOCKED_VIEW: (
            ParameterEffect('rejection_factor', 'multiply', 0.45, 'obstruction blocks radiative view factor'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class RadiatorFault(TimeWindowFault):
    """Base L2-light fault for radiator."""
    fault_type_enum: RadiatorL2FaultType = RadiatorL2FaultType.SURFACE_CONTAMINATION
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
    RadiatorL2FaultType.SURFACE_CONTAMINATION: 'contamination lowers emissivity and raises absorptivity',
    RadiatorL2FaultType.DEPLOY_FAILURE: 'deployment fault reduces effective radiator area',
    RadiatorL2FaultType.BLOCKED_VIEW: 'obstruction blocks radiative view factor',
}


@dataclass
class RadiatorSurfaceContaminationFault(RadiatorFault):
    """contamination lowers emissivity and raises absorptivity."""
    fault_type_enum: RadiatorL2FaultType = RadiatorL2FaultType.SURFACE_CONTAMINATION
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class RadiatorDeployFailureFault(RadiatorFault):
    """deployment fault reduces effective radiator area."""
    fault_type_enum: RadiatorL2FaultType = RadiatorL2FaultType.DEPLOY_FAILURE
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class RadiatorBlockedViewFault(RadiatorFault):
    """obstruction blocks radiative view factor."""
    fault_type_enum: RadiatorL2FaultType = RadiatorL2FaultType.BLOCKED_VIEW
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[RadiatorFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        RadiatorSurfaceContaminationFault(),
        RadiatorDeployFailureFault(),
    ]

__all__ = [
    'RadiatorFaultType', 'RadiatorFault', "default_faults",
    'RadiatorSurfaceContaminationFault',
    'RadiatorDeployFailureFault',
    'RadiatorBlockedViewFault',
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

def apply_radiator_faults(radiator_config, fault_specs: list[FaultSpec]) -> object:
    cfg = radiator_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"HeatRejectionDegradation", "Blockage", "Leakage", "FinsDamage", "ThermalInterfaceDegradation", "FrostFormation", "EfficiencyLoss"} and hasattr(cfg, "max_rejection_w"):
            updates["max_rejection_w"] = _loss_scalar(cfg.max_rejection_w, mag)
        if name == "SurfaceContamination" and hasattr(cfg, "emissivity"):
            updates["emissivity"] = _loss_scalar(cfg.emissivity, mag)
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

# --- Basilisk runtime fault application (component-owned) ---
def apply_runtime_radiator_fault(radiator: object, spec: FaultSpec, *, set_attr) -> bool:
    from dataclasses import replace

    fault_value = str(getattr(spec.fault_type, "value", spec.fault_type))
    if fault_value != "thermal_radiator_rejection_loss":
        return False
    magnitude = max(0.0, min(1.0, float(getattr(spec, "magnitude", 0.0))))
    factor = 1.0 - magnitude
    if hasattr(radiator, "rejection_factor"):
        set_attr(radiator, "rejection_factor", factor)
        return True
    if hasattr(radiator, "params") and hasattr(radiator.params, "emissivity"):
        set_attr(radiator, "params", replace(radiator.params, emissivity=float(radiator.params.emissivity) * factor))
        return True
    return False

