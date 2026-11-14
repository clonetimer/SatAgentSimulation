from __future__ import annotations

import json
from pathlib import Path

import pytest

from sat_sim.workbench_spatial_assets import (
    SCHEMA_VERSION,
    validate_spatial_asset_registry,
)


def _registry():
    digest = "a" * 64
    return {
        "schema_version": SCHEMA_VERSION,
        "assets": [{
            "asset_id": "rw-model",
            "asset_version": "v1",
            "asset_format": "glb",
            "path": "assets/models/rw.glb",
            "sha256": digest,
            "size_bytes": 1024,
            "length_unit": "m",
            "up_axis": "Y",
            "forward_axis": "X",
            "origin_semantics": "mechanical mounting datum",
            "source_evidence": {
                "document_id": "CAD-ADCS-001",
                "revision": "C",
                "locator": "assembly/RW1",
            },
            "review_status": "verified",
        }],
        "instances": [{
            "instance_id": "rw-1",
            "object_id": "component.reaction_wheel",
            "asset_ref": {"asset_id": "rw-model", "asset_version": "v1", "sha256": digest},
            "parent_instance_id": None,
            "mount_frame_id": "spacecraft.body",
            "translation_m": [0.12, -0.08, 0.03],
            "rotation_q_wxyz": [1.0, 0.0, 0.0, 0.0],
            "source_evidence": {
                "document_id": "CAD-ADCS-001",
                "revision": "C",
                "locator": "assembly/RW1/placement",
            },
            "verification_status": "verified",
        }],
    }


def _codes(result):
    return {item["code"] for item in result["errors"]}


def test_empty_registry_is_valid_and_does_not_invent_equipment_instances():
    result = validate_spatial_asset_registry({
        "schema_version": SCHEMA_VERSION, "assets": [], "instances": [],
    })
    assert result["ok"] is True
    assert result["asset_count"] == 0
    assert result["instance_count"] == 0
    assert result["renderable_instances"] == []
    assert result["semantics"]["capability_categories_are_inventory"] is False


def test_verified_asset_and_instance_are_renderable_only_with_equipment_object():
    object_tree = {
        "roots": [{
            "object_id": "whole_spacecraft", "kind": "whole_spacecraft",
            "children": [{
                "object_id": "component.reaction_wheel", "kind": "equipment",
                "instance_semantics": "capability_model_not_vehicle_inventory",
                "inventory_count": None,
            }],
        }]
    }
    result = validate_spatial_asset_registry(_registry(), object_tree=object_tree)
    assert result["ok"] is True
    assert len(result["renderable_instances"]) == 1
    assert result["renderable_instances"][0]["instance_id"] == "rw-1"


@pytest.mark.parametrize("path", [
    "../outside.glb", "/absolute/model.glb", "C:/models/rw.glb",
    "https://example.invalid/rw.glb", "assets\\rw.glb",
])
def test_asset_path_must_be_repository_relative_and_non_traversing(path):
    registry = _registry()
    registry["assets"][0]["path"] = path
    result = validate_spatial_asset_registry(registry)
    assert result["ok"] is False
    assert "ASSET_PATH_UNSAFE" in _codes(result)
    assert result["renderable_instances"] == []


@pytest.mark.parametrize("quaternion", [
    [2.0, 0.0, 0.0, 0.0],
    [float("nan"), 0.0, 0.0, 0.0],
    [1.0, 0.0, 0.0],
])
def test_invalid_instance_rotation_fails_closed(quaternion):
    registry = _registry()
    registry["instances"][0]["rotation_q_wxyz"] = quaternion
    result = validate_spatial_asset_registry(registry)
    assert result["ok"] is False
    assert {"INSTANCE_QUATERNION_INVALID", "INSTANCE_QUATERNION_NOT_UNIT"} & _codes(result)
    assert result["renderable_instances"] == []


def test_verified_instance_requires_verified_asset_and_matching_digest():
    registry = _registry()
    registry["assets"][0]["review_status"] = "reviewed"
    registry["instances"][0]["asset_ref"]["sha256"] = "b" * 64
    result = validate_spatial_asset_registry(registry)
    assert result["ok"] is False
    assert {"INSTANCE_ASSET_HASH_MISMATCH", "VERIFIED_INSTANCE_ASSET_NOT_VERIFIED"} <= _codes(result)
    assert result["renderable_instances"] == []


def test_instance_parent_must_exist_and_parent_graph_must_be_acyclic():
    registry = _registry()
    child = dict(registry["instances"][0])
    child["instance_id"] = "rw-2"
    child["parent_instance_id"] = "rw-1"
    registry["instances"].append(child)
    result = validate_spatial_asset_registry(registry)
    assert result["ok"] is True

    registry["instances"][0]["parent_instance_id"] = "rw-2"
    result = validate_spatial_asset_registry(registry)
    assert result["ok"] is False
    assert "INSTANCE_PARENT_CYCLE" in _codes(result)
    assert result["renderable_instances"] == []


def test_object_tree_category_without_equipment_instance_is_not_renderable():
    object_tree = {"roots": [{
        "object_id": "whole_spacecraft", "kind": "whole_spacecraft",
        "children": [{
            "object_id": "component.reaction_wheel", "kind": "component",
            "inventory_count": None,
        }],
    }]}
    result = validate_spatial_asset_registry(_registry(), object_tree=object_tree)
    assert result["ok"] is False
    assert "INSTANCE_OBJECT_NOT_EQUIPMENT" in _codes(result)


def test_json_schema_is_strict_and_accepts_empty_current_main_registry():
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "src/sat_sim/schemas/spatial_asset_registry.schema.json").read_text())
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate({
        "schema_version": SCHEMA_VERSION, "assets": [], "instances": [],
    })
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate({
            "schema_version": SCHEMA_VERSION, "assets": [], "instances": [],
            "unreviewed_model": True,
        })
