"""Fault/ degradation / constraint effect contracts.

The package is intentionally *BSK-RL-style* rather than a dependency on BSK-RL:
it borrows the episode/observation/effect-contract vocabulary while keeping the
project TaskSpec and Run Bundle contracts unchanged.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class FaultEffectContract:
    """Describes what an event is expected to do and how it should be observed."""

    effect: str
    category: str
    display_name_zh: str
    target_kind: str
    trigger_semantics: str
    expected_observables: tuple[str, ...] = ()
    evidence_policy: str = "state_and_observable_effect"
    parameter_hints: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["parameter_hints"] = dict(self.parameter_hints)
        return out


__all__ = ["FaultEffectContract"]
