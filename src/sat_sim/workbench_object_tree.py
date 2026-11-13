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
    "component.data_queue", "component.power_sink", "component.thermal_node",
}
_EXTERNAL_OBJECTS = {"component.ground_station"}
_RELATIONSHIP_OBJECTS = {"component.link_budget"}


def _quantity_children(object_row: Mapping[str, Any], schemas: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    capability_id = object_row.get("execution_capability_id")
    if not isinstance(capability_id, str):
        return [], {"parameters": 0, "summary": 0, "observable": 0, "unreviewed": 0}
    schema = schemas.get(capability_id)
    if not isinstance(schema, Mapping):
        return [], {"parameters": 0, "summary": 0, "observable": 0, "unreviewed": 0}
    counts = {"parameters": 0, "summary": 0, "observable": 0, "unreviewed": 0}
    parameter_nodes: list[dict[str, Any]] = []
    summary_nodes: list[dict[str, Any]] = []
    observable_nodes: list[dict[str, Any]] = []
    seen_parameters: set[str] = set()
    owner_name = str(object_row.get("name_zh") or object_row.get("object_id"))
    is_component = str(capability_id).startswith("component.")
    for field in schema.get("fields", []):
        if not isinstance(field, Mapping):
            continue
        path = str(field.get("path") or "")
        if not path.startswith("parameters.values."):
            continue
        source_name = path[len("parameters.values."):]
        if source_name in seen_parameters:
            raise ValueError(f"OBJECT_TREE_DUPLICATE_PARAMETER:{capability_id}:{source_name}")
        seen_parameters.add(source_name)
        definition = parameter_definition(capability_id, source_name) if is_component else None
        if is_component and (definition is None or field.get("unit") != definition["unit"]):
            counts["unreviewed"] += 1
            continue
        quantity_name = definition["display_name_zh"] if definition else f"{owner_name} · {field.get('label') or source_name}"
        instance_binding = definition.get("instance_binding") if definition else None
        default_value = deepcopy(field.get("default"))
        if instance_binding and isinstance(default_value, list):
            count_field = next((item for item in schema.get("fields", [])
                                if isinstance(item, Mapping)
                                and str(item.get("path", "")).endswith(f".{instance_binding['count_parameter']}")), None)
            configured_count = count_field.get("default") if isinstance(count_field, Mapping) else None
            instance_count = int(configured_count) if isinstance(configured_count, (int, float)) and configured_count > 0 else len(default_value)
            instance_count = min(instance_count, len(default_value))
            for index in range(instance_count):
                parameter_nodes.append({
                    "node_id": f"{object_row['object_id']}:parameter:{source_name}:{index}",
                    "kind": "parameter", "source_capability_id": capability_id,
                    "source_field": source_name, "array_index": index,
                    "display_name_zh": f"反作用轮 {index + 1} · {quantity_name.split(' · ', 1)[-1]}",
                    "unit": field.get("unit"), "value_type": field.get("type"),
                    "default": deepcopy(default_value[index]),
                    "minimum": field.get("min"), "maximum": field.get("max"),
                    "description_zh": str(field.get("description") or ""), "children": [],
                })
                counts["parameters"] += 1
        else:
            parameter_nodes.append({
                "node_id": f"{object_row['object_id']}:parameter:{source_name}",
                "kind": "parameter", "source_capability_id": capability_id,
                "source_field": source_name, "display_name_zh": quantity_name,
                "unit": field.get("unit"), "value_type": field.get("type"),
                "default": default_value,
                "minimum": field.get("min"), "maximum": field.get("max"),
                "description_zh": str(field.get("description") or ""), "children": [],
            })
            counts["parameters"] += 1
    outputs = schema.get("outputs")
    if isinstance(outputs, Mapping):
        for section, kind, target in (("summary_options", "summary", summary_nodes), ("trace_options", "observable", observable_nodes)):
            seen_outputs: set[str] = set()
            for field in outputs.get(section, []):
                if not isinstance(field, Mapping) or not field.get("name"):
                    continue
                source_name = str(field["name"])
                if source_name in seen_outputs:
                    raise ValueError(f"OBJECT_TREE_DUPLICATE_OUTPUT:{capability_id}:{source_name}")
                seen_outputs.add(source_name)
                definition = output_definition(source_name, capability_id=capability_id, kind="trace" if kind == "observable" else "summary") if is_component else None
                if is_component and (definition is None or field.get("unit") != definition["unit"]):
                    counts["unreviewed"] += 1
                    continue
                quantity_name = definition["display_name_zh"] if definition else f"{owner_name} · {field.get('label') or source_name}"
                target.append({
                    "node_id": f"{object_row['object_id']}:{kind}:{source_name}",
                    "kind": kind, "source_capability_id": capability_id,
                    "source_field": source_name, "display_name_zh": quantity_name,
                    "unit": field.get("unit"), "value_type": field.get("dtype"),
                    "description_zh": str(field.get("description") or ""), "children": [],
                })
                counts[kind] += 1
    children: list[dict[str, Any]] = []
    if parameter_nodes:
        children.append({"node_id": f"{object_row['object_id']}:physical-parameters",
                         "kind": "parameter_group", "name_zh": "物理配置参数", "children": parameter_nodes})
    if observable_nodes or summary_nodes:
        output_children = []
        if observable_nodes:
            output_children.append({"node_id": f"{object_row['object_id']}:observable-outputs",
                                    "kind": "observable_group", "name_zh": "输出参量", "children": observable_nodes})
        if summary_nodes:
            output_children.append({"node_id": f"{object_row['object_id']}:summary-outputs",
                                    "kind": "summary_group", "name_zh": "结果指标", "children": summary_nodes})
        children.extend(output_children)
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
            if object_id in _CONTEXT_OBJECTS:
                row["kind"] = "context_object"
            elif object_id in _EXTERNAL_OBJECTS:
                row["kind"] = "external_object"
            elif object_id in _RELATIONSHIP_OBJECTS:
                row["kind"] = "relationship_object"
            else:
                row["kind"] = "equipment"
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
        elif object_id in _CONTEXT_OBJECTS or object_id in _EXTERNAL_OBJECTS or object_id in _RELATIONSHIP_OBJECTS:
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
    external_children = [indexed[key] for key in sorted(_EXTERNAL_OBJECTS) if key in indexed]
    for row in external_children:
        row["parent_object_id"] = "external_environment"
    if external_children:
        roots.append({
            "object_id": "external_environment", "name_zh": "外部设施",
            "kind": "external_group", "parent_object_id": None,
            "spatial_anchor": None, "children": external_children,
        })
    relationship_children = []
    for definition in scene_catalog.get("relationship_objects", []):
        if not isinstance(definition, Mapping):
            raise ValueError("OBJECT_TREE_INVALID_RELATIONSHIP")
        object_id = str(definition.get("object_id") or "")
        if object_id not in indexed or object_id not in _RELATIONSHIP_OBJECTS:
            raise ValueError(f"OBJECT_TREE_RELATIONSHIP_OBJECT_MISSING:{object_id}")
        row = indexed[object_id]
        row["name_zh"] = definition.get("name_zh") or row.get("name_zh")
        row["relationship_type"] = definition.get("relationship_type")
        row["endpoint_roles"] = deepcopy(definition.get("endpoint_roles", []))
        row["endpoint_binding"] = definition.get("endpoint_binding")
        row["parent_object_id"] = "relationships"
        relationship_children.append(row)
    if relationship_children:
        roots.append({
            "object_id": "relationships", "name_zh": "链路与跨对象关系",
            "kind": "relationship_group", "parent_object_id": None,
            "spatial_anchor": None, "children": relationship_children,
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
            "external_objects_are_not_spacecraft_hardware": True,
            "relationship_objects_are_not_single_equipment": True,
            "component_counts_are_inventory": False,
            "instance_count_source": "explicit_capability_or_vehicle_inventory_only",
            "unreviewed_fields_visible": False,
            "spatial_anchors_require_asset_evidence": True,
        },
    }
