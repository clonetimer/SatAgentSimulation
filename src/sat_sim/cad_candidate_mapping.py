"""Evidence-bounded candidate mapping between CAD assembly nodes and workbench equipment categories.

This module proposes lexical candidates only. It never creates installed equipment
instances, spatial anchors, or verified as-built relationships.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "cad-engineering-candidate-mapping.v1"

# Deliberately narrow aliases. Generic "panel", "board", "sensor", "module",
# "COM", and "OBC" labels are not sufficient to identify an installed function.
_ALIASES: dict[str, tuple[tuple[str, str, float], ...]] = {
    "component.reaction_wheel": (
        (r"reaction[ _-]?wheel", "explicit_reaction_wheel_name", 1.0),
        (r"(?:^|[^A-Z0-9])RW(?:[^A-Z0-9]|$)", "rw_abbreviation", 0.92),
    ),
    "component.battery": (
        (r"battery", "explicit_battery_name", 1.0),
    ),
    "component.sun_sensor": (
        (r"sun[ _-]?sensor", "explicit_sun_sensor_name", 1.0),
        (r"(?:^|[^A-Z0-9])SNSS(?:[^A-Z0-9]|$)", "snss_abbreviation_requires_function_review", 0.82),
    ),
    "component.star_tracker": (
        (r"star[ _-]?tracker", "explicit_star_tracker_name", 1.0),
    ),
    "component.imu": (
        (r"(?:^|[^A-Z0-9])IMU(?:[^A-Z0-9]|$)", "imu_abbreviation", 0.95),
        (r"gyroscope|gyro[ _-]?sensor", "gyro_sensor_name", 0.88),
    ),
    "component.magnetometer": (
        (r"magnetometer", "explicit_magnetometer_name", 1.0),
    ),
    "component.mtb": (
        (r"magnetic[ _-]?(?:torquer|torque[ _-]?rod)", "magnetic_torquer_name", 1.0),
        (r"(?:^|[^A-Z0-9])MTB(?:[^A-Z0-9]|$)", "mtb_abbreviation", 0.9),
    ),
    "component.antenna": (
        (r"antenna", "explicit_antenna_name", 1.0),
    ),
    "component.thruster": (
        (r"thruster", "explicit_thruster_name", 1.0),
    ),
    "component.fuel_tank": (
        (r"fuel[ _-]?tank|propellant[ _-]?tank", "explicit_fuel_tank_name", 1.0),
    ),
    "component.solar_panel": (
        (r"solar[ _-]?(?:panel|array|cell)|photovoltaic", "explicit_solar_generation_name", 1.0),
    ),
    "component.transmitter": (
        (r"transmitter|transceiver", "explicit_transmitter_name", 0.95),
    ),
    "component.radiator": (
        (r"radiator", "explicit_radiator_name", 1.0),
    ),
    "component.heater": (
        (r"heater", "explicit_heater_name", 1.0),
    ),
    "component.pdu": (
        (r"power[ _-]?distribution[ _-]?unit|(?:^|[^A-Z0-9])PDU(?:[^A-Z0-9]|$)", "pdu_name", 0.9),
    ),
    "component.onboard_storage": (
        (r"onboard[ _-]?storage|mass[ _-]?memory", "explicit_storage_name", 0.9),
    ),
    "component.payload": (
        (r"payload[ _-]?(?:instrument|unit|module)", "explicit_payload_name", 0.9),
        (r"perovskite[ _-]?solar[ _-]?cells?", "perovskite_solar_cell_mission_payload_candidate", 0.95),
    ),
    "component.payload_sensor": (
        (r"payload[ _-]?sensor", "explicit_payload_sensor_name", 1.0),
    ),
}

_NON_EQUIPMENT_ROLE_PATTERNS: dict[str, re.Pattern[str]] = {
    # Mechanical installation aids are not the antenna itself.
    "component.antenna": re.compile(r"fixing|guide|washer|bracket|mount|holder|support|connector|parts", re.I),
    # BIRDS-4 Perovskite Solar Cells are a technology-demonstration payload,
    # not the bus power-generation solar array.
    "component.solar_panel": re.compile(r"perovskite", re.I),
}

_VARIANT_PATTERNS = (
    ("em", re.compile(r"B4_EM|(?:^|[_\s])EM(?:[_\s]|$)", re.I)),
    ("b3", re.compile(r"B3[_\s]", re.I)),
    ("fm", re.compile(r"B4_FM|BIRDS4_FM", re.I)),
)


def _variant_tags(label: str) -> list[str]:
    return [tag for tag, pattern in _VARIANT_PATTERNS if pattern.search(label)]


def _flatten_equipment(tree_payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Accept workbench_object_tree response (tree/roots) or a raw tree node."""
    roots: list[Any]
    if isinstance(tree_payload.get("roots"), list):
        roots = list(tree_payload["roots"])
        tree_root = tree_payload.get("tree")
        if isinstance(tree_root, Mapping):
            tree_root_id = tree_root.get("object_id")
            if not any(isinstance(root, Mapping) and root.get("object_id") == tree_root_id for root in roots):
                roots.insert(0, tree_root)
    elif isinstance(tree_payload.get("tree"), Mapping):
        roots = [tree_payload["tree"]]
    elif isinstance(tree_payload.get("object_id"), str):
        roots = [tree_payload]
    else:
        raise ValueError("CAD_MAPPING_ENGINEERING_TREE_MISSING")

    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(node: Any) -> None:
        if not isinstance(node, Mapping):
            raise ValueError("CAD_MAPPING_INVALID_ENGINEERING_NODE")
        object_id = node.get("object_id")
        if node.get("kind") == "equipment" and isinstance(object_id, str):
            if object_id in seen:
                raise ValueError(f"CAD_MAPPING_DUPLICATE_ENGINEERING_OBJECT:{object_id}")
            seen.add(object_id)
            result.append({
                "object_id": object_id,
                "name_zh": str(node.get("name_zh") or object_id),
                "subsystem": node.get("parent_object_id"),
                "instance_semantics": node.get("instance_semantics"),
                "inventory_count": node.get("inventory_count"),
            })
        children = node.get("children", [])
        if not isinstance(children, list):
            raise ValueError("CAD_MAPPING_ENGINEERING_CHILDREN_MUST_BE_LIST")
        for child in children:
            visit(child)

    for root in roots:
        visit(root)
    return result


def _cad_node_rows(payload: Mapping[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = payload if isinstance(payload, list) else payload.get("nodes")
    if not isinstance(rows, list):
        raise ValueError("CAD_MAPPING_CAD_NODES_MISSING")
    result: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("CAD_MAPPING_INVALID_CAD_NODE")
        path = row.get("source_node_path")
        name = row.get("source_label_name") or row.get("source_node_name")
        if not isinstance(path, str) or not path.strip() or not isinstance(name, str) or not name.strip():
            raise ValueError("CAD_MAPPING_CAD_NODE_REQUIRES_PATH_AND_NAME")
        if path in seen_paths:
            raise ValueError(f"CAD_MAPPING_DUPLICATE_CAD_NODE_PATH:{path}")
        seen_paths.add(path)
        result.append({
            "source_node_path": path,
            "parent_node_path": row.get("parent_node_path"),
            "source_node_name": str(row.get("source_node_name") or name),
            "source_label_name": name,
            "duplicate_occurrence": row.get("duplicate_occurrence"),
            "direct_shape_count": row.get("direct_shape_count"),
            "child_count": row.get("child_count"),
            "local_transform_m": row.get("local_transform_m"),
            "world_transform_m": row.get("world_transform_m"),
        })
    return result


def build_candidate_mapping(
    engineering_tree: Mapping[str, Any],
    assembly_tree: Mapping[str, Any] | list[dict[str, Any]],
    *,
    source_model: str = "unspecified_reference_cad",
    source_revision: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic lexical candidate report; never assert as-built truth."""
    equipment = _flatten_equipment(engineering_tree)
    cad_nodes = _cad_node_rows(assembly_tree)
    equipment_by_id = {row["object_id"]: row for row in equipment}
    candidates: list[dict[str, Any]] = []

    for node in cad_nodes:
        label = node["source_label_name"]
        variant_tags = _variant_tags(label + " " + node["source_node_path"])
        # A CAD leaf with one or more direct shapes is geometry, not necessarily
        # a component occurrence. Only assembly nodes provide a stable candidate
        # boundary at this stage; leaf geometry is retained in the source tree.
        if not isinstance(node["child_count"], int) or node["child_count"] <= 0:
            continue
        for object_id, aliases in _ALIASES.items():
            if object_id not in equipment_by_id:
                continue
            excluded_role = _NON_EQUIPMENT_ROLE_PATTERNS.get(object_id)
            if excluded_role and excluded_role.search(label):
                continue
            hits = []
            for pattern, reason, strength in aliases:
                if re.search(pattern, label, re.I):
                    hits.append({"reason": reason, "lexical_match_strength": strength})
            if not hits:
                continue
            best = max(hits, key=lambda item: item["lexical_match_strength"])
            identity_review = bool({"em", "b3"}.intersection(variant_tags))
            candidate_key = hashlib.sha256(
                f"{object_id}\0{node['source_node_path']}".encode("utf-8")
            ).hexdigest()[:20]
            candidates.append({
                "candidate_id": candidate_key,
                "engineering_object_id": object_id,
                "engineering_object_name": equipment_by_id[object_id]["name_zh"],
                "cad_source_model": source_model,
                "cad_source_revision": source_revision,
                "cad_node_path": node["source_node_path"],
                "cad_parent_node_path": node["parent_node_path"],
                "cad_source_node_name": node["source_node_name"],
                "cad_source_label_name": label,
                "duplicate_occurrence": node["duplicate_occurrence"],
                "direct_shape_count": node["direct_shape_count"],
                "child_count": node["child_count"],
                "node_granularity": "assembly_occurrence",
                "match_method": "conservative_source_name_alias",
                "match_evidence": hits,
                "lexical_match_strength": best["lexical_match_strength"],
                "variant_tags": variant_tags,
                "identity_review_required": identity_review,
                "geometry_review_required": True,
                "coordinate_review_required": True,
                "installation_evidence_required": True,
                "review_status": "candidate_only",
                "verification_status": "draft",
                "instance_id": None,
                "spatial_anchor": None,
                "render_eligible": False,
            })

    by_object = Counter(item["engineering_object_id"] for item in candidates)
    by_node = Counter(item["cad_node_path"] for item in candidates)
    for item in candidates:
        item["multiple_cad_candidates_for_object"] = by_object[item["engineering_object_id"]] > 1
        item["multiple_engineering_candidates_for_node"] = by_node[item["cad_node_path"]] > 1
        item["ambiguous"] = (
            item["multiple_cad_candidates_for_object"]
            or item["multiple_engineering_candidates_for_node"]
            or item["identity_review_required"]
        )

    mapped_objects = set(by_object)
    mapped_nodes = set(by_node)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "cad_model": source_model,
            "cad_revision": source_revision,
            "cad_node_count": len(cad_nodes),
            "cad_assembly_node_count": sum(1 for node in cad_nodes if isinstance(node["child_count"], int) and node["child_count"] > 0),
            "cad_leaf_geometry_node_count": sum(1 for node in cad_nodes if not isinstance(node["child_count"], int) or node["child_count"] <= 0),
            "engineering_equipment_category_count": len(equipment),
        },
        "semantics": {
            "mapping_kind": "candidate_not_verified",
            "lexical_strength_is_not_probability": True,
            "cad_nodes_are_not_installed_inventory": True,
            "candidate_count_is_not_equipment_count": True,
            "no_instances_or_spatial_anchors_created": True,
            "requires_p9m3_evidence_review_before_p9m4_promotion": True,
        },
        "summary": {
            "candidate_count": len(candidates),
            "candidate_engineering_object_count": len(mapped_objects),
            "candidate_cad_node_count": len(mapped_nodes),
            "unmatched_engineering_object_ids": sorted(set(equipment_by_id) - mapped_objects),
            "unmatched_cad_node_count": len(cad_nodes) - len(mapped_nodes),
            "skipped_leaf_geometry_node_count": sum(1 for node in cad_nodes if not isinstance(node["child_count"], int) or node["child_count"] <= 0),
            "ambiguous_candidate_count": sum(1 for item in candidates if item["ambiguous"]),
            "identity_review_candidate_count": sum(1 for item in candidates if item["identity_review_required"]),
            "render_eligible_candidate_count": sum(1 for item in candidates if item["render_eligible"]),
        },
        "candidates": sorted(candidates, key=lambda item: (item["engineering_object_id"], item["cad_node_path"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engineering-tree", type=Path, required=True)
    parser.add_argument("--assembly-tree", type=Path, required=True)
    parser.add_argument("--source-model", default="BIRDS4_FM_Final v7.step")
    parser.add_argument("--source-revision")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    engineering = json.loads(args.engineering_tree.read_text(encoding="utf-8"))
    assembly = json.loads(args.assembly_tree.read_text(encoding="utf-8"))
    result = build_candidate_mapping(
        engineering, assembly,
        source_model=args.source_model,
        source_revision=args.source_revision,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
