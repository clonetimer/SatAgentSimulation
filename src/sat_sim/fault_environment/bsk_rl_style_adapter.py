"""A small BSK-RL-style adapter surface without a Gym/RL dependency."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from sat_sim.bsk_engine.types import BSKEventSpec

from .fault_episode import build_fault_episodes


@dataclass(frozen=True)
class FaultEnvironmentSnapshot:
    schema_version: str
    style: str
    episode_count: int
    categories: dict[str, int]
    episodes: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BSKRLStyleFaultAdapter:
    """Builds episode evidence while preserving the project TaskSpec interface."""

    schema_version = "fault-environment.v1"
    style = "bsk_rl_style_no_gym_dependency"

    def summarize(self, events: Sequence[BSKEventSpec], trace_rows: Sequence[Mapping[str, Any]]) -> FaultEnvironmentSnapshot:
        episodes = build_fault_episodes(events, trace_rows)
        counts = {"fault": 0, "degradation": 0, "constraint": 0}
        for episode in episodes:
            counts[episode.category] = counts.get(episode.category, 0) + 1
        return FaultEnvironmentSnapshot(
            schema_version=self.schema_version,
            style=self.style,
            episode_count=len(episodes),
            categories=counts,
            episodes=tuple(e.to_dict() for e in episodes),
        )


__all__ = ["BSKRLStyleFaultAdapter", "FaultEnvironmentSnapshot"]
