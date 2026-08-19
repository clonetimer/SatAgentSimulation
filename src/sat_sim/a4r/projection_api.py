"""Generated Agent, form and API schemas for the A4R composite projection."""
from __future__ import annotations

from typing import Any

from sat_sim_model_assets.composite_digital_twin import COMPOSITE_PROJECTION, parameter_contracts


def projection_schema_bundle() -> dict[str, Any]:
    contracts = parameter_contracts()
    return {
        "projection_id": str(COMPOSITE_PROJECTION.projection_id),
        "projection_sha256": COMPOSITE_PROJECTION.content_sha256,
        "form_schema": COMPOSITE_PROJECTION.form_schema(contracts),
        "agent_tool_schema": COMPOSITE_PROJECTION.agent_tool_schema(contracts),
        "api_schema": COMPOSITE_PROJECTION.api_schema(contracts),
    }


__all__ = ["projection_schema_bundle"]
