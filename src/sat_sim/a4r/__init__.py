"""A4R composite digital-twin model-asset resolution.

Execution adapters are loaded lazily so model-asset resolution does not require
Basilisk at import time.
"""
from __future__ import annotations


def __getattr__(name: str):  # noqa: ANN001
    if name == "CompositeDigitalTwinGraphExecutionAdapter":
        from .adapter import CompositeDigitalTwinGraphExecutionAdapter
        return CompositeDigitalTwinGraphExecutionAdapter
    if name == "projection_schema_bundle":
        from .projection_api import projection_schema_bundle
        return projection_schema_bundle
    if name in {"A4Resolution", "is_a4_capability_metadata", "resolve_composite_digital_twin_execution"}:
        from .resolution import A4Resolution, is_a4_capability_metadata, resolve_composite_digital_twin_execution
        return {
            "A4Resolution": A4Resolution,
            "is_a4_capability_metadata": is_a4_capability_metadata,
            "resolve_composite_digital_twin_execution": resolve_composite_digital_twin_execution,
        }[name]
    raise AttributeError(name)


__all__ = [
    "A4Resolution",
    "CompositeDigitalTwinGraphExecutionAdapter",
    "is_a4_capability_metadata",
    "projection_schema_bundle",
    "resolve_composite_digital_twin_execution",
]
