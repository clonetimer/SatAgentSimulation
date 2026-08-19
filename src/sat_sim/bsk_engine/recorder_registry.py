"""Recorder declarations for BSKSim-style scenarios."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .types import BSKRecorderSpec

DEFAULT_FOUNDATION_RECORDERS = (
    BSKRecorderSpec("time_s", "sim.time", 1.0, "s", "仿真时间"),
    BSKRecorderSpec("orbit.theta_rad", "dynamics.orbit", 1.0, "rad", "轨道相位角"),
    BSKRecorderSpec("attitude.pointing_error_deg", "fsw.attitude_error", 1.0, "deg", "姿态指向误差"),
    BSKRecorderSpec("fsw.mode", "fsw.mode_request", 1.0, "", "飞控模式"),
)


def recorders_from_spec(spec: Mapping[str, Any], *, sample_s: float) -> tuple[BSKRecorderSpec, ...]:
    outputs = spec.get("outputs") if isinstance(spec.get("outputs"), Mapping) else {}
    requested = outputs.get("plots") if isinstance(outputs.get("plots"), Iterable) and not isinstance(outputs.get("plots"), (str, bytes)) else []
    fields = [str(item) for item in requested]
    recorders = list(DEFAULT_FOUNDATION_RECORDERS)
    for field in fields:
        if field not in {r.field for r in recorders}:
            recorders.append(BSKRecorderSpec(field=field, source="requested.output", sample_s=sample_s, description="TaskSpec 请求输出"))
    return tuple(BSKRecorderSpec(r.field, r.source, sample_s, r.unit, r.description) for r in recorders)
