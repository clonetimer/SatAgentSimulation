"""Schema contracts for the pdu component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class PduConfig:
    bus_max_w: float = 100.0
    shed_order: tuple = ()


@dataclass(frozen=True)
class PduLoadSheddingConfig:
    """SOC thresholds for the Basilisk-scheduled PDU policy."""

    payload_min_soc: float = 0.55
    comm_min_soc: float = 0.50
    heater_min_soc: float = 0.30
    adcs_min_soc: float = 0.20
    recovery_soc: float = 0.65
    fallback_initial_soc: float = 0.75
