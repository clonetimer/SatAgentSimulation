from __future__ import annotations

from pathlib import Path

import yaml

from sat_sim_kernel import ModelKind
from sat_sim_model_assets.attitude_control import (
    ADCS_GRAPH,
    ASSET_REGISTRY,
    ATTITUDE_CONTROL_BINDINGS,
    ATTITUDE_CONTROL_OBJECT,
    ATTITUDE_CONTROL_PROJECTION,
    BASILISK_RW_PROFILE,
    BASILISK_SPACECRAFT_PROFILE,
    PARAMETER_SET,
    PYTHON_RW_PROFILE,
    REACTION_WHEEL_DEFINITION,
    SPACECRAFT_GRAPH,
    SPACECRAFT_INTERFACE_DEFINITION,
    parameter_contracts,
)
from sat_sim.a3r.projection_api import projection_schema_bundle


def test_asset_registry_validates_and_has_stable_inventory() -> None:
    ASSET_REGISTRY.validate()
    inventory = ASSET_REGISTRY.inventory()
    assert inventory["definitions"]["component.reaction_wheel@1"] == REACTION_WHEEL_DEFINITION.content_sha256
    assert inventory["graphs"]["subsystem.adcs@1"] == ADCS_GRAPH.content_sha256
    assert inventory["graphs"]["spacecraft.demo_satellite@1"] == SPACECRAFT_GRAPH.content_sha256
    assert inventory["parameter_sets"][str(PARAMETER_SET.parameter_set_id)] == PARAMETER_SET.content_sha256


def test_graphs_are_physical_composites_with_explicit_feedback() -> None:
    assert ADCS_GRAPH.kind is ModelKind.SUBSYSTEM
    assert SPACECRAFT_GRAPH.kind is ModelKind.SPACECRAFT
    assert any(edge.feedback for edge in ADCS_GRAPH.connections)
    assert any(edge.feedback for edge in SPACECRAFT_GRAPH.connections)
    assert all(edge.feedback for edge in ADCS_GRAPH.connections if edge.source.node_id.value == "body")


def test_parameter_values_are_separate_from_model_semantics() -> None:
    PARAMETER_SET.validate_against(SPACECRAFT_INTERFACE_DEFINITION)
    declared = {item.name.value for item in SPACECRAFT_INTERFACE_DEFINITION.parameters}
    assert set(PARAMETER_SET.values) <= declared
    assert PARAMETER_SET.source_refs
    assert PARAMETER_SET.confidence == "engineering_demo"


def test_python_and_basilisk_profiles_share_reaction_wheel_definition() -> None:
    assert BASILISK_RW_PROFILE.model_ref == REACTION_WHEEL_DEFINITION.model_ref
    assert PYTHON_RW_PROFILE.model_ref == REACTION_WHEEL_DEFINITION.model_ref
    assert BASILISK_RW_PROFILE.implementation_id != PYTHON_RW_PROFILE.implementation_id
    assert "rw_friction_degradation" in BASILISK_RW_PROFILE.supported_effects
    assert "rw_friction_degradation" not in PYTHON_RW_PROFILE.supported_effects
    assert BASILISK_SPACECRAFT_PROFILE.adapter_key == ATTITUDE_CONTROL_PROJECTION.adapter_key


def test_operations_object_and_four_binding_types_are_present() -> None:
    assert str(ATTITUDE_CONTROL_OBJECT.object_ref) == "spacecraft.attitude_control"
    assert len(ATTITUDE_CONTROL_BINDINGS.properties) == 2
    assert len(ATTITUDE_CONTROL_BINDINGS.actions) == 1
    assert len(ATTITUDE_CONTROL_BINDINGS.effects) == 1
    assert len(ATTITUDE_CONTROL_BINDINGS.evidence) == 1
    assert ATTITUDE_CONTROL_BINDINGS.content_sha256


def test_projection_generates_form_agent_and_api_schemas() -> None:
    bundle = projection_schema_bundle()
    assert bundle["projection_sha256"] == ATTITUDE_CONTROL_PROJECTION.content_sha256
    assert bundle["form_schema"]["x-projection-id"] == str(ATTITUDE_CONTROL_PROJECTION.projection_id)
    assert "initial_pointing_error_deg" in bundle["form_schema"]["properties"]
    assert "spacecraft.attitude_control.enter_safe_mode" in bundle["agent_tool_schema"]["actions"]
    assert bundle["api_schema"]["adapter_key"] == "basilisk.attitude_control_graph"
    assert set(ATTITUDE_CONTROL_PROJECTION.parameter_alias_map().values()) <= set(parameter_contracts())


def test_existing_capability_catalog_points_to_generated_projection_without_new_capability() -> None:
    path = Path("src/sat_sim/capabilities/whole_spacecraft.unified_native.v1.yaml")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    projection = payload["model_projection"]
    execution = payload["execution"]
    assert projection["projection_id"] == str(ATTITUDE_CONTROL_PROJECTION.projection_id)
    assert projection["model_ref"] == str(ATTITUDE_CONTROL_PROJECTION.model_ref)
    assert execution["adapter_key"] == ATTITUDE_CONTROL_PROJECTION.adapter_key
    assert execution["implementation_id"] == str(ATTITUDE_CONTROL_PROJECTION.implementation_id)
