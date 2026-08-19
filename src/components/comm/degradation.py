from __future__ import annotations

# Local component degradation declarations; component-local module is the canonical source.
"""通信系统退化模块。

定义通信系统(Comm)的退化状态和退化速率类，用于模拟通信系统的性能退化过程。

文献支撑:
- Shao R, You W, Nie Y. Reliability modeling framework of satellite constellation based on three-parameter interval grey number Lz transformation[J]. Scientific Reports, 2025, 15: 21022.
- 航天器多因素可靠性-洞察研究[R]. 飞鸽文档, 2024.
"""
from dataclasses import dataclass
from typing import Literal

from ..degradation_base import DegradationModel, DegradationRate, DegradationState


@dataclass(frozen=True)
class CommDegradation(DegradationState):
    """通信系统退化状态。

    记录通信系统的关键退化参数，包括传输功率损失、接收灵敏度下降和误码率增加。

    Attributes:
        transmit_power_loss_pct: 发射功率损失百分比（0-100）
        receive_sensitivity_loss_pct: 接收灵敏度损失百分比（0-100）
        bit_error_rate_increase_pct: 误码率增加百分比（0-100）
        bandwidth_efficiency_loss_pct: 带宽效率损失百分比（0-100）
        antenna_gain_loss_pct: 天线增益损失百分比（0-100）
    """
    transmit_power_loss_pct: float = 0.0
    receive_sensitivity_loss_pct: float = 0.0
    bit_error_rate_increase_pct: float = 0.0
    bandwidth_efficiency_loss_pct: float = 0.0
    antenna_gain_loss_pct: float = 0.0

    @property
    def component_type(self) -> str:
        return "comm"


@dataclass(frozen=True)
class CommDegradationRate(DegradationRate):
    """通信系统退化速率。

    定义通信系统各退化参数的变化速率，支持固定和随机两种模式。

    Attributes:
        transmit_power_loss_rate_per_year: 每年发射功率损失速率（百分比）
        receive_sensitivity_loss_rate_per_year: 每年接收灵敏度损失速率（百分比）
        bit_error_rate_increase_rate_per_year: 每年误码率增加速率（百分比）
        bandwidth_efficiency_loss_rate_per_year: 每年带宽效率损失速率（百分比）
        antenna_gain_loss_rate_per_year: 每年天线增益损失速率（百分比）
    """
    transmit_power_loss_rate_per_year: float = 0.15
    receive_sensitivity_loss_rate_per_year: float = 0.1
    bit_error_rate_increase_rate_per_year: float = 0.2
    bandwidth_efficiency_loss_rate_per_year: float = 0.08
    antenna_gain_loss_rate_per_year: float = 0.12
    model: Literal[DegradationModel.FIXED, DegradationModel.RANDOM] = DegradationModel.FIXED

    @property
    def component_type(self) -> str:
        return "comm"

# L2-light local time-function degradation mechanism declarations
