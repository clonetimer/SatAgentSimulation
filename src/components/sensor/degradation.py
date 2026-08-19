from __future__ import annotations

# Local component degradation declarations; component-local module is the canonical source.
"""传感器退化模块。

定义传感器退化状态类，用于模拟传感器的性能退化过程。
"""
from dataclasses import dataclass
from typing import Literal

from ..degradation_base import DegradationModel, DegradationRate, DegradationState


@dataclass(frozen=True)
class SensorDegradation(DegradationState):
    """传感器退化状态。

    记录传感器的关键退化参数，包括噪声增加和偏置漂移。

    Attributes:
        noise_increase_pct: 噪声增加百分比（0-100）
        bias_drift_factor: 偏置漂移因子（无单位，表示漂移严重程度）
    """
    noise_increase_pct: float = 0.0
    bias_drift_factor: float = 0.0

    @property
    def component_type(self) -> str:
        """返回部件类型标识。

        Returns:
            str: "sensor"
        """
        return "sensor"


@dataclass(frozen=True)
class SensorDegradationRate(DegradationRate):
    """传感器退化速率。

    定义传感器各退化参数的变化速率，支持固定和随机两种模式。

    Attributes:
        noise_increase_rate_per_year: 每年噪声增加速率（百分比）
        bias_drift_rate_per_day: 每天偏置漂移速率
        model: 退化模型类型（固定或随机）
    """
    noise_increase_rate_per_year: float = 0.5  # 每年增加 0.5%
    bias_drift_rate_per_day: float = 1e-6  # 每天漂移因子增加 1e-6
    model: Literal[DegradationModel.FIXED, DegradationModel.RANDOM] = DegradationModel.FIXED

    @property
    def component_type(self) -> str:
        """返回部件类型标识。

        Returns:
            str: "sensor"
        """
        return "sensor"

# L2-light local time-function degradation mechanism declarations
