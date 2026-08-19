"""Generated Agent, form and API schemas for the A3R capability projection."""
from __future__ import annotations

from typing import Any

from sat_sim_model_assets.attitude_control import ATTITUDE_CONTROL_PROJECTION, parameter_contracts


def projection_schema_bundle() -> dict[str, Any]:
    contracts = parameter_contracts()
    return {
        "projection_id": str(ATTITUDE_CONTROL_PROJECTION.projection_id),
        "projection_sha256": ATTITUDE_CONTROL_PROJECTION.content_sha256,
        "form_schema": ATTITUDE_CONTROL_PROJECTION.form_schema(contracts),
        "agent_tool_schema": ATTITUDE_CONTROL_PROJECTION.agent_tool_schema(contracts),
        "api_schema": ATTITUDE_CONTROL_PROJECTION.api_schema(contracts),
    }


__all__ = ["projection_schema_bundle"]
