"""Build the user-facing spacecraft -> subsystem -> equipment -> quantity tree.

Component model categories are not an as-built spacecraft inventory. External
and logical objects remain outside the satellite branch; no count or spatial
location is invented.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .workbench_quantity_names import output_definition, parameter_definition

OBJECT_TREE_VERSION = "workbench-object-tree.v1"
_CONTEXT_OBJECTS = {
    "component.ground_station", "component.link_budget", "component.data_queue",
    "component.power_sink", "component.thermal_node",
}


def _quantity_children(object_row: Mapping[str, Any], schemas: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    capability_id = object_row.get("execution_capability_id")
    if not isinstance(capability_id, str):
        return [], {"parameters": 0, "summary": 0, "observable": 0, "unreviewed": 0}
    schema = schemas.get(capability_id)
    if not isinstance(schema, Mapping):
        return [], {"parameters": 0, "summary": 0, "observable": 0, "unreviewed": 0}
    children: list[dict[str, Any]] = []
    counts = {"parameters": 0, "summary": 0, "observable": 0, "unreviewed": 0}
    seen_parameters: set[str] = set()
    for field in schema.get("fields", []):
        if not isinstance(field, Mapping):
            continue
        path = str(field.get("path") or "")
        if path.startswith("parameters.values."):
            source_name = path[len("parameters.values."):]
        elif path.startswith("parameters."):
            source_name = path[len("parameters."):]
        else:
            continue
        if source_name in seen_parameters:
            raise ValueError(f"OBJECT_TREE_DUPLICATE_PARAMETER:{capability_id}:{source_name}")
        seen_parameters.add(source_name)
        definition = parameter_definition(capability_id, source_name)
        if definition is None or field.get("unit") != definition["unit"]:
            counts["unreviewed"] += 1
            continue
        children.append({
            "node_id": f"{object_row['object_id']}:parameter:{source_name}",
            "kind": "parameter", "source_capability_id": capability_id,
            "source_field": source_name, "display_name_zh": definition["display_name_zh"],
            "unit": field.get("unit"), "value_type": field.get("type"),
            "default": deepcopy(field.get("default")),
            "minimum": field.get("min"), "maximum": field.get("max"),
            "description_zh": str(field.get("description") or ""), "children": [],
        })
        counts["parameters"] += 1
    outputs = schema.get("outputs")
    if isinstance(outputs, Mapping):
        for section, kind in (("summary_options", "summary"), ("trace_options", "observable")):
            seen_outputs: set[str] = set()
            for field in outputs.get(section, []):
                if not isinstance(field, Mapping) or not field.get("name"):
                    continue
                source_name = str(field["name"])
                if source_name in seen_outputs:
                    raise ValueError(f"OBJECT_TREE_DUPLICATE_OUTPUT:{capability_id}:{source_name}")
                seen_outputs.add(source_name)
                definition = output_definition(
                    source_name, capability_id=capability_id,
                    kind="trace" if kind == "observable" else "summary",
                )
                if definition is None or field.get("unit") != definition["unit"]:
                    counts["unreviewed"] += 1
                    continue
                children.append({
                    "node_id": f"{object_row['object_id']}:{kind}:{source_name}",
                    "kind": kind, "source_capability_id": capability_id,
                    "source_field": source_name, "display_name_zh": definition["display_name_zh"],
                    "unit": field.get("unit"), "value_type": field.get("dtype"),
                    "description_zh": str(field.get("description") or ""), "children": [],
                })
                counts[kind] += 1
    return children, counts


def build_object_tree(scene_catalog: Mapping[str, Any],
                      capability_schemas: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Nest read-only metadata as spacecraft -> subsystem -> equipment -> quantities."""
    if not isinstance(scene_catalog, Mapping) or not isinstance(capability_schemas, Mapping):
        raise ValueError("OBJECT_TREE_INPUTS_MUST_BE_MAPPINGS")
    rows = scene_catalog.get("objects")
    if not isinstance(rows, list):
        raise ValueError("OBJECT_TREE_OBJECTS_MUST_BE_LIST")
    indexed: dict[str, dict[str, Any]] = {}
    for source in rows:
        if not isinstance(source, Mapping) or not isinstance(source.get("object_id"), str):
            raise ValueError("OBJECT_TREE_INVALID_OBJECT")
        object_id = source["object_id"]
        if object_id in indexed:
            raise ValueError(f"OBJECT_TREE_DUPLICATE_OBJECT:{object_id}")
        row = deepcopy(dict(source))
        row["children"] = []
        if row.get("kind") == "component":
            row["kind"] = "equipment" if object_id not in _CONTEXT_OBJECTS else "context_object"
            if row["kind"] == "equipment":
                row["instance_semantics"] = "capability_model_not_vehicle_inventory"
                row["inventory_count"] = None
                row["inventory_count_source"] = None
        children, counts = _quantity_children(row, capability_schemas)
        row["children"].extend(children)
        row["quantity_counts"] = counts
        indexed[object_id] = row
    if "whole_spacecraft" not in indexed:
        raise ValueError("OBJECT_TREE_ROOT_MISSING")
    roots: list[dict[str, Any]] = []
    for object_id, row in indexed.items():
        parent_id = row.get("parent_object_id")
        if object_id == "whole_spacecraft":
            roots.append(row)
        elif object_id in _CONTEXT_OBJECTS:
            continue
        elif parent_id is None:
            roots.append(row)
        elif parent_id not in indexed:
            raise ValueError(f"OBJECT_TREE_PARENT_MISSING:{object_id}:{parent_id}")
        else:
            indexed[parent_id]["children"].append(row)
    context_children = [indexed[key] for key in sorted(_CONTEXT_OBJECTS) if key in indexed]
    for row in context_children:
        row["parent_object_id"] = "mission_context"
    if context_children:
        roots.append({
            "object_id": "mission_context", "name_zh": "任务环境与逻辑对象",
            "kind": "context_group", "parent_object_id": None,
            "spatial_anchor": None, "children": context_children,
        })
    tree = indexed["whole_spacecraft"]
    totals = {"equipment": 0, "parameters": 0, "summary": 0, "observable": 0, "unreviewed": 0}
    for row in indexed.values():
        if row["kind"] == "equipment":
            totals["equipment"] += 1
        for key in ("parameters", "summary", "observable", "unreviewed"):
            totals[key] += row.get("quantity_counts", {}).get(key, 0)
    return {
        "schema_version": OBJECT_TREE_VERSION,
        "tree": tree, "roots": roots, "totals": totals,
        "semantics": {
            "hierarchy": "spacecraft_subsystem_equipment_quantity",
            "component_counts_are_inventory": False,
            "instance_count_source": "explicit_capability_or_vehicle_inventory_only",
            "unreviewed_fields_visible": False,
            "spatial_anchors_require_asset_evidence": True,
        },
    }
