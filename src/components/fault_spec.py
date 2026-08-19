"""
故障基础模块

定义故障规范的基础数据类。
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class FaultSpec:
    """
    故障规范数据类
    
    用于描述卫星部件的故障特性，包括故障类型、发生时间、持续时间和严重程度等。
    
    属性:
        fault_type: 故障类型，可以是枚举值或字符串标识
        onset_time_s: 故障发生时间（秒），相对于仿真开始时刻
        duration_s: 故障持续时间（秒）
                   - -1.0 表示永久故障
                   - >0 表示瞬态故障，持续指定秒数后自动恢复
        magnitude: 故障严重程度或幅度，具体含义取决于故障类型
                  - 例如：对于容量损失故障，可以表示损失的容量百分比
                  - 对于偏置漂移故障，可以表示偏置值
        target_id: 故障影响的目标部件标识符，可选参数
        
    示例:
        # 永久性电池容量突降故障
        fault = FaultSpec(
            fault_type=BatteryFaultType.SuddenCapacityLoss,
            onset_time_s=1000.0,
            duration_s=-1.0,
            magnitude=0.3,  # 损失30%容量
            target_id="battery_1"
        )
        
        # 瞬态传感器信号丢失故障
        fault = FaultSpec(
            fault_type=SensorFaultType.SignalLoss,
            onset_time_s=500.0,
            duration_s=10.0,  # 持续10秒
            magnitude=1.0,  # 完全丢失
            target_id="imu_1"
        )
    """
    fault_type: str
    onset_time_s: float
    duration_s: float
    magnitude: float
    target_id: Optional[str] = None
    
    def is_permanent(self) -> bool:
        """
        判断故障是否为永久性故障
        
        返回:
            bool: 如果 duration_s == -1.0 返回 True，否则返回 False
        """
        return self.duration_s == -1.0
    
    def is_transient(self) -> bool:
        """
        判断故障是否为瞬态故障
        
        返回:
            bool: 如果 duration_s > 0 返回 True，否则返回 False
        """
        return self.duration_s > 0.0
    
    def get_end_time_s(self) -> Optional[float]:
        """
        获取故障结束时间
        
        返回:
            Optional[float]: 对于瞬态故障返回结束时间，对于永久故障返回 None
        """
        if self.is_transient():
            return self.onset_time_s + self.duration_s
        return None
