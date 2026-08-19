"""Schema contracts for the power sink component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class PowerSinkConfig:
    name: str = 'load'
    base_w: float = 0.0
    mode_power_w: dict | None = None
