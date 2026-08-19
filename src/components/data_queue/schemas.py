"""Schema contracts for the data queue component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class DataQueueConfig:
    capacity_bits: float = 100.0
