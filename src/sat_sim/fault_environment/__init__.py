"""BSK-RL-style fault environment utilities for sat-sim.

This package intentionally avoids importing Gymnasium or BSK-RL.  Heavy objects
that depend on the BSKSim engine are imported lazily so API/workbench catalog
queries do not create circular imports with ``sat_sim.bsk_engine``.
"""
from __future__ import annotations

from .fault_catalog import get_fault_contract, list_fault_contracts
from .fault_effect_contract import FaultEffectContract

__all__ = [
    "BSKRLStyleFaultAdapter",
    "FaultEnvironmentSnapshot",
    "FaultEffectContract",
    "FaultEpisode",
    "FaultState",
    "build_fault_episode",
    "build_fault_episodes",
    "get_fault_contract",
    "list_fault_contracts",
    "state_from_event",
]


def __getattr__(name: str):
    if name in {"BSKRLStyleFaultAdapter", "FaultEnvironmentSnapshot"}:
        from .bsk_rl_style_adapter import BSKRLStyleFaultAdapter, FaultEnvironmentSnapshot
        return {"BSKRLStyleFaultAdapter": BSKRLStyleFaultAdapter, "FaultEnvironmentSnapshot": FaultEnvironmentSnapshot}[name]
    if name in {"FaultEpisode", "build_fault_episode", "build_fault_episodes"}:
        from .fault_episode import FaultEpisode, build_fault_episode, build_fault_episodes
        return {"FaultEpisode": FaultEpisode, "build_fault_episode": build_fault_episode, "build_fault_episodes": build_fault_episodes}[name]
    if name in {"FaultState", "state_from_event"}:
        from .fault_state import FaultState, state_from_event
        return {"FaultState": FaultState, "state_from_event": state_from_event}[name]
    raise AttributeError(name)
