"""Test-only Basilisk guidance routing for isolated ADCS recovery trials.

This changes the *actual* C attTrackingError subscriber between genuine
Basilisk inertial3D and hillPoint output messages. It does not restore a
general flight mode manager, task activations, or production TC state.
"""
from __future__ import annotations

from typing import Any

from .native_mode_checkpoint import validate_mode_checkpoint

GUIDANCE_SOURCE = {
    "inertial3d": "attitude_reference",
    "nadir": "hill_guidance",
}


def _vector(message: Any) -> tuple[float, ...]:
    return tuple(float(x) for x in message.read().sigma_RN)


def switch_native_guidance(graph: dict[str, Any], mode: str) -> None:
    """Switch native C message subscription, failing on missing source/output."""
    if mode not in GUIDANCE_SOURCE:
        raise ValueError("ADCS_NATIVE_GUIDANCE_UNSUPPORTED_MODE")
    desired = graph[GUIDANCE_SOURCE[mode]].attRefOutMsg
    actual = graph["attitude_error"].attRefInMsg
    actual.subscribeTo(desired)
    if not actual.isLinked() or _vector(actual) != _vector(desired):
        raise ValueError("ADCS_NATIVE_GUIDANCE_SUBSCRIPTION_FAILED")


def restore_native_guidance_route_fresh(
    graph: dict[str, Any], document: dict[str, Any],
) -> None:
    """Reapply selected route after fresh graph init, before first resumed tick.

    Journal digest/time validation does not imply physical mode completeness.
    """
    data = validate_mode_checkpoint(document)
    if data["current_mode"] not in GUIDANCE_SOURCE:
        raise ValueError("ADCS_NATIVE_GUIDANCE_UNSUPPORTED_MODE")
    if graph["sim"].TotalSim.CurrentNanos != 0:
        raise ValueError("ADCS_NATIVE_GUIDANCE_REQUIRES_FRESH_GRAPH")
    switch_native_guidance(graph, data["current_mode"])
