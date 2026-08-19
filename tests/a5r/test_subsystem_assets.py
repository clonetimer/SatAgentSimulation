from __future__ import annotations

from pathlib import Path

import yaml

from sat_sim.a5r.projection_api import projection_schema_bundle
from sat_sim_model_assets.subsystem_verticals import (
    SUBSYSTEM_ASSET_REGISTRY,
    SUBSYSTEM_BUNDLES,
    parameter_contracts,
)


CAPABILITY_ROOT = Path("src/sat_sim/capabilities")


def _capability(capability_id: str) -> dict:
    return yaml.safe_load((CAPABILITY_ROOT / f"{capability_id}.yaml").read_text(encoding="utf-8"))


def test_a5r_subsystem_registry_validates_four_verticals() -> None:
    SUBSYSTEM_ASSET_REGISTRY.validate()
    inventory = SUBSYSTEM_ASSET_REGISTRY.inventory()
    assert len(SUBSYSTEM_BUNDLES) == 4
    for bundle in SUBSYSTEM_BUNDLES:
        assert inventory["definitions"][str(bundle.model_ref)] == bundle.root_definition.content_sha256
        assert inventory["graphs"][str(bundle.graph.graph_ref)] == bundle.graph.content_sha256
        assert inventory["parameter_sets"][str(bundle.parameter_set.parameter_set_id)] == bundle.parameter_set.content_sha256
        assert bundle.implementation_profile.adapter_key == bundle.adapter_key
        assert bundle.projection.adapter_key == bundle.adapter_key


def test_a5r_public_parameter_projection_has_zero_yaml_drift() -> None:
    for bundle in SUBSYSTEM_BUNDLES:
        capability = _capability(bundle.capability_id)
        public = set((capability.get("parameters") or {}).keys())
        projected = set(bundle.projection.parameter_alias_map().values())
        declared = {str(item.name) for item in bundle.root_definition.parameters}
        assert projected == public
        assert public <= declared
        assert set(bundle.parameter_set.values) == declared


def test_a5r_capability_yaml_routes_are_non_legacy_and_match_assets() -> None:
    for bundle in SUBSYSTEM_BUNDLES:
        capability = _capability(bundle.capability_id)
        execution = capability["execution"]
        projection = capability["model_projection"]
        assert execution["adapter_key"] == bundle.adapter_key
        assert execution["model_ref"] == str(bundle.model_ref)
        assert execution["implementation_id"] == str(bundle.implementation_id)
        assert execution["legacy_fallback_enabled"] is False
        assert projection["projection_id"] == str(bundle.projection.projection_id)
        assert projection["operations_object_ref"] == str(bundle.operations_object.object_ref)
        assert set(projection["exposed_properties"]) == {str(item.ref) for item in bundle.operations_object.properties}


def test_a5r_projection_schemas_do_not_expose_execution_internals() -> None:
    forbidden = {"adapter", "adapter_key", "class_path", "implementation_id", "model_ref", "runner"}
    for bundle in SUBSYSTEM_BUNDLES:
        payload = projection_schema_bundle(bundle.capability_id)
        properties = set(payload["form_schema"]["properties"])
        assert not (properties & forbidden)
        assert payload["api_schema"]["adapter_key"] == bundle.adapter_key
        assert payload["projection_sha256"] == bundle.projection.content_sha256
        assert set(bundle.projection.parameter_alias_map().values()) <= set(parameter_contracts(bundle))
