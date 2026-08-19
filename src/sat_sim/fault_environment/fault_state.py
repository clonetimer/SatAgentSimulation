"""Fault state machine primitives."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sat_sim.bsk_engine.types import BSKEventSpec

from .fault_catalog import get_fault_contract


@dataclass(frozen=True)
class FaultState:
    event_id: str
    category: str
    effect: str
    target: str
    start_s: float
    end_s: float | None
    status: str
    display_name_zh: str
    target_kind: str
    parameters: dict[str, Any]

    def active_at(self, time_s: float) -> bool:
        if time_s < self.start_s:
            return False
        return self.end_s is None or time_s <= self.end_s

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def state_from_event(event: BSKEventSpec) -> FaultState:
    contract = get_fault_contract(event.effect, event.category)
    return FaultState(
        event_id=event.event_id,
        category=event.category,
        effect=event.effect,
        target=event.target,
        start_s=event.start_s,
        end_s=event.end_s,
        status="scheduled",
        display_name_zh=contract.display_name_zh,
        target_kind=contract.target_kind,
        parameters=dict(event.parameters),
    )


__all__ = ["FaultState", "state_from_event"]
