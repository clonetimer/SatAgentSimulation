"""Read-only M3-to-M4 intake. No approval, installed instance or GLB selector inference.

Run as ``python -m sat_sim.engineering_reference_registry --help``. Only the
pinned, blocked M3 reference-evidence path is supported; as-built approval is
intentionally a separate future operation.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import struct
from pathlib import Path
from typing import Any, Mapping

from sat_sim.engineering_asset_mapping import (
    REQUIRED_AS_BUILT_EVIDENCE, SCHEMA_VERSION, validate_mapping_bundle,
)
from sat_sim.engineering_mapping_policy import require_sha256, require_text


def _digest(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError(f"P9M4_INTAKE_{code}")


def _equipment(tree: Mapping[str, Any]) -> dict[str, str]:
    _require(tree.get("schema_version") == "workbench-object-tree.v2", "ENGINEERING_SCHEMA")
    roots = tree.get("roots")
    if roots is None:
        roots = [tree.get("tree")]
    _require(isinstance(roots, list) and bool(roots), "ENGINEERING_ROOTS")
    result: dict[str, str] = {}

    def visit(node: Any) -> None:
        _require(isinstance(node, Mapping), "ENGINEERING_NODE")
        if node.get("kind") == "equipment":
            object_id = require_text(node.get("object_id"), "equipment.object_id")
            _require(object_id not in result, "DUPLICATE_EQUIPMENT")
            result[object_id] = str(node.get("name_zh") or object_id)
        children = node.get("children", [])
        _require(isinstance(children, list), "ENGINEERING_CHILDREN")
        for child in children:
            visit(child)

    for root in roots:
        visit(root)
    _require(bool(result), "EMPTY_EQUIPMENT")
    return result


def _glb_identity(path: Path, geometry: Mapping[str, Any]) -> str:
    expected = require_sha256(geometry.get("sha256"), "conversion.geometry.sha256")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        header = stream.read(12)
        digest.update(header)
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    size = path.stat().st_size
    _require(size == geometry.get("size_bytes") and digest.hexdigest() == expected, "GLB_IDENTITY")
    _require(len(header) == 12, "GLB_HEADER")
    magic, version, declared_size = struct.unpack("<4sII", header)
    _require(magic == b"glTF" and version == 2 and declared_size == size, "GLB_HEADER")
    return expected


def _glb_node_inventory(path: Path) -> dict[str, Any]:
    """Read the complete GLB chunk envelope and node hierarchy without promotion."""
    data = path.read_bytes()
    _require(len(data) >= 20, "GLB_JSON_HEADER")
    magic, version, declared_length = struct.unpack_from("<4sII", data, 0)
    _require(magic == b"glTF" and version == 2 and declared_length == len(data), "GLB_JSON_HEADER")

    offset = 12
    document = None
    binary_seen = False
    chunk_index = 0
    while offset < len(data):
        _require(offset + 8 <= len(data), "GLB_CHUNK_HEADER")
        chunk_length, chunk_type = struct.unpack_from("<I4s", data, offset)
        offset += 8
        chunk_end = offset + chunk_length
        _require(chunk_end <= len(data) and chunk_length % 4 == 0, "GLB_CHUNK_RANGE")
        if chunk_index == 0:
            _require(chunk_type == b"JSON", "GLB_JSON_CHUNK_ORDER")
        if chunk_type == b"JSON":
            _require(document is None, "GLB_DUPLICATE_JSON_CHUNK")
            try:
                document = json.loads(data[offset:chunk_end].decode("utf-8").rstrip(" "))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("P9M4_INTAKE_GLB_JSON_INVALID") from exc
        elif chunk_type == b"BIN\0":
            _require(chunk_index == 1 and document is not None and not binary_seen,
                     "GLB_BIN_CHUNK_ORDER_OR_DUPLICATE")
            binary_seen = True
        # Unknown extension chunks are retained by the GLB format and ignored here.
        offset = chunk_end
        chunk_index += 1
    _require(offset == len(data) and isinstance(document, Mapping), "GLB_CHUNK_LAYOUT")

    raw_nodes = document.get("nodes")
    raw_meshes = document.get("meshes", [])
    raw_scenes = document.get("scenes", [])
    _require(isinstance(raw_nodes, list), "GLB_NODES")
    _require(isinstance(raw_meshes, list) and isinstance(raw_scenes, list), "GLB_SCENE_OR_MESH_ARRAY")
    parents: dict[int, int] = {}
    rows = []
    for index, node in enumerate(raw_nodes):
        _require(isinstance(node, Mapping), "GLB_NODE")
        children = node.get("children", [])
        _require(isinstance(children, list), "GLB_NODE_CHILDREN")
        mesh_index = node.get("mesh")
        _require(mesh_index is None or (type(mesh_index) is int and 0 <= mesh_index < len(raw_meshes)),
                 "GLB_NODE_MESH_REFERENCE")
        if mesh_index is not None:
            primitives = raw_meshes[mesh_index].get("primitives") if isinstance(raw_meshes[mesh_index], Mapping) else None
            _require(isinstance(primitives, list) and bool(primitives), "GLB_MESH_PRIMITIVES")
        for child in children:
            _require(type(child) is int and 0 <= child < len(raw_nodes), "GLB_CHILD_INDEX")
            _require(child != index and child not in parents, "GLB_NODE_MULTIPLE_PARENTS")
            parents[child] = index
        name = node.get("name")
        _require(name is None or isinstance(name, str), "GLB_NODE_NAME_TYPE")
        rows.append({
            "node_index": index,
            "name": name,
            "mesh_index": mesh_index,
            "parent_node_index": None,
            "child_node_indices": list(children),
        })
    for row in rows:
        row["parent_node_index"] = parents.get(row["node_index"])
    for node_index in range(len(rows)):
        visited = set()
        cursor = node_index
        while cursor in parents:
            _require(cursor not in visited, "GLB_NODE_HIERARCHY_CYCLE")
            visited.add(cursor)
            cursor = parents[cursor]
    for scene in raw_scenes:
        _require(isinstance(scene, Mapping), "GLB_SCENE")
        roots = scene.get("nodes", [])
        _require(isinstance(roots, list), "GLB_SCENE_ROOTS")
        for root_index in roots:
            _require(type(root_index) is int and 0 <= root_index < len(rows), "GLB_SCENE_ROOT_INDEX")
            _require(root_index not in parents, "GLB_SCENE_ROOT_HAS_PARENT")
    return {
        "node_count": len(rows),
        "mesh_count": len(raw_meshes),
        "scene_count": len(raw_scenes),
        "nodes": rows,
    }


def _candidate_glb_binding(step_node: Mapping[str, Any], step_nodes: Mapping[str, Mapping[str, Any]],
                           glb_nodes: Mapping[str, Any]) -> dict[str, Any]:
    source_name = step_node.get("source_node_name")
    parent_path = step_node.get("parent_node_path")
    parent_name = None
    if not isinstance(source_name, str) or not source_name.strip():
        status = "step_node_name_missing"
        selected = None
    elif parent_path is None:
        status = "pending_root_check"
        selected = None
    elif parent_path not in step_nodes:
        status = "step_parent_unresolved"
        selected = None
    else:
        parent_name = step_nodes[parent_path].get("source_node_name")
        if not isinstance(parent_name, str) or not parent_name.strip():
            status = "step_parent_name_missing"
            selected = None
        else:
            status = "pending_exact_name_match"
            selected = None

    descendant_mesh_nodes = []
    if status in {"pending_root_check", "pending_exact_name_match"}:
        matches = [row for row in glb_nodes["nodes"] if row.get("name") == source_name]
        if not matches:
            status = "unmatched_exact_name"
        elif len(matches) > 1:
            status = "ambiguous_exact_name"
        else:
            candidate = matches[0]
            parent_index = candidate.get("parent_node_index")
            glb_parent = next((row for row in glb_nodes["nodes"] if row["node_index"] == parent_index), None)
            glb_parent_name = glb_parent.get("name") if glb_parent else None
            parent_matches = (
                parent_index is None if parent_path is None
                else glb_parent_name == parent_name
            )
            if not parent_matches:
                status = "parent_name_mismatch"
            else:
                nodes_by_index = {row["node_index"]: row for row in glb_nodes["nodes"]}
                pending = list(candidate.get("child_node_indices", []))
                while pending:
                    child_index = pending.pop(0)
                    child = nodes_by_index[child_index]
                    if child.get("mesh_index") is not None:
                        descendant_mesh_nodes.append({
                            "node_index": child["node_index"],
                            "name": child.get("name"),
                            "mesh_index": child["mesh_index"],
                        })
                    pending.extend(child.get("child_node_indices", []))
                if candidate.get("mesh_index") is not None:
                    status = "unique_exact_name_parent_match_candidate"
                    selected = candidate
                elif descendant_mesh_nodes:
                    status = "unique_exact_name_parent_match_group_candidate"
                    selected = candidate
                else:
                    status = "matched_node_without_mesh"

    return {
        "status": status,
        "source_step_node_name": source_name if isinstance(source_name, str) else None,
        "expected_step_parent_name": parent_name,
        "candidate_glb_node_index": selected.get("node_index") if selected else None,
        "candidate_glb_node_name": selected.get("name") if selected else None,
        "candidate_glb_parent_index": selected.get("parent_node_index") if selected else None,
        "candidate_glb_mesh_index": selected.get("mesh_index") if selected else None,
        "candidate_descendant_mesh_nodes": descendant_mesh_nodes,
        "candidate_geometry_node_count": len(descendant_mesh_nodes) + (1 if selected and selected.get("mesh_index") is not None else 0),
        "selector_promoted": False,
        "requires_visual_review": True,
    }


def build_reference_registry(
    review: Mapping[str, Any],
    engineering_tree: Mapping[str, Any],
    assembly_tree: Mapping[str, Any],
    conversion_report: Mapping[str, Any],
    glb_path: Path,
) -> dict[str, Any]:
    """Validate source linkage and retain every physical category without promotion."""
    _require(all(isinstance(x, Mapping) for x in (review, engineering_tree, assembly_tree, conversion_report)), "INPUT_OBJECTS")
    _require(review.get("schema_version") == "cad-installation-evidence-review.v1", "REVIEW_SCHEMA")
    packets = review.get("review_packets")
    _require(isinstance(packets, list) and bool(packets), "EMPTY_REVIEW")
    source, converted = review.get("source", {}), conversion_report.get("source", {})
    geometry = conversion_report.get("geometry", {})
    _require(all(isinstance(x, Mapping) for x in (source, converted, geometry)), "SOURCE_OBJECTS")
    commit, tree_sha = source.get("source_commit"), source.get("source_tree_sha")
    _require(all(isinstance(x, str) and re.fullmatch(r"[0-9a-f]{40}", x) for x in (commit, tree_sha)), "SOURCE_IDENTITY")
    _require(commit == source.get("cad_revision") == converted.get("commit"), "SOURCE_IDENTITY")
    _require(source.get("cad_model") == converted.get("path") == assembly_tree.get("source_path"), "SOURCE_IDENTITY")
    _require(conversion_report.get("promotion_status") == "reference_cad_imported_not_as_built_verified", "CONVERSION_PROMOTION")
    _require(conversion_report.get("conversion", {}).get("output_unit") == "metres", "CONVERSION_UNITS")
    glb_sha = _glb_identity(Path(glb_path), geometry)
    glb_inventory = _glb_node_inventory(Path(glb_path))
    equipment = _equipment(engineering_tree)
    raw_nodes = assembly_tree.get("nodes")
    _require(isinstance(raw_nodes, list), "ASSEMBLY_NODES")
    nodes = {}
    for node in raw_nodes:
        _require(isinstance(node, Mapping), "ASSEMBLY_NODE")
        path = require_text(node.get("source_node_path"), "assembly.source_node_path")
        _require(path not in nodes, "DUPLICATE_CAD_PATH")
        nodes[path] = node
    _require(len(nodes) == source.get("cad_node_count") == geometry.get("assembly_node_count"), "NODE_COUNT")
    _require(len(packets) == source.get("candidate_count") == review.get("summary", {}).get("review_packet_count"), "PACKET_COUNT")
    glb_binding_status_counts: dict[str, int] = {}
    mappings, covered = [], set()
    seen_candidates, seen_packets, seen_pairs = set(), set(), set()
    for packet in packets:
        _require(isinstance(packet, Mapping), "PACKET_OBJECT")
        candidate_id = require_text(packet.get("candidate_id"), "candidate_id")
        packet_id = require_text(packet.get("review_packet_id"), "review_packet_id")
        _require(candidate_id not in seen_candidates and packet_id not in seen_packets, "DUPLICATE_PACKET")
        seen_candidates.add(candidate_id)
        seen_packets.add(packet_id)
        object_id = packet.get("engineering_object_id")
        _require(isinstance(object_id, str) and object_id in equipment, "UNKNOWN_EQUIPMENT")
        cad = packet.get("cad_source", {})
        _require(isinstance(cad, Mapping), "CAD_SOURCE")
        path = cad.get("source_node_path")
        _require(isinstance(path, str) and path in nodes, "NODE_NOT_FOUND")
        _require((object_id, path) not in seen_pairs, "DUPLICATE_PACKET")
        seen_pairs.add((object_id, path))
        node = nodes[path]
        _require(cad.get("parent_node_path") == node.get("parent_node_path"), "PARENT_MISMATCH")
        parent = node.get("parent_node_path")
        _require(parent is None or parent in nodes, "PARENT_NOT_FOUND")
        _require(cad.get("upstream_commit") == cad.get("revision") == commit and cad.get("source_tree_sha") == tree_sha and cad.get("model") == source.get("cad_model"), "SOURCE_IDENTITY")
        node_digest = _digest({"candidate_id": candidate_id, "source_commit": commit, "source_tree_sha": tree_sha, "node": node})
        _require(cad.get("evidence_sha256") == node_digest, "NODE_EVIDENCE")
        promotion = packet.get("promotion", {})
        _require(isinstance(promotion, Mapping) and promotion.get("status") == "blocked_evidence_incomplete"
                 and promotion.get("render_eligible") is False
                 and all(promotion.get(k) is None for k in ("instance_id", "installation_transform", "spatial_anchor")), "PROMOTION_FORBIDDEN")
        gates = packet.get("required_reviews")
        _require(isinstance(gates, Mapping) and REQUIRED_AS_BUILT_EVIDENCE.issubset(gates), "REVIEW_GATES")
        binding_candidate = _candidate_glb_binding(node, nodes, glb_inventory)
        glb_binding_status_counts[binding_candidate["status"]] = glb_binding_status_counts.get(binding_candidate["status"], 0) + 1
        mapping_id = "reference:" + _digest([glb_sha, object_id, path])[:24]
        mappings.append({
            "mapping_id": mapping_id, "object_id": object_id, "name_zh": equipment[object_id],
            "mapping_kind": "reference_cad", "asset_instance_id": None,
            "geometry_asset": {"asset_id": "glb:" + glb_sha, "sha256": glb_sha,
                "source_revision": commit, "source_node_path": path,
                "node_path_space": "step_assembly_occurrence", "glb_node_binding": None,
                "glb_node_binding_candidate": binding_candidate},
            "geometry_frame": {"identity_status": "unverified"},
            "spatial_anchor": None, "telemetry_binding": None, "evidence": [],
            "render": {"as_built_eligible": False, "renderable": False, "views": ["reference_cad"]},
            "review": {"packet_id": packet_id, "candidate_id": candidate_id,
                "packet_canonical_sha256": _digest(packet), "node_evidence_sha256": node_digest,
                "status": promotion["status"], "required_reviews": copy.deepcopy(dict(gates)),
                "node_binding_status": binding_candidate["status"]},
        })
        covered.add(object_id)
    for object_id in sorted(set(equipment) - covered):
        mappings.append({
            "mapping_id": "unlocated:" + object_id, "object_id": object_id,
            "name_zh": equipment[object_id], "mapping_kind": "unlocated",
            "asset_instance_id": None, "geometry_asset": None, "geometry_frame": None,
            "spatial_anchor": None, "telemetry_binding": None, "evidence": [],
            "render": {"as_built_eligible": False, "renderable": False, "views": []},
        })
    mappings.sort(key=lambda x: (x["object_id"], x["mapping_id"]))
    bundle = {"schema_version": SCHEMA_VERSION, "mappings": mappings}
    validation = validate_mapping_bundle(bundle)
    return {
        **bundle, "validation": validation,
        "intake": {"source_commit": commit, "glb_sha256": glb_sha,
            "canonical_json_sha256": {"m3_review": _digest(review), "engineering_tree": _digest(engineering_tree),
                "assembly_tree": _digest(assembly_tree), "conversion_report": _digest(conversion_report)},
            "scope": "reference_registration_only_not_as_built_approval"},
        "glb_node_inventory": {"node_count": glb_inventory["node_count"], "mesh_count": glb_inventory["mesh_count"], "scene_count": glb_inventory["scene_count"]},
        "glb_node_binding_audit": {
            "status_counts": dict(sorted(glb_binding_status_counts.items())),
            "selector_promotion_count": 0,
            "visual_review_required": True,
        },
        "summary": {"equipment_category_count": len(equipment), "reference_candidate_count": len(packets),
            "unlocated_equipment_count": len(equipment) - len(covered), "as_built_verified_count": 0,
            "node_renderable_count": 0, "telemetry_binding_count": 0},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("review", "engineering-tree", "assembly-tree", "conversion-report", "glb", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    load = lambda path: json.loads(path.read_text(encoding="utf-8"))
    result = build_reference_registry(load(args.review), load(args.engineering_tree), load(args.assembly_tree), load(args.conversion_report), args.glb)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
