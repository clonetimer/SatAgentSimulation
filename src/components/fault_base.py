"""Fault base classes for component-local fault mechanisms.

This module owns the shared building blocks for component fault models.
Component-specific fault classes live in ``components/<component>/faults.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence
import copy


def copy_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep mutable copy of effective component parameters."""
    return copy.deepcopy(dict(params))


@dataclass(frozen=True)
class ParameterEffect:
    """A time-local parameter operation with a physical-mechanism label."""

    parameter: str
    operation: str
    magnitude: float
    description: str = ""

    def apply(self, params: dict[str, Any], *, severity: float = 1.0, factor: float = 1.0) -> dict[str, Any]:
        out = copy_params(params)
        current = out.get(self.parameter, 0.0)
        mag = self.magnitude * severity * factor
        op = self.operation
        if op == "multiply":
            out[self.parameter] = current * max(0.0, mag)
        elif op == "add":
            out[self.parameter] = current + mag
        elif op == "override":
            out[self.parameter] = self.magnitude
        elif op == "clamp_max":
            out[self.parameter] = min(current, mag)
        elif op == "clamp_min":
            out[self.parameter] = max(current, mag)
        elif op == "add_loss":
            out[self.parameter] = current - abs(mag)
        elif op == "degrade_multiply":
            # magnitude is maximum fractional loss at factor=1.
            out[self.parameter] = current * max(0.0, 1.0 - abs(self.magnitude) * severity * factor)
        elif op == "grow_multiply":
            # magnitude is maximum fractional growth at factor=1.
            out[self.parameter] = current * (1.0 + abs(self.magnitude) * severity * factor)
        elif op == "grow_add":
            out[self.parameter] = current + abs(self.magnitude) * severity * factor
        else:
            raise ValueError(f"Unsupported ParameterEffect operation: {op}")
        return out


@dataclass
class TimeWindowFault:
    """Base class for component faults active over a finite time window."""

    start_s: float = 120.0
    end_s: float = 300.0
    severity: float = 1.0
    target_id: str = ""
    name: str = "TimeWindowFault"
    fault_type: str = "generic_fault"
    physical_mechanism: str = "time-window parameterized component fault"
    effects: Sequence[ParameterEffect] = field(default_factory=tuple)
    composition: str = "fault_after_degradation"

    def active(self, t_s: float) -> bool:
        return self.start_s <= float(t_s) <= self.end_s

    def apply(self, params: Mapping[str, Any], t_s: float, context: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        out = copy_params(params)
        if not self.active(t_s):
            return out, {
                "name": self.name,
                "fault_type": self.fault_type,
                "active": False,
                "start_s": self.start_s,
                "end_s": self.end_s,
            }
        before = copy_params(out)
        applied = []
        for effect in self.effects:
            out = effect.apply(out, severity=self.severity, factor=1.0)
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
            "fault_type": self.fault_type,
            "active": True,
            "start_s": self.start_s,
            "end_s": self.end_s,
            "severity": self.severity,
            "physical_mechanism": self.physical_mechanism,
            "composition": self.composition,
            "effects": applied,
        }
