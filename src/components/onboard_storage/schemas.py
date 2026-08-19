"""Schema contracts for the onboard storage component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class OnboardStorageConfig:
    capacity_bits: float = 20_000_000.0
    high_watermark: float = 0.9
