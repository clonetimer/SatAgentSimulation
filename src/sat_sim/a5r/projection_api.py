"""Generated projection schemas for A5R subsystem model assets."""
from __future__ import annotations

from sat_sim_model_assets.subsystem_verticals import (
    SUBSYSTEM_BUNDLES,
    bundle_for_capability_id,
    parameter_contracts,
)


def projection_schema_bundle(capability_id: str) -> dict[str, object]:
    bundle = bundle_for_capability_id(capability_id)
    contracts = parameter_contracts(bundle)
    return {
        "schema_version": "sat-sim.a5r-projection-bundle.v1",
        "capability_id": capability_id,
        "projection_id": str(bundle.projection.projection_id),
        "projection_sha256": bundle.projection.content_sha256,
        "form_schema": bundle.projection.form_schema(contracts),
        "agent_tool_schema": bundle.projection.agent_tool_schema(contracts),
        "api_schema": bundle.projection.api_schema(contracts),
    }


def all_projection_schema_bundles() -> dict[str, dict[str, object]]:
    return {bundle.capability_id: projection_schema_bundle(bundle.capability_id) for bundle in SUBSYSTEM_BUNDLES}


__all__ = ["all_projection_schema_bundles", "projection_schema_bundle"]
