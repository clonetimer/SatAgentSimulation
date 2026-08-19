"""Degradation base classes for component-local degradation mechanisms.

This module keeps the original static degradation state/rate bases and adds the
L2-light time-function degradation base.  Component-specific degradation classes
live in ``components/<component>/degradation.py``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Mapping, Sequence
import math

from .fault_base import ParameterEffect, copy_params


class DegradationModel(Enum):
    """退化模型类型枚举。"""
    FIXED = "fixed"      # 固定退化速率
    RANDOM = "random"   # 随机退化速率


@dataclass(frozen=True)
class DegradationState(ABC):
    """退化状态基类。

    所有部件的退化状态类都应继承此类，并定义部件特定的退化参数。
    """
    @property
    @abstractmethod
    def component_type(self) -> str:
        """返回部件类型标识。

        Returns:
            str: 部件类型名称
        """
        pass


@dataclass(frozen=True)
class DegradationRate(ABC):
    """退化速率基类。

    所有部件的退化速率类都应继承此类，支持固定和随机两种退化模式。
    """
    model: Literal[DegradationModel.FIXED, DegradationModel.RANDOM] = DegradationModel.FIXED

    @property
    @abstractmethod
    def component_type(self) -> str:
        """返回部件类型标识。

        Returns:
            str: 部件类型名称
        """
        pass


@dataclass
class TimeFunctionDegradation:
    """Base class for time-parameterized component degradation.

    Degradation starts at ``start_s`` and produces a bounded coefficient as a
    function of elapsed time.  The class does not integrate internal state; it
    modifies effective parameters when a runner queries a scenario time.
    """

    start_s: float = 60.0
    rate_per_s: float = 2.0e-4
    max_fraction: float = 1.0
    severity: float = 1.0
    law: str = "saturating_exponential"
    name: str = "TimeFunctionDegradation"
    degradation_type: str = "generic_degradation"
    physical_mechanism: str = "time-function parameterized component degradation"
    effects: Sequence[ParameterEffect] = field(default_factory=tuple)

    def age_s(self, t_s: float) -> float:
        return max(0.0, float(t_s) - self.start_s)

    def coefficient(self, t_s: float) -> float:
        age = self.age_s(t_s)
        if age <= 0.0:
            return 0.0
        if self.law == "linear":
            raw = self.rate_per_s * age
        elif self.law == "exponential":
            raw = 1.0 - math.exp(-self.rate_per_s * age)
        elif self.law == "saturating_exponential":
            raw = 1.0 - math.exp(-self.rate_per_s * age)
        elif self.law == "step":
            raw = 1.0
        else:
            raise ValueError(f"Unsupported degradation law: {self.law}")
        return min(self.max_fraction, max(0.0, raw)) * self.severity

    def apply(self, params: Mapping[str, Any], t_s: float, context: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        out = copy_params(params)
        coeff = self.coefficient(t_s)
        if coeff <= 0.0:
            return out, {
                "name": self.name,
                "degradation_type": self.degradation_type,
                "active": False,
                "start_s": self.start_s,
                "coefficient": 0.0,
                "law": self.law,
            }
        before = copy_params(out)
        applied = []
        for effect in self.effects:
            out = effect.apply(out, severity=1.0, factor=coeff)
            applied.append({
                "parameter": effect.parameter,
                "operation": effect.operation,
                "magnitude": effect.magnitude,
                "description": effect.description,
                "before": before.get(effect.parameter),
                "after": out.get(effect.parameter),
            })
        return out, {
            "name": self.name,
            "degradation_type": self.degradation_type,
            "active": True,
            "start_s": self.start_s,
            "age_s": self.age_s(t_s),
            "coefficient": coeff,
            "law": self.law,
            "rate_per_s": self.rate_per_s,
            "max_fraction": self.max_fraction,
            "physical_mechanism": self.physical_mechanism,
            "effects": applied,
        }
