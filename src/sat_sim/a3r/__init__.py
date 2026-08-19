"""A3R double-model vertical slice.

The package initializer intentionally avoids importing the Basilisk execution
adapter.  Resolution and projection contracts must remain usable in environments
where the optional Basilisk runtime is not installed.
"""
from __future__ import annotations

A3R_VERSION = "sat-sim.a3r.v1"


def __getattr__(name: str):  # noqa: ANN001
    if name == "AttitudeControlGraphExecutionAdapter":
        from .adapter import AttitudeControlGraphExecutionAdapter
        return AttitudeControlGraphExecutionAdapter
    if name == "projection_schema_bundle":
        from .projection_api import projection_schema_bundle
        return projection_schema_bundle
    if name in {"A3Resolution", "resolve_attitude_control_execution"}:
        from .resolution import A3Resolution, resolve_attitude_control_execution
        return {"A3Resolution": A3Resolution, "resolve_attitude_control_execution": resolve_attitude_control_execution}[name]
    raise AttributeError(name)


__all__ = [
    "A3R_VERSION",
    "A3Resolution",
    "AttitudeControlGraphExecutionAdapter",
    "projection_schema_bundle",
    "resolve_attitude_control_execution",
]
