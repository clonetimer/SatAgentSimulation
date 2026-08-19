from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
地面站故障类型模块

定义地面站(GroundStation)部件可能发生的故障类型。

文献支撑:
- 基于MBSE的卫星总体设计与FMEA方法融合及应用研究[R]. 2025.
- 航天器多因素可靠性-洞察研究[R]. 飞鸽文档, 2024.
"""

from enum import Enum


class GroundStationFaultType(Enum):
    """
    地面站故障类型枚举

    地面站故障主要包括通信中断、天线故障和信号处理故障等模式。

    枚举值:
        CommunicationLoss: 通信丢失
            - 与卫星通信链路中断
            - magnitude 表示中断持续时间（秒）

        AntennaFailure: 天线故障
            - 天线无法正常工作
            - magnitude 表示故障严重程度（0-1）

        SignalProcessingFailure: 信号处理故障
            - 信号处理单元故障
            - magnitude 表示故障严重程度（0-1）

        TrackingLoss: 跟踪丢失
            - 无法跟踪卫星
            - magnitude 表示丢失跟踪时间（秒）

        FrequencyDrift: 频率漂移
            - 收发频率偏离
            - magnitude 表示漂移量（Hz）

        PowerFailure: 电力故障
            - 地面站电力供应中断
            - magnitude 表示中断持续时间（秒）

        DataTransmissionFailure: 数据传输故障
            - 数据传输失败
            - magnitude 表示传输失败比例（0-1）

        WeatherBlockage: 天气遮挡
            - 恶劣天气导致信号衰减
            - magnitude 表示信号衰减比例（0-1）

        EquipmentMalfunction: 设备故障
            - 地面站设备故障
            - magnitude 表示故障设备数量比例（0-1）
    """
    CommunicationLoss = "communication_loss"
    AntennaFailure = "antenna_failure"
    SignalProcessingFailure = "signal_processing_failure"
    TrackingLoss = "tracking_loss"
    FrequencyDrift = "frequency_drift"
    PowerFailure = "power_failure"
    DataTransmissionFailure = "data_transmission_failure"
    WeatherBlockage = "weather_blockage"
    EquipmentMalfunction = "equipment_malfunction"

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class GroundStationL2FaultType(str, Enum):
    TRACKING_LOSS = 'tracking_loss'
    WEATHER_FADE = 'weather_fade'
    RECEIVER_OUTAGE = 'receiver_outage'


def _effects_for(kind: GroundStationL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        GroundStationL2FaultType.TRACKING_LOSS: (
            ParameterEffect('tracking_error_deg', 'add', 8.0, 'tracking loop loss increases pointing error'),
            ParameterEffect('g_over_t_db', 'add', -8.0, 'tracking loop loss increases pointing error'),
        ),
        GroundStationL2FaultType.WEATHER_FADE: (
            ParameterEffect('weather_loss_db', 'add', 6.0, 'rain/cloud attenuation reduces receive margin'),
            ParameterEffect('g_over_t_db', 'add', -3.0, 'rain/cloud attenuation reduces receive margin'),
        ),
        GroundStationL2FaultType.RECEIVER_OUTAGE: (
            ParameterEffect('availability', 'override', 0.0, 'receiver chain outage removes contact availability'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class GroundStationFault(TimeWindowFault):
    """Base L2-light fault for ground_station."""
    fault_type_enum: GroundStationL2FaultType = GroundStationL2FaultType.TRACKING_LOSS
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
    GroundStationL2FaultType.TRACKING_LOSS: 'tracking loop loss increases pointing error',
    GroundStationL2FaultType.WEATHER_FADE: 'rain/cloud attenuation reduces receive margin',
    GroundStationL2FaultType.RECEIVER_OUTAGE: 'receiver chain outage removes contact availability',
}


@dataclass
class GroundStationTrackingLossFault(GroundStationFault):
    """tracking loop loss increases pointing error."""
    fault_type_enum: GroundStationL2FaultType = GroundStationL2FaultType.TRACKING_LOSS
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class GroundStationWeatherFadeFault(GroundStationFault):
    """rain/cloud attenuation reduces receive margin."""
    fault_type_enum: GroundStationL2FaultType = GroundStationL2FaultType.WEATHER_FADE
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class GroundStationReceiverOutageFault(GroundStationFault):
    """receiver chain outage removes contact availability."""
    fault_type_enum: GroundStationL2FaultType = GroundStationL2FaultType.RECEIVER_OUTAGE
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[GroundStationFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        GroundStationTrackingLossFault(),
        GroundStationWeatherFadeFault(),
    ]

__all__ = [
    'GroundStationFaultType', 'GroundStationFault', "default_faults",
    'GroundStationTrackingLossFault',
    'GroundStationWeatherFadeFault',
    'GroundStationReceiverOutageFault',
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

def apply_ground_station_faults(gs_config, fault_specs: list[FaultSpec]) -> object:
    cfg = gs_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"CommunicationLoss", "AntennaFailure", "SignalProcessingFailure", "PowerFailure", "DataTransmissionFailure", "WeatherBlockage", "EquipmentMalfunction"} and hasattr(cfg, "max_range_m"):
            updates["max_range_m"] = _loss_scalar(cfg.max_range_m, 1.0 if name in {"CommunicationLoss", "PowerFailure"} else mag)
        if name == "TrackingLoss" and hasattr(cfg, "min_elevation_deg"):
            updates["min_elevation_deg"] = _increase_scalar(cfg.min_elevation_deg, mag)
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

