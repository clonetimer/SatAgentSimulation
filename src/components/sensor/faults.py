from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
传感器故障类型模块

定义传感器部件可能发生的故障类型。
"""

from enum import Enum


class SensorFaultType(Enum):
    """
    传感器故障类型枚举
    
    定义传感器部件可能发生的各种故障类型。
    
    枚举值:
        SignalLoss: 信号丢失
            - 传感器完全失去信号输出
            - magnitude 表示丢失程度（0-1），1.0为完全丢失
            
        BiasDrift: 偏置漂移
            - 传感器测量值产生固定偏移
            - magnitude 表示偏置值（单位取决于传感器类型）
            
        NoiseIncrease: 噪声增加
            - 传感器输出噪声异常增大
            - magnitude 表示噪声增大的倍数（1.0 为正常噪声水平）
    
    示例:
        # IMU 偏置漂移故障
        fault = FaultSpec(
            fault_type=SensorFaultType.BiasDrift,
            onset_time_s=1500.0,
            duration_s=-1.0,
            magnitude=0.1,  # 偏置值 0.1 rad/s
            target_id="imu_1"
        )
    """
    SignalLoss = "signal_loss"  # 信号丢失
    BiasDrift = "bias_drift"  # 偏置漂移
    NoiseIncrease = "noise_increase"  # 噪声增加

# L2-light local time-window fault mechanism declarations

# --- Basilisk runtime fault application (component-owned) ---
def apply_runtime_sensor_fault(sensor: object, spec: FaultSpec, *, set_attr) -> bool:
    """Apply generic sensor runtime hooks without modifying output messages."""

    fault_value = str(getattr(spec.fault_type, "value", spec.fault_type))
    magnitude = float(getattr(spec, "magnitude", 0.0))
    if fault_value == "bias_drift":
        for attr in ("sensorBias", "senBias", "senRotBias", "senTransBias"):
            if not hasattr(sensor, attr):
                continue
            current = getattr(sensor, attr)
            if isinstance(current, (list, tuple)):
                set_attr(sensor, attr, [float(value) + magnitude for value in current])
            else:
                set_attr(sensor, attr, float(current) + magnitude)
            return True
    if fault_value == "noise_increase":
        for attr in ("sensorNoise", "senNoiseStd", "faultNoiseStd"):
            if not hasattr(sensor, attr):
                continue
            current = getattr(sensor, attr)
            if isinstance(current, (list, tuple)):
                set_attr(sensor, attr, [float(value) * (1.0 + magnitude) for value in current])
            else:
                set_attr(sensor, attr, float(current) * (1.0 + magnitude))
            return True
    if fault_value == "signal_loss":
        # Only writable native fault-state/status controls are allowed.  Do not
        # overwrite sensor output payloads as a fault-injection shortcut.
        for attr, value in (("faultState", 1), ("sensorStatus", 0), ("availability", 0.0)):
            if hasattr(sensor, attr):
                set_attr(sensor, attr, value)
                return True
    return False

