from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
通信系统故障类型模块

定义通信系统部件可能发生的故障类型。
"""

from enum import Enum


class CommFaultType(Enum):
    """
    通信系统故障类型枚举
    
    定义通信系统部件可能发生的各种故障类型。
    
    枚举值:
        LinkLoss: 链路丢失
            - 通信链路完全中断
            - magnitude 表示丢失程度（0-1），1.0为完全丢失
            
        DataCorruption: 数据损坏
            - 传输的数据发生损坏或错误
            - magnitude 表示数据损坏率（0-1）
            
        Intermittent: 间歇性故障
            - 通信链路不稳定，时断时续
            - magnitude 表示故障发生的频率或概率（0-1）
    
    示例:
        # 通信链路丢失
        fault = FaultSpec(
            fault_type=CommFaultType.LinkLoss,
            onset_time_s=3000.0,
            duration_s=-1.0,
            magnitude=1.0,  # 完全丢失
            target_id="transmitter_1"
        )
        
        # 间歇性通信故障
        fault = FaultSpec(
            fault_type=CommFaultType.Intermittent,
            onset_time_s=3000.0,
            duration_s=100.0,  # 持续100秒
            magnitude=0.3,  # 30%的时间出现故障
            target_id="antenna_1"
        )
    """
    LinkLoss = "link_loss"  # 链路丢失
    DataCorruption = "data_corruption"  # 数据损坏
    Intermittent = "intermittent"  # 间歇性故障

# L2-light local time-window fault mechanism declarations
