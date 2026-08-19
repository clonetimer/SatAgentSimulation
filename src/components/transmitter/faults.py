from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
发射机故障类型模块

定义发射机(Transmitter)部件可能发生的故障类型。

文献支撑:
- 航天器多因素可靠性-洞察研究[R]. 飞鸽文档, 2024.
"""

from enum import Enum


class TransmitterFaultType(Enum):
    """
    发射机故障类型枚举

    发射机故障主要包括功率下降、频率漂移和通信中断等模式。

    枚举值:
        PowerLoss: 功率损失
            - 发射功率下降
            - magnitude 表示功率损失比例（0-1）

        FrequencyDrift: 频率漂移
            - 发射频率偏离
            - magnitude 表示漂移量（Hz）

        ModulationFailure: 调制故障
            - 调制信号异常
            - magnitude 表示故障严重程度（0-1）

        SignalLoss: 信号丢失
            - 发射信号完全丢失
            - magnitude 表示丢失持续时间（秒）

        PowerAmplifierFailure: 功率放大器故障
            - 功率放大器失效
            - magnitude 表示故障严重程度（0-1）

        AntennaMismatch: 天线失配
            - 天线阻抗失配
            - magnitude 表示失配程度（0-1）

        Overheating: 过热故障
            - 发射机温度超过安全阈值
            - magnitude 表示温度超阈值比例

        PowerSupplyFailure: 电源故障
            - 发射机电源供应故障
            - magnitude 表示故障持续时间（秒）
    """
    PowerLoss = "power_loss"
    FrequencyDrift = "frequency_drift"
    ModulationFailure = "modulation_failure"
    SignalLoss = "signal_loss"
    PowerAmplifierFailure = "power_amplifier_failure"
    AntennaMismatch = "antenna_mismatch"
    Overheating = "overheating"
    PowerSupplyFailure = "power_supply_failure"

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class TransmitterL2FaultType(str, Enum):
    POWER_AMPLIFIER_FAULT = 'power_amplifier_fault'
    FREQUENCY_DRIFT_FAULT = 'frequency_drift_fault'
    SIGNAL_LOSS = 'signal_loss'


def _effects_for(kind: TransmitterL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        TransmitterL2FaultType.POWER_AMPLIFIER_FAULT: (
            ParameterEffect('rf_power_w', 'multiply', 0.35, 'PA fault reduces RF output and raises waste heat'),
            ParameterEffect('pa_efficiency', 'multiply', 0.55, 'PA fault reduces RF output and raises waste heat'),
            ParameterEffect('heat_generation_w', 'add', 8.0, 'PA fault reduces RF output and raises waste heat'),
        ),
        TransmitterL2FaultType.FREQUENCY_DRIFT_FAULT: (
            ParameterEffect('frequency_error_hz', 'add', 2500.0, 'oscillator fault shifts carrier frequency'),
        ),
        TransmitterL2FaultType.SIGNAL_LOSS: (
            ParameterEffect('availability', 'override', 0.0, 'RF chain loss removes transmit availability'),
            ParameterEffect('rf_power_w', 'override', 0.0, 'RF chain loss removes transmit availability'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class TransmitterFault(TimeWindowFault):
    """Base L2-light fault for transmitter."""
    fault_type_enum: TransmitterL2FaultType = TransmitterL2FaultType.POWER_AMPLIFIER_FAULT
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
    TransmitterL2FaultType.POWER_AMPLIFIER_FAULT: 'PA fault reduces RF output and raises waste heat',
    TransmitterL2FaultType.FREQUENCY_DRIFT_FAULT: 'oscillator fault shifts carrier frequency',
    TransmitterL2FaultType.SIGNAL_LOSS: 'RF chain loss removes transmit availability',
}


@dataclass
class TransmitterPowerAmplifierFaultFault(TransmitterFault):
    """PA fault reduces RF output and raises waste heat."""
    fault_type_enum: TransmitterL2FaultType = TransmitterL2FaultType.POWER_AMPLIFIER_FAULT
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class TransmitterFrequencyDriftFaultFault(TransmitterFault):
    """oscillator fault shifts carrier frequency."""
    fault_type_enum: TransmitterL2FaultType = TransmitterL2FaultType.FREQUENCY_DRIFT_FAULT
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class TransmitterSignalLossFault(TransmitterFault):
    """RF chain loss removes transmit availability."""
    fault_type_enum: TransmitterL2FaultType = TransmitterL2FaultType.SIGNAL_LOSS
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[TransmitterFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        TransmitterPowerAmplifierFaultFault(),
        TransmitterFrequencyDriftFaultFault(),
    ]

__all__ = [
    'TransmitterFaultType', 'TransmitterFault', "default_faults",
    'TransmitterPowerAmplifierFaultFault',
    'TransmitterFrequencyDriftFaultFault',
    'TransmitterSignalLossFault',
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

def apply_transmitter_faults(tx_config, fault_specs: list[FaultSpec]) -> object:
    cfg = tx_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"PowerLoss", "SignalLoss", "PowerSupplyFailure"} and hasattr(cfg, "max_tx_power_w"):
            updates["max_tx_power_w"] = 0.0
        if name in {"PowerAmplifierFailure", "ModulationFailure", "ModulatorFailure", "AntennaMismatch"}:
            if hasattr(cfg, "max_tx_power_w"):
                updates["max_tx_power_w"] = _loss_scalar(cfg.max_tx_power_w, mag)
            if hasattr(cfg, "gain_dB"):
                updates["gain_dB"] = _loss_scalar(cfg.gain_dB, mag)
        if name in {"FrequencyDrift", "Overheating"} and hasattr(cfg, "efficiency"):
            updates["efficiency"] = _loss_scalar(cfg.efficiency, mag)
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

# --- Basilisk runtime fault application (component-owned) ---
def apply_runtime_transmitter_fault(transmitter: object, spec: FaultSpec, *, set_attr) -> bool:
    """Apply transmitter-owned runtime fault hooks."""

    fault_value = str(getattr(spec.fault_type, "value", spec.fault_type))
    magnitude = max(0.0, float(getattr(spec, "magnitude", 0.0)))
    if fault_value in {"link_loss", "signal_loss", "power_loss"}:
        if hasattr(transmitter, "transmitterStatus"):
            set_attr(transmitter, "transmitterStatus", 0)
            return True
        if hasattr(transmitter, "nodeBaudRate"):
            set_attr(transmitter, "nodeBaudRate", 0.0)
            return True
    if fault_value == "power_amplifier_failure" and hasattr(transmitter, "nodeBaudRate"):
        set_attr(transmitter, "nodeBaudRate", float(getattr(transmitter, "nodeBaudRate")) * max(0.0, 1.0 - magnitude))
        return True
    if fault_value == "data_corruption" and hasattr(transmitter, "dataCorruptionRate"):
        set_attr(transmitter, "dataCorruptionRate", magnitude)
        return True
    if fault_value == "intermittent" and hasattr(transmitter, "intermittentFaultProb"):
        set_attr(transmitter, "intermittentFaultProb", magnitude)
        return True
    return False

