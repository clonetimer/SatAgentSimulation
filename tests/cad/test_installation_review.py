from __future__ import annotations

import copy

import pytest

from sat_sim.cad_installation_review import (
    SCHEMA_VERSION,
    build_installation_review_packets,
    validate_rigid_transform,
)


IDENTITY = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
]


def _translated(z):
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, z],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _candidate_payload():
    return {
        "schema_version": "cad-engineering-candidate-mapping.v1",
        "source": {"cad_model": "BIRDS4_FM_Final v7.step", "cad_revision": "3bab47a"},
        "candidates": [{
            "candidate_id": "candidate-battery",
            "engineering_object_id": "component.battery",
            "engineering_object_name": "蓄电池",
            "cad_source_model": "BIRDS4_FM_Final v7.step",
            "cad_source_revision": "3bab47a",
            "cad_node_path": "root/B4_battery v1",
            "variant_tags": ["fm"],
            "source_model_identity_review_required": True,
        }],
    }


def _assembly(local=None, world=None):
    return {"nodes": [
        {
            "source_node_path": "root", "source_node_name": "root", "source_label_name": "root",
            "parent_node_path": None, "duplicate_occurrence": 1, "direct_shape_count": 0,
            "child_count": 1, "local_transform_m": IDENTITY, "world_transform_m": IDENTITY,
        },
        {
            "source_node_path": "root/B4_battery v1", "source_node_name": "B4_battery v1",
            "source_label_name": "B4_battery v1", "parent_node_path": "root",
            "duplicate_occurrence": 1, "direct_shape_count": 0, "child_count": 2,
            "local_transform_m": local or _translated(0.02),
            "world_transform_m": world or _translated(0.02),
        },
    ]}


def _manifest():
    return {
        "source_commit": "3bab47a0f53410ef79005d21321ff659726f0a1c",
        "source_tree_sha": "0b84617fd98db32b5d997dc24585da95d5bd2b55",
        "source_repository": "https://github.com/BIRDSOpenSource/BIRDS4-CAD",
        "assembly_procedure": {
            "repository": "https://github.com/BIRDSOpenSource/BIRDS4-ProceduresAndReports",
            "path": "Procedures/BIRDS4-FM Assembly Procedure.docx",
            "source_commit": "d7eadd98f5327e1ddea82b640293d49bfdb09555",
            "git_blob_sha1": "4236578c000b413773399d91e094b57a20a52e28",
            "size_bytes": 61750445,
        },
        "coordinate_reference": {
            "title": "BIRDS Newsletter Issue No.49",
            "url": "https://birds1.birds-project.com/files/BIRDS_Newsletter_Issue_No.49.pdf",
            "page": 158,
            "role": "axis/datum orientation reference only",
        },
    }


def test_rigid_transform_checks_proper_rotation_and_homogeneous_row():
    report = validate_rigid_transform(_translated(0.03), "test")
    assert report["valid_rigid_transform"] is True
    assert report["translation_m"] == [0.0, 0.0, 0.03]
    assert report["interpretation"].startswith("CAD source transform only")


def test_reflection_is_not_accepted_as_proper_rotation():
    reflection = copy.deepcopy(IDENTITY)
    reflection[0][0] = -1.0
    with pytest.raises(ValueError, match="NOT_PROPER_ROTATION"):
        validate_rigid_transform(reflection, "reflection")


def test_non_orthogonal_rotation_is_rejected():
    invalid = copy.deepcopy(IDENTITY)
    invalid[0][1] = 0.2
    with pytest.raises(ValueError, match="NON_ORTHOGONAL_ROTATION"):
        validate_rigid_transform(invalid, "skew")


def test_invalid_homogeneous_row_is_rejected():
    invalid = copy.deepcopy(IDENTITY)
    invalid[3][0] = 1.0
    with pytest.raises(ValueError, match="INVALID_HOMOGENEOUS_LAST_ROW"):
        validate_rigid_transform(invalid, "bad-row")


def test_review_packet_preserves_cad_transform_but_never_promotes_installation():
    report = build_installation_review_packets(_candidate_payload(), _assembly(), _manifest())
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["summary"]["review_packet_count"] == 1
    assert report["summary"]["cad_local_transform_valid_count"] == 1
    assert report["summary"]["cad_world_transform_valid_count"] == 1
    packet = report["review_packets"][0]
    assert packet["cad_geometry_transform"]["local_transform_m"] == _translated(0.02)
    assert packet["cad_geometry_transform"]["parent_composition_check"] == "pass"
    assert packet["cad_geometry_transform"]["coordinate_frame_identity"] == "unverified"
    assert packet["cad_geometry_transform"]["datum_origin"] == "unverified"
    assert packet["flight_unit_identity"]["target_flight_unit"] is None
    assert packet["installation_evidence"]["assembly_procedure"] == "pinned_and_extracted_but_not_human_reviewed"
    assert packet["procedure_text_evidence_locators"]["status"] == "raw_text_source_locators_only_not_claim_approval"
    assert packet["procedure_text_evidence_locators"]["references"]["battery"][0]["table_index"] == 3
    assert packet["procedure_text_evidence_locators"]["references"]["structural testing model"][0]["paragraph_index"] == 21
    assert packet["installation_evidence"]["signed_installation_transform"] is None
    assert packet["promotion"]["status"] == "blocked_evidence_incomplete"
    assert packet["promotion"]["instance_id"] is None
    assert packet["promotion"]["installation_transform"] is None
    assert packet["promotion"]["spatial_anchor"] is None
    assert packet["promotion"]["render_eligible"] is False
    assert report["semantics"]["cad_transform_is_not_installation_transform"] is True


def test_parent_world_and_child_local_must_compose_to_declared_child_world():
    wrong_world = _translated(0.05)
    with pytest.raises(ValueError, match="PARENT_TRANSFORM_COMPOSITION_MISMATCH"):
        build_installation_review_packets(_candidate_payload(), _assembly(world=wrong_world), _manifest())


def test_missing_candidate_node_fails_closed():
    with pytest.raises(ValueError, match="CANDIDATE_NODE_NOT_FOUND"):
        build_installation_review_packets(
            _candidate_payload(),
            {"nodes": [{"source_node_path": "other", "local_transform_m": IDENTITY, "world_transform_m": IDENTITY}]},
            _manifest(),
        )


def test_procedure_source_is_pinned_but_not_misrepresented_as_reviewed():
    extraction = {
        "source": {
            "sha256": "abc123",
            "verification_status": "source_bytes_verified",
            "source_commit": _manifest()["assembly_procedure"]["source_commit"],
            "git_blob_sha1": _manifest()["assembly_procedure"]["git_blob_sha1"],
            "size_bytes": _manifest()["assembly_procedure"]["size_bytes"],
        },
        "extraction": {"paragraph_count": 42, "table_count": 8, "human_review_status": "not_reviewed"},
        "keyword_index": {
            "battery": [{"kind": "table_cell", "table_index": 3, "row_index": 4, "cell_index": 3, "text": "Battery"}],
            "structural testing model": [{"kind": "paragraph", "paragraph_index": 21, "text": "Structural Testing Model"}],
            "axis definition": [{"kind": "paragraph", "paragraph_index": 39, "text": "Axis Definition"}],
        },
    }
    report = build_installation_review_packets(_candidate_payload(), _assembly(), _manifest(), extraction)
    source = report["evidence_catalog"]["assembly_procedure_source"]
    assert source["source_commit"] == _manifest()["assembly_procedure"]["source_commit"]
    assert source["git_blob_sha1"] == _manifest()["assembly_procedure"]["git_blob_sha1"]
    assert source["extracted_source_sha256"] == "abc123"
    assert source["extracted_paragraph_count"] == 42
    assert source["extracted_table_count"] == 8
    assert source["source_status"] == "retrieved_and_hash_verified_raw_text_not_human_reviewed"
    packet = report["review_packets"][0]
    assert packet["installation_evidence"]["assembly_procedure"] == "pinned_and_extracted_but_not_human_reviewed"
    assert packet["procedure_text_evidence_locators"]["status"] == "raw_text_source_locators_only_not_claim_approval"
    assert packet["procedure_text_evidence_locators"]["references"]["battery"][0]["table_index"] == 3
    assert packet["procedure_text_evidence_locators"]["references"]["structural testing model"][0]["paragraph_index"] == 21
    assert report["evidence_catalog"]["coordinate_reference"]["source_status"] == "orientation_reference_only_not_complete_transform_evidence"


def test_procedure_extraction_receipt_must_match_pinned_manifest():
    extraction = {
        "source": {
            "verification_status": "source_bytes_verified",
            "source_commit": _manifest()["assembly_procedure"]["source_commit"],
            "git_blob_sha1": "wrong-hash",
            "size_bytes": _manifest()["assembly_procedure"]["size_bytes"],
        },
        "extraction": {},
    }
    with pytest.raises(ValueError, match="PROCEDURE_GIT_BLOB_MISMATCH"):
        build_installation_review_packets(_candidate_payload(), _assembly(), _manifest(), extraction)
