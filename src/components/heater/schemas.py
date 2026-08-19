"""Schema contracts for the heater component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class HeaterConfig:
    max_power_w: float = 25.0
    setpoint_k: float = 285.0
    hysteresis_k: float = 2.0
