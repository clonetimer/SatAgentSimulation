"""Synthetic fault-injection tests plus an opt-in real CI-artifact intake test."""
from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import json
import os
import struct
from pathlib import Path

import pytest


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _write_glb(path, nodes):
    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": nodes,
        "meshes": [{"primitives": [{"attributes": {}}]}],
    }
    body = json.dumps(document, separators=(",", ":")).encode("utf-8")
    body += b" " * (-len(body) % 4)
    path.write_bytes(struct.pack("<4sIII4s", b"glTF", 2, 20 + len(body), len(body), b"JSON") + body)




def _builder():
    name = "sat_sim.engineering_reference_registry"
    assert importlib.util.find_spec(name) is not None, "M4 real evidence intake is not implemented"
    return importlib.import_module(name).build_reference_registry


@pytest.fixture
def inputs(tmp_path):
    """Synthetic input is deliberately not a flight-unit evidence record."""
    commit, tree_sha = "a" * 40, "b" * 40
    glb = tmp_path / "fixture.glb"
    _write_glb(glb, [
        {"name": "root", "children": [1]},
        {"name": "battery", "mesh": 0},
    ])
    node = {"source_node_path": "root/battery", "parent_node_path": "root", "source_node_name": "battery", "child_count": 1}
    node_hash = _digest({"candidate_id": "battery-candidate", "source_commit": commit, "source_tree_sha": tree_sha, "node": node})
    review = {
        "schema_version": "cad-installation-evidence-review.v1",
        "source": {"source_commit": commit, "cad_revision": commit, "source_tree_sha": tree_sha, "cad_model": "fixture.step", "cad_node_count": 2, "candidate_count": 1},
        "summary": {"review_packet_count": 1},
        "review_packets": [{
            "review_packet_id": "packet-battery", "candidate_id": "battery-candidate",
            "engineering_object_id": "component.battery",
            "cad_source": {"model": "fixture.step", "revision": commit, "upstream_commit": commit, "source_tree_sha": tree_sha, "source_node_path": "root/battery", "parent_node_path": "root", "evidence_sha256": node_hash},
            "required_reviews": {k: "blocked" for k in ("flight_unit_identity", "hardware_identity", "assembly_relation", "coordinate_datum", "installation_transform")},
            "promotion": {"status": "blocked_evidence_incomplete", "instance_id": None, "installation_transform": None, "spatial_anchor": None, "render_eligible": False},
        }],
    }
    tree = {"schema_version": "workbench-object-tree.v2", "roots": [{"object_id": "whole_spacecraft", "kind": "whole_spacecraft", "children": [
        {"object_id": "component.battery", "kind": "equipment", "name_zh": "蓄电池", "children": []},
        {"object_id": "component.imu", "kind": "equipment", "name_zh": "惯性测量单元", "children": []},
        {"object_id": "external.station", "kind": "external_object", "children": []},
    ]}]}
    assembly = {"source_path": "fixture.step", "nodes": [{"source_node_path": "root", "parent_node_path": None, "source_node_name": "root"}, node]}
    conversion = {"source": {"commit": commit, "path": "fixture.step"}, "conversion": {"output_unit": "metres"}, "geometry": {"assembly_node_count": 2, "sha256": hashlib.sha256(glb.read_bytes()).hexdigest(), "size_bytes": glb.stat().st_size}, "promotion_status": "reference_cad_imported_not_as_built_verified"}
    return [review, tree, assembly, conversion, glb]


def test_registry_is_deterministic_read_only_and_never_promotes(inputs):
    before = copy.deepcopy(inputs[:4])
    first = _builder()(*inputs)
    assert first == _builder()(*inputs)
    assert inputs[:4] == before
    assert first["validation"]["counts_by_kind"] == {"reference_cad": 1, "unlocated": 1}
    assert first["validation"]["as_built_verified_count"] == 0
    assert first["summary"]["node_renderable_count"] == 0
    reference = next(r for r in first["mappings"] if r["mapping_kind"] == "reference_cad")
    assert reference["geometry_asset"]["sha256"] == inputs[3]["geometry"]["sha256"]
    assert reference["geometry_asset"]["node_path_space"] == "step_assembly_occurrence"
    assert reference["geometry_asset"]["glb_node_binding"] is None
    assert reference["geometry_frame"] == {"identity_status": "unverified"}
    assert reference["telemetry_binding"] is None
    assert reference["review"]["required_reviews"] == inputs[0]["review_packets"][0]["required_reviews"]
    assert all(r["asset_instance_id"] is None for r in first["mappings"])
    assert "external.station" not in {r["object_id"] for r in first["mappings"]}


@pytest.mark.parametrize("mutation,error", [
    ("source_commit", "SOURCE_IDENTITY"), ("unknown_object", "UNKNOWN_EQUIPMENT"),
    ("node_digest", "NODE_EVIDENCE"), ("node_path", "NODE_NOT_FOUND"),
    ("duplicate", "DUPLICATE_PACKET"), ("promoted", "PROMOTION_FORBIDDEN"),
    ("empty", "EMPTY_REVIEW"), ("missing_gate", "REVIEW_GATES"),
    ("parent", "PARENT_MISMATCH"), ("wrong_count", "NODE_COUNT"),
])
def test_review_mutations_fail_closed(inputs, mutation, error):
    review, _, _, _, _ = inputs
    packet = review["review_packets"][0]
    if mutation == "source_commit":
        review["source"]["source_commit"] = "c" * 40
    elif mutation == "unknown_object":
        packet["engineering_object_id"] = "external.station"
    elif mutation == "node_digest":
        packet["cad_source"]["evidence_sha256"] = "0" * 64
    elif mutation == "node_path":
        packet["cad_source"]["source_node_path"] = "root/absent"
    elif mutation == "duplicate":
        review["review_packets"].append(copy.deepcopy(packet))
        review["source"]["candidate_count"] = review["summary"]["review_packet_count"] = 2
    elif mutation == "promoted":
        packet["promotion"]["render_eligible"] = True
    elif mutation == "empty":
        review["review_packets"] = []
    elif mutation == "missing_gate":
        del packet["required_reviews"]["coordinate_datum"]
    elif mutation == "parent":
        packet["cad_source"]["parent_node_path"] = "different-parent"
    elif mutation == "wrong_count":
        review["source"]["cad_node_count"] = 99
    with pytest.raises(ValueError, match=error):
        _builder()(*inputs)


def test_modified_glb_bytes_are_not_accepted_as_verified_geometry(inputs):
    path = inputs[-1]
    path.write_bytes(path.read_bytes()[:-1] + b"x")
    with pytest.raises(ValueError, match="GLB_IDENTITY"):
        _builder()(*inputs)


def test_glb_identity_is_not_the_review_document_hash(inputs):
    inputs[3]["geometry"]["sha256"] = _digest(inputs[0])
    with pytest.raises(ValueError, match="GLB_IDENTITY"):
        _builder()(*inputs)


def test_cad_parent_must_exist_even_for_reference_mapping(inputs):
    inputs[2]["nodes"] = inputs[2]["nodes"][1:]
    inputs[0]["source"]["cad_node_count"] = inputs[3]["geometry"]["assembly_node_count"] = 1
    with pytest.raises(ValueError, match="PARENT_NOT_FOUND"):
        _builder()(*inputs)


def test_duplicate_equipment_ids_fail_closed(inputs):
    children = inputs[1]["roots"][0]["children"]
    children.append(copy.deepcopy(children[0]))
    with pytest.raises(ValueError, match="DUPLICATE_EQUIPMENT"):
        _builder()(*inputs)


def test_real_pinned_m3_evidence_maps_three_candidates_and_sixteen_unlocated():
    root_value = os.environ.get("SAT_SIM_P9M4_REAL_EVIDENCE")
    if not root_value:
        pytest.skip("Dedicated P9-M4 real artifact intake job only")
    root = Path(root_value)
    load = lambda name: json.loads((root / name).read_text(encoding="utf-8"))
    result = _builder()(
        load("BIRDS4_FM_Final_v7.installation-review.json"),
        load("workbench-object-tree.v2.json"),
        load("BIRDS4_FM_Final_v7.assembly-tree.json"),
        load("BIRDS4_FM_Final_v7.report.json"),
        root / "BIRDS4_FM_Final_v7.glb",
    )
    assert result["validation"]["counts_by_kind"] == {"reference_cad": 3, "unlocated": 16}
    assert result["summary"]["equipment_category_count"] == 19
    assert result["summary"]["as_built_verified_count"] == 0
    assert result["summary"]["node_renderable_count"] == 0
    refs = [r for r in result["mappings"] if r["mapping_kind"] == "reference_cad"]
    assert {r["object_id"] for r in refs} == {"component.battery", "component.reaction_wheel", "component.payload"}
    assert all(r["review"]["required_reviews"]["coordinate_datum"] == "blocked" for r in refs)
    assert all(r["geometry_asset"]["glb_node_binding"] is None for r in refs)
    assert all(r["geometry_asset"]["glb_node_binding_candidate"]["selector_promoted"] is False for r in refs)
    assert all(r["render"]["renderable"] is False for r in refs)
    assert result["glb_node_binding_audit"]["selector_promotion_count"] == 0
    print("P9M4_GLB_NODE_BINDING_AUDIT=" + json.dumps(result["glb_node_binding_audit"], sort_keys=True))
    (root / "p9m4-reference-registry.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_unique_exact_glb_node_name_and_parent_is_candidate_only(inputs):
    result = _builder()(*inputs)
    reference = next(row for row in result["mappings"] if row["mapping_kind"] == "reference_cad")
    candidate = reference["geometry_asset"]["glb_node_binding_candidate"]
    assert candidate["status"] == "unique_exact_name_parent_match_candidate"
    assert candidate["candidate_glb_node_index"] == 1
    assert candidate["candidate_glb_node_name"] == "battery"
    assert candidate["candidate_glb_parent_index"] == 0
    assert candidate["selector_promoted"] is False
    assert candidate["requires_visual_review"] is True
    assert reference["geometry_asset"]["glb_node_binding"] is None
    assert reference["render"]["renderable"] is False
    assert reference["review"]["node_binding_status"] == "unique_exact_name_parent_match_candidate"


def test_unique_name_without_mesh_is_not_a_renderable_node_candidate(inputs):
    path = inputs[-1]
    _write_glb(path, [
        {"name": "root", "children": [1]},
        {"name": "battery"},
    ])
    inputs[3]["geometry"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    inputs[3]["geometry"]["size_bytes"] = path.stat().st_size
    result = _builder()(*inputs)
    reference = next(row for row in result["mappings"] if row["mapping_kind"] == "reference_cad")
    candidate = reference["geometry_asset"]["glb_node_binding_candidate"]
    assert candidate["status"] == "matched_node_without_mesh"
    assert candidate["candidate_glb_node_index"] is None


def test_ambiguous_glb_names_do_not_produce_node_selector(inputs):
    path = inputs[-1]
    _write_glb(path, [
        {"name": "root", "children": [1, 2]},
        {"name": "battery", "mesh": 0},
        {"name": "battery", "mesh": 1},
    ])
    inputs[3]["geometry"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    inputs[3]["geometry"]["size_bytes"] = path.stat().st_size
    result = _builder()(*inputs)
    reference = next(row for row in result["mappings"] if row["mapping_kind"] == "reference_cad")
    candidate = reference["geometry_asset"]["glb_node_binding_candidate"]
    assert candidate["status"] == "ambiguous_exact_name"
    assert candidate["candidate_glb_node_index"] is None
    assert reference["geometry_asset"]["glb_node_binding"] is None


def test_missing_step_parent_name_never_produces_candidate_selector(inputs):
    inputs[2]["nodes"][0]["source_node_name"] = None
    result = _builder()(*inputs)
    reference = next(row for row in result["mappings"] if row["mapping_kind"] == "reference_cad")
    candidate = reference["geometry_asset"]["glb_node_binding_candidate"]
    assert candidate["status"] == "step_parent_name_missing"
    assert candidate["candidate_glb_node_index"] is None
    assert candidate["selector_promoted"] is False


def test_step_root_cannot_match_non_root_glb_node(inputs):
    node = inputs[2]["nodes"][1]
    node["parent_node_path"] = None
    inputs[0]["review_packets"][0]["cad_source"]["parent_node_path"] = None
    inputs[0]["review_packets"][0]["cad_source"]["evidence_sha256"] = _digest({
        "candidate_id": "battery-candidate",
        "source_commit": inputs[0]["source"]["source_commit"],
        "source_tree_sha": inputs[0]["source"]["source_tree_sha"],
        "node": node,
    })
    result = _builder()(*inputs)
    reference = next(row for row in result["mappings"] if row["mapping_kind"] == "reference_cad")
    candidate = reference["geometry_asset"]["glb_node_binding_candidate"]
    assert candidate["status"] == "parent_name_mismatch"
    assert candidate["candidate_glb_node_index"] is None


def test_glb_chunk_overrun_is_rejected_after_hash_receipt_matches(inputs):
    path = inputs[-1]
    data = bytearray(path.read_bytes())
    struct.pack_into("<I", data, 12, 0xFFFFFFFC)
    path.write_bytes(data)
    inputs[3]["geometry"]["sha256"] = hashlib.sha256(data).hexdigest()
    inputs[3]["geometry"]["size_bytes"] = len(data)
    with pytest.raises(ValueError, match="GLB_CHUNK_RANGE"):
        _builder()(*inputs)


def test_glb_bin_chunk_must_follow_json_directly(inputs):
    path = inputs[-1]
    _write_glb(path, [{"name": "battery", "mesh": 0}])
    data = path.read_bytes()
    json_length = struct.unpack_from("<I", data, 12)[0]
    json_chunk = data[12:20 + json_length]
    unknown = b"test"
    binary = b"\0\0\0\0"
    chunks = (
        json_chunk
        + struct.pack("<I4s", len(unknown), b"TEST") + unknown
        + struct.pack("<I4s", len(binary), b"BIN\0") + binary
    )
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks)
    inputs[3]["geometry"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    inputs[3]["geometry"]["size_bytes"] = path.stat().st_size
    with pytest.raises(ValueError, match="GLB_BIN_CHUNK_ORDER_OR_DUPLICATE"):
        _builder()(*inputs)


def test_glb_node_hierarchy_cycle_is_rejected(inputs):
    path = inputs[-1]
    _write_glb(path, [
        {"name": "root", "children": [1]},
        {"name": "battery", "children": [0], "mesh": 0},
    ])
    inputs[3]["geometry"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    inputs[3]["geometry"]["size_bytes"] = path.stat().st_size
    with pytest.raises(ValueError, match="GLB_NODE_HIERARCHY_CYCLE"):
        _builder()(*inputs)


def test_glb_parent_name_mismatch_does_not_produce_node_selector(inputs):
    path = inputs[-1]
    _write_glb(path, [
        {"name": "different-root", "children": [1]},
        {"name": "battery", "mesh": 0},
    ])
    inputs[3]["geometry"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    inputs[3]["geometry"]["size_bytes"] = path.stat().st_size
    result = _builder()(*inputs)
    reference = next(row for row in result["mappings"] if row["mapping_kind"] == "reference_cad")
    candidate = reference["geometry_asset"]["glb_node_binding_candidate"]
    assert candidate["status"] == "parent_name_mismatch"
    assert candidate["candidate_glb_node_index"] is None
    assert reference["geometry_asset"]["glb_node_binding"] is None
