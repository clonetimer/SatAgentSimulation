"""EPS power-budget helper functions."""
from __future__ import annotations

from collections.abc import Mapping

from components.power_sink.builder import PowerSinkConfig, demand_w


def mode_load_requests(loads: Mapping[str, PowerSinkConfig], mode: str) -> dict[str, float]:
    """Return requested load power by component name for a mode."""

    return {name: max(0.0, float(demand_w(cfg, mode=mode, enabled=True))) for name, cfg in loads.items()}


def merge_requested_loads(base: Mapping[str, float], overrides: Mapping[str, float] | None = None) -> dict[str, float]:
    """Merge mode-derived loads and explicit requested loads.

    Explicit requested loads replace same-name mode loads, and new load names are
    allowed.  Negative loads are clamped to zero because EPS load demand is a
    non-negative consumption request.
    """

    merged = {str(k): max(0.0, float(v)) for k, v in base.items()}
    for key, value in (overrides or {}).items():
        merged[str(key)] = max(0.0, float(value))
    return merged


def served_loads_after_shed(requested: Mapping[str, float], shed_loads: tuple[str, ...]) -> dict[str, float]:
    """Return served-load dictionary after named loads are shed."""

    shed = set(shed_loads)
    return {name: (0.0 if name in shed else max(0.0, float(value))) for name, value in requested.items()}


def total_power(loads_w: Mapping[str, float]) -> float:
    return sum(max(0.0, float(v)) for v in loads_w.values())
