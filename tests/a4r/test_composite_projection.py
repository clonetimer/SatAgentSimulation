from __future__ import annotations

from pathlib import Path

import yaml

from sat_sim.a4r.projection_api import projection_schema_bundle
from sat_sim_model_assets.composite_digital_twin import (
    COMPOSITE_ADAPTER_KEY,
    COMPOSITE_PROJECTION,
    EFFECT_IDS,
    parameter_contracts,
)


CAPABILITY_PATH = Path("src/sat_sim/capabilities/whole_spacecraft.composite_digital_twin.v1.yaml")
FORBIDDEN_INPUT_KEYS = {"adapter", "adapter_key", "class_path", "implementation_id", "model_ref", "runner"}


def _capability() -> dict:
    return yaml.safe_load(CAPABILITY_PATH.read_text(encoding="utf-8"))


def test_projection_generates_composite_form_agent_and_api_schemas() -> None:
    bundle = projection_schema_bundle()

    assert bundle["projection_sha256"] == COMPOSITE_PROJECTION.content_sha256
    assert bundle["form_schema"]["x-projection-id"] == str(COMPOSITE_PROJECTION.projection_id)
    assert bundle["api_schema"]["adapter_key"] == COMPOSITE_ADAPTER_KEY
    assert "battery_capacity_wh" in bundle["form_schema"]["properties"]
    assert "spacecraft.composite_digital_twin.payload_instrument_off" in bundle["agent_tool_schema"]["effects"]
    assert "whole-spacecraft composite digital-twin" in bundle["agent_tool_schema"]["description"]
    assert "attitude-control object" not in bundle["agent_tool_schema"]["description"]
    assert set(COMPOSITE_PROJECTION.parameter_alias_map().values()) <= set(parameter_contracts())


def test_projection_parameter_drift_against_capability_yaml_is_zero() -> None:
    capability_parameters = set((_capability().get("parameters") or {}).keys())
    bundle = projection_schema_bundle()
    projected_parameters = {
        str(payload["x-model-source"])
        for payload in bundle["form_schema"]["properties"].values()
    }

    assert projected_parameters == capability_parameters


def test_projection_effect_drift_against_capability_yaml_is_zero() -> None:
    capability_effects = {
        str(item["effect_id"])
        for item in (_capability().get("operator") or {}).get("effects", ())
    }
    projected_effects = {
        item.rsplit(".", 1)[-1]
        for item in projection_schema_bundle()["agent_tool_schema"]["effects"]
    }

    assert projected_effects == set(EFFECT_IDS)
    assert projected_effects == capability_effects


def test_form_and_request_body_do_not_expose_execution_route_inputs() -> None:
    bundle = projection_schema_bundle()
    form_properties = set(bundle["form_schema"]["properties"])
    request_properties = set(bundle["api_schema"]["request_body"]["properties"])
    agent_input_properties = set(bundle["agent_tool_schema"]["input_schema"]["properties"])

    assert not (form_properties & FORBIDDEN_INPUT_KEYS)
    assert not (request_properties & FORBIDDEN_INPUT_KEYS)
    assert not (agent_input_properties & FORBIDDEN_INPUT_KEYS)
    assert "class_path" not in str(bundle["agent_tool_schema"])
