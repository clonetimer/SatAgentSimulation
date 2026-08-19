from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
IMU故障类型模块

定义IMU传感器部件可能发生的故障类型。
"""

from enum import Enum


class ImuFaultType(Enum):
    """
    IMU故障类型枚举

    定义IMU传感器部件可能发生的各种故障类型。

    枚举值:
        SignalLoss: 信号丢失
            - IMU完全失去信号输出
            - magnitude 表示丢失程度（0-1），1.0为完全丢失

        GyroBiasDrift: 陀螺仪偏置漂移
            - 陀螺仪测量值产生固定偏移
            - magnitude 表示偏置值（单位：rad/s）

        AccelBiasDrift: 加速度计偏置漂移
            - 加速度计测量值产生固定偏移
            - magnitude 表示偏置值（单位：m/s²）

        GyroNoiseIncrease: 陀螺仪噪声增加
            - 陀螺仪输出噪声异常增大
            - magnitude 表示噪声增大的倍数（1.0 为正常噪声水平）

        AccelNoiseIncrease: 加速度计噪声增加
            - 加速度计输出噪声异常增大
            - magnitude 表示噪声增大的倍数（1.0 为正常噪声水平）

        StuckAtZero: 卡零故障
            - IMU输出始终为零
            - magnitude 无意义

    示例:
        # IMU陀螺仪偏置漂移故障
        from ..fault_spec import FaultSpec
        fault = FaultSpec(
            fault_type=ImuFaultType.GyroBiasDrift,
            onset_time_s=1500.0,
            duration_s=-1.0,
            magnitude=0.1,  # 偏置值 0.1 rad/s
            target_id="imu_1"
        )
    """
    SignalLoss = "signal_loss"              # 信号丢失
    GyroBiasDrift = "gyro_bias_drift"       # 陀螺仪偏置漂移
    AccelBiasDrift = "accel_bias_drift"     # 加速度计偏置漂移
    GyroNoiseIncrease = "gyro_noise_increase"  # 陀螺仪噪声增加
    AccelNoiseIncrease = "accel_noise_increase"  # 加速度计噪声增加
    StuckAtZero = "stuck_at_zero"           # 卡零故障

    @property
    def severity_level(self) -> int:
        """
        获取故障严重程度等级

        返回:
            int: 故障严重程度等级（1-3级）
                - 1级: 轻微故障，影响有限
                - 2级: 中等故障，影响较大
                - 3级: 严重故障，影响重大
        """
        severity_mapping = {
            ImuFaultType.SignalLoss: 3,
            ImuFaultType.StuckAtZero: 3,
            ImuFaultType.GyroBiasDrift: 2,
            ImuFaultType.AccelBiasDrift: 2,
            ImuFaultType.GyroNoiseIncrease: 1,
            ImuFaultType.AccelNoiseIncrease: 1,
        }
        return severity_mapping[self]


# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class ImuL2FaultType(str, Enum):
    BIAS_STEP = 'bias_step'
    AXIS_DROPOUT = 'axis_dropout'
    NOISE_BURST = 'noise_burst'


def _effects_for(kind: ImuL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        ImuL2FaultType.BIAS_STEP: (
            ParameterEffect('bias_rad_s', 'add', 0.01, 'electronics upset creates gyro/accelerometer bias step'),
        ),
        ImuL2FaultType.AXIS_DROPOUT: (
            ParameterEffect('availability', 'multiply', 0.0, 'sensor axis loss invalidates measurement channel'),
        ),
        ImuL2FaultType.NOISE_BURST: (
            ParameterEffect('noise_sigma_rad_s', 'multiply', 8.0, 'electronics disturbance raises measurement noise'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class ImuFault(TimeWindowFault):
    """Base L2-light fault for imu."""
    fault_type_enum: ImuL2FaultType = ImuL2FaultType.BIAS_STEP
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
    ImuL2FaultType.BIAS_STEP: 'electronics upset creates gyro/accelerometer bias step',
    ImuL2FaultType.AXIS_DROPOUT: 'sensor axis loss invalidates measurement channel',
    ImuL2FaultType.NOISE_BURST: 'electronics disturbance raises measurement noise',
}


@dataclass
class ImuBiasStepFault(ImuFault):
    """electronics upset creates gyro/accelerometer bias step."""
    fault_type_enum: ImuL2FaultType = ImuL2FaultType.BIAS_STEP
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class ImuAxisDropoutFault(ImuFault):
    """sensor axis loss invalidates measurement channel."""
    fault_type_enum: ImuL2FaultType = ImuL2FaultType.AXIS_DROPOUT
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class ImuNoiseBurstFault(ImuFault):
    """electronics disturbance raises measurement noise."""
    fault_type_enum: ImuL2FaultType = ImuL2FaultType.NOISE_BURST
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[ImuFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        ImuBiasStepFault(),
        ImuAxisDropoutFault(),
    ]

__all__ = [
    'ImuFaultType', 'ImuFault', "default_faults",
    'ImuBiasStepFault',
    'ImuAxisDropoutFault',
    'ImuNoiseBurstFault',
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

def apply_imu_faults(imu_config, fault_specs: list[FaultSpec]) -> object:
    cfg = imu_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = _mag(spec)
        updates = {}
        if name in {"SignalLoss", "StuckAtZero"}:
            if hasattr(cfg, "gyro_scale"):
                updates["gyro_scale"] = _zero_like(cfg.gyro_scale)
            if hasattr(cfg, "accel_scale"):
                updates["accel_scale"] = _zero_like(cfg.accel_scale)
        if name == "GyroBiasDrift" and hasattr(cfg, "gyro_bias_rad_s"):
            updates["gyro_bias_rad_s"] = tuple(float(v) + mag for v in cfg.gyro_bias_rad_s)
        if name == "AccelBiasDrift" and hasattr(cfg, "accel_bias_m_s2"):
            updates["accel_bias_m_s2"] = tuple(float(v) + mag for v in cfg.accel_bias_m_s2)
        if name == "GyroNoiseIncrease" and hasattr(cfg, "gyro_bias_walk_std_rad_s_sqrt_s"):
            updates["gyro_bias_walk_std_rad_s_sqrt_s"] = _increase_scalar(cfg.gyro_bias_walk_std_rad_s_sqrt_s, mag)
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

