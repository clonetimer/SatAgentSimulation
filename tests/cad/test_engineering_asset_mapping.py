from __future__ import annotations

import copy

import pytest

from sat_sim.engineering_asset_mapping import SCHEMA_VERSION, validate_mapping_bundle, validate_mapping_record


IDENTITY = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]


def _evidence():
    claims = [
        "flight_unit_identity", "hardware_identity", "assembly_relation",
        "coordinate_datum", "installation_transform",
    ]
    return [{
        "claim_type": claim,
        "source_sha256": f"sha256-{claim}",
        "source_locator": f"doc:{claim}:page-1",
        "reviewer": "independent-reviewer",
        "decision": "approved",
    } for claim in claims]


def _as_built():
    return {
        "mapping_id": "map-rw-001",
        "object_id": "component.reaction_wheel",
        "mapping_kind": "as_built_verified",
        "asset_instance_id": "birds4-maya2-rw-001",
        "geometry_asset": {
            "asset_id": "birds4-fm-asset",
            "sha256": "a" * 64,
            "source_node_path": "root/FM/RW/occurrence-1",
            "source_revision": "approved-drawing-rev-C",
        },
        "geometry_frame": {
            "identity_status": "approved",
            "frame_id": "MAYA2_BODY",
            "parent_frame_id": "MAYA2_BODY",
            "handedness": "right",
            "installation_transform_parent_from_child": IDENTITY,
        },
        "spatial_anchor": {"parent_instance_id": "birds4-maya2-spacecraft"},
        "applicability": {
            "spacecraft_model": "BIRDS-4",
            "flight_unit": "Maya-2",
            "hardware_part_number": "RW-001",
            "hardware_revision": "C",
        },
        "evidence": _evidence(),
        "render": {"as_built_eligible": True, "views": ["as_built"]},
    }


def _simulation_configuration():
    return {
        "mapping_id": "map-rw-sim",
        "object_id": "component.reaction_wheel",
        "mapping_kind": "simulation_configuration_geometry",
        "asset_instance_id": None,
        "geometry_asset": None,
        "geometry_frame": {
            "identity_status": "task_spec_configuration",
            "frame_id": "B",
            "source_parameter": "rWB_B",
        },
        "spatial_anchor": None,
        "simulation_binding": {"run_id": "run-123", "taskspec_sha256": "b" * 64},
        "telemetry_binding": {"binding_status": "run_scoped"},
        "render": {"as_built_eligible": False, "views": ["simulation_configuration"]},
    }


def _reference_cad():
    return {
        "mapping_id": "map-rw-reference",
        "object_id": "component.reaction_wheel",
        "mapping_kind": "reference_cad",
        "asset_instance_id": None,
        "geometry_asset": {
            "asset_id": "birds4-reference",
            "sha256": "c" * 64,
            "source_node_path": "root/B4_RW_Assembly",
        },
        "geometry_frame": {"identity_status": "unverified"},
        "spatial_anchor": None,
        "telemetry_binding": None,
        "render": {"as_built_eligible": False, "views": ["reference_cad"]},
    }


def _unlocated():
    return {
        "mapping_id": "map-imu-unlocated",
        "object_id": "component.imu",
        "mapping_kind": "unlocated",
        "asset_instance_id": None,
        "geometry_asset": None,
        "geometry_frame": None,
        "spatial_anchor": None,
        "telemetry_binding": None,
        "render": {"as_built_eligible": False, "renderable": False, "views": []},
    }


def test_all_four_mapping_kinds_remain_semantically_separate():
    report = validate_mapping_bundle({
        "schema_version": SCHEMA_VERSION,
        "mappings": [_as_built(), _simulation_configuration(), _reference_cad(), _unlocated()],
    })
    assert report["mapping_count"] == 4
    assert report["counts_by_kind"] == {
        "as_built_verified": 1,
        "reference_cad": 1,
        "simulation_configuration_geometry": 1,
        "unlocated": 1,
    }
    assert report["as_built_verified_count"] == 1
    assert report["render_eligible_as_built_count"] == 1
    assert report["semantics"]["reference_cad_is_not_as_built"] is True
    assert report["semantics"]["simulation_configuration_is_not_as_built"] is True


def test_as_built_mapping_requires_all_five_approved_claim_groups():
    record = _as_built()
    record["evidence"] = [row for row in record["evidence"] if row["claim_type"] != "coordinate_datum"]
    with pytest.raises(ValueError, match="AS_BUILT_EVIDENCE_INCOMPLETE"):
        validate_mapping_record(record)


def test_as_built_mapping_rejects_left_handed_or_non_rigid_transform():
    record = _as_built()
    record["geometry_frame"]["installation_transform_parent_from_child"] = copy.deepcopy(IDENTITY)
    record["geometry_frame"]["installation_transform_parent_from_child"][0][0] = -1.0
    with pytest.raises(ValueError, match="TRANSFORM_NOT_PROPER_RIGID"):
        validate_mapping_record(record)


def test_reference_cad_cannot_claim_asset_instance_spatial_anchor_or_telemetry():
    record = _reference_cad()
    record["telemetry_binding"] = {"binding_status": "approved"}
    with pytest.raises(ValueError, match="REFERENCE_CAD_CANNOT_CLAIM_INSTANCE_OR_TELEMETRY"):
        validate_mapping_record(record)


def test_simulation_configuration_geometry_cannot_claim_as_built_instance():
    record = _simulation_configuration()
    record["asset_instance_id"] = "fake-as-built"
    with pytest.raises(ValueError, match="SIMULATION_GEOMETRY_MUST_NOT_CLAIM_AS_BUILT_INSTANCE"):
        validate_mapping_record(record)


def test_unlocated_object_cannot_be_given_a_fake_transform_or_mesh():
    record = _unlocated()
    record["geometry_frame"] = {"identity_status": "unverified"}
    with pytest.raises(ValueError, match="UNLOCATED_OBJECT_HAS_GEOMETRY"):
        validate_mapping_record(record)


def test_duplicate_asset_instance_ids_are_rejected():
    first = _as_built()
    second = _as_built()
    second["mapping_id"] = "map-rw-002"
    with pytest.raises(ValueError, match="DUPLICATE_ASSET_INSTANCE_ID"):
        validate_mapping_bundle({"schema_version": SCHEMA_VERSION, "mappings": [first, second]})


def test_unknown_schema_and_kind_fail_closed():
    with pytest.raises(ValueError, match="UNSUPPORTED_SCHEMA_VERSION"):
        validate_mapping_bundle({"schema_version": "v0", "mappings": []})
    record = _reference_cad()
    record["mapping_kind"] = "verified_by_filename"
    with pytest.raises(ValueError, match="UNKNOWN_MAPPING_KIND"):
        validate_mapping_record(record)


def test_as_built_mapping_requires_named_parent_and_child_frames():
    record = _as_built()
    record["geometry_frame"]["frame_id"] = None
    with pytest.raises(ValueError, match="AS_BUILT_FRAME_APPROVAL_REQUIRED"):
        validate_mapping_record(record)


def test_simulation_geometry_requires_run_and_taskspec_identity():
    record = _simulation_configuration()
    record["simulation_binding"] = {"run_id": "run-123"}
    with pytest.raises(ValueError, match="SIMULATION_RUN_BINDING_REQUIRED"):
        validate_mapping_record(record)


def test_non_mapping_render_policy_fails_closed():
    record = _reference_cad()
    record["render"] = True
    with pytest.raises(ValueError, match="RENDER_POLICY_REQUIRED"):
        validate_mapping_record(record)
