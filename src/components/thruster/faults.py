from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
推进器故障类型模块

定义推进器部件可能发生的故障类型。
"""

from enum import Enum


class ThrusterFaultType(Enum):
    """
    推进器故障类型枚举
    
    定义推进器部件可能发生的各种故障类型。
    
    枚举值:
        IgnitionFailure: 点火失败
            - 推进器无法正常点火或启动
            - magnitude 表示失败概率或严重程度（0-1）
            
        NozzleBlockage: 喷管堵塞
            - 推进器喷管被异物堵塞
            - magnitude 表示堵塞程度（0-1），1.0为完全堵塞
            
        FuelLeak: 燃料泄漏
            - 推进器燃料系统发生泄漏
            - magnitude 表示泄漏速率倍数（1.0 为正常消耗速率）
    
    示例:
        # 推进器喷管完全堵塞
        fault = FaultSpec(
            fault_type=ThrusterFaultType.NozzleBlockage,
            onset_time_s=2000.0,
            duration_s=-1.0,
            magnitude=1.0,  # 完全堵塞
            target_id="thruster_1"
        )
    """
    IgnitionFailure = "ignition_failure"  # 点火失败
    NozzleBlockage = "nozzle_blockage"  # 喷管堵塞
    FuelLeak = "fuel_leak"  # 燃料泄漏

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class ThrusterL2FaultType(str, Enum):
    VALVE_STUCK_CLOSED = 'valve_stuck_closed'
    VALVE_STUCK_OPEN = 'valve_stuck_open'
    NOZZLE_BLOCKAGE = 'nozzle_blockage'


def _effects_for(kind: ThrusterL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        ThrusterL2FaultType.VALVE_STUCK_CLOSED: (
            ParameterEffect('valve_opening', 'override', 0.0, 'valve closed fault blocks propellant flow'),
            ParameterEffect('thrust_n', 'override', 0.0, 'valve closed fault blocks propellant flow'),
            ParameterEffect('mass_flow_factor', 'override', 0.0, 'valve closed fault blocks propellant flow'),
        ),
        ThrusterL2FaultType.VALVE_STUCK_OPEN: (
            ParameterEffect('valve_opening', 'override', 1.0, 'valve open fault commands unintended flow/leak'),
            ParameterEffect('leak_rate_kg_s', 'add', 0.0001, 'valve open fault commands unintended flow/leak'),
        ),
        ThrusterL2FaultType.NOZZLE_BLOCKAGE: (
            ParameterEffect('thrust_n', 'multiply', 0.45, 'nozzle blockage reduces thrust coefficient/mass flow'),
            ParameterEffect('mass_flow_factor', 'multiply', 0.55, 'nozzle blockage reduces thrust coefficient/mass flow'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class ThrusterFault(TimeWindowFault):
    """Base L2-light fault for thruster."""
    fault_type_enum: ThrusterL2FaultType = ThrusterL2FaultType.VALVE_STUCK_CLOSED
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
    ThrusterL2FaultType.VALVE_STUCK_CLOSED: 'valve closed fault blocks propellant flow',
    ThrusterL2FaultType.VALVE_STUCK_OPEN: 'valve open fault commands unintended flow/leak',
    ThrusterL2FaultType.NOZZLE_BLOCKAGE: 'nozzle blockage reduces thrust coefficient/mass flow',
}


@dataclass
class ThrusterValveStuckClosedFault(ThrusterFault):
    """valve closed fault blocks propellant flow."""
    fault_type_enum: ThrusterL2FaultType = ThrusterL2FaultType.VALVE_STUCK_CLOSED
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class ThrusterValveStuckOpenFault(ThrusterFault):
    """valve open fault commands unintended flow/leak."""
    fault_type_enum: ThrusterL2FaultType = ThrusterL2FaultType.VALVE_STUCK_OPEN
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class ThrusterNozzleBlockageFault(ThrusterFault):
    """nozzle blockage reduces thrust coefficient/mass flow."""
    fault_type_enum: ThrusterL2FaultType = ThrusterL2FaultType.NOZZLE_BLOCKAGE
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[ThrusterFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        ThrusterValveStuckClosedFault(),
        ThrusterValveStuckOpenFault(),
    ]

__all__ = [
    'ThrusterFaultType', 'ThrusterFault', "default_faults",
    'ThrusterValveStuckClosedFault',
    'ThrusterValveStuckOpenFault',
    'ThrusterNozzleBlockageFault',
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

def apply_thruster_faults(thruster_config, fault_specs: list[FaultSpec]) -> object:
    cfg = thruster_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name == "IgnitionFailure" and hasattr(cfg, "thrust_n"):
            updates["thrust_n"] = _zero_like(cfg.thrust_n)
        if name in {"NozzleBlockage", "NozzleErosion"} and hasattr(cfg, "thrust_n"):
            updates["thrust_n"] = _loss_like(cfg.thrust_n, mag)
        if name == "FuelLeak" and hasattr(cfg, "isp_s"):
            updates["isp_s"] = _loss_like(cfg.isp_s, mag * 0.2)
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

# --- Basilisk runtime fault application (component-owned) ---
def apply_runtime_thruster_fault(
    thruster: object,
    spec: FaultSpec,
    *,
    set_attr,
    record_mutation=None,
    register_post_restore=None,
) -> bool:
    """Apply a runtime fault to a Basilisk thruster object.

    The component layer owns the native ``THRSimConfig`` field mapping.  The
    caller supplies mutation/audit callbacks so scheduling and recovery remain
    outside the component.
    """

    fault_value = str(getattr(spec.fault_type, "value", spec.fault_type))
    magnitude = max(0.0, float(getattr(spec, "magnitude", 0.0)))
    mutated = False
    if fault_value in {"ignition_failure", "nozzle_blockage"}:
        factor = 0.0 if fault_value == "ignition_failure" else max(0.0, 1.0 - magnitude)
        if hasattr(thruster, "thrustFactor"):
            set_attr(thruster, "thrustFactor", factor)
            mutated = True
        # A component registry may target one THRSimConfig directly (thruster_0,
        # thruster_1, ...).  Support that path so asymmetric faults do not
        # silently degrade the whole cluster.
        if hasattr(thruster, "MaxThrust"):
            set_attr(thruster, "MaxThrust", float(getattr(thruster, "MaxThrust")) * factor)
            mutated = True
        if fault_value == "nozzle_blockage" and hasattr(thruster, "steadyIsp"):
            set_attr(thruster, "steadyIsp", float(getattr(thruster, "steadyIsp")) * max(0.0, 1.0 - 0.5 * magnitude))
            mutated = True
        thruster_data = getattr(thruster, "thrusterData", None)
        try:
            count = len(thruster_data) if thruster_data is not None else 0
        except Exception:
            count = 0
        for idx in range(int(count)):
            try:
                thr_cfg = thruster_data[idx]
            except Exception:
                continue
            if hasattr(thr_cfg, "MaxThrust"):
                set_attr(thr_cfg, "MaxThrust", float(getattr(thr_cfg, "MaxThrust")) * factor)
                mutated = True
            if fault_value == "nozzle_blockage" and hasattr(thr_cfg, "steadyIsp"):
                isp_factor = max(0.0, 1.0 - 0.5 * magnitude)
                set_attr(thr_cfg, "steadyIsp", float(getattr(thr_cfg, "steadyIsp")) * isp_factor)
                mutated = True
    elif fault_value == "fuel_leak" and hasattr(thruster, "thrustFactor"):
        set_attr(thruster, "thrustFactor", magnitude)
        mutated = True

    refresh = getattr(thruster, "UpdateThrusterProperties", None)
    if mutated and callable(refresh):
        refresh()
        if record_mutation is not None:
            record_mutation(thruster, "UpdateThrusterProperties", "not_called", "called")
        if register_post_restore is not None:
            register_post_restore(refresh)
    return mutated

