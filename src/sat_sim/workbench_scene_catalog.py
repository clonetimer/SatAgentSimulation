"""Read-only P9 scene metadata derived from the existing workbench catalog.

This layer owns display hierarchy, not capabilities, physics, or session policy.
A product category is not an installed equipment instance or a glTF mesh.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

SCENE_CATALOG_VERSION = "workbench-scene-catalog.v1"

_COMPONENT_PARENT = {
    "antenna": "comm_data", "battery": "eps", "cmg": "adcs",
    "data_queue": "comm_data", "fuel_tank": "propulsion",
    "ground_station": "comm_data", "heater": "thermal", "imu": "adcs",
    "link_budget": "comm_data", "magnetometer": "adcs", "mtb": "adcs",
    "onboard_storage": "comm_data", "payload": "payload",
    "payload_sensor": "payload", "pdu": "eps", "power_sink": "eps",
    "radiator": "thermal", "reaction_wheel": "adcs", "solar_panel": "eps",
    "star_tracker": "adcs", "sun_sensor": "adcs", "thermal_node": "thermal",
    "thruster": "propulsion", "transmitter": "comm_data",
}
_SUBSYSTEM_NAMES = {
    "adcs": "姿态控制分系统", "propulsion": "推进分系统", "eps": "电源分系统",
    "thermal": "热控分系统", "payload": "载荷分系统", "comm_data": "通信与数据管理分系统",
}
_MODE_MESSAGES = {
    "POLICY_NOT_LOADED": "正在读取实时服务状态。",
    "SERVICE_DISABLED": "实时仿真服务尚未启用，请检查启动设置。",
    "POLICY_INVALID": "实时服务返回的状态无效，请重新连接。",
    "CAPABILITY_NOT_ADVERTISED": "当前对象不支持此运行方式。",
    "NO_INDEPENDENT_CAPABILITY": "当前对象暂不支持独立仿真。",
    "CONTINUOUS_MODE_REQUIRES_WHOLE_UNIFIED_RUNTIME": "持续实时仅支持整星。",
    "SUPERVISED_NATIVE_ONLY_WHOLE_CONTINUOUS": "当前服务仅支持整星持续实时。",
}


def _mode(available: bool, reason: str | None = None) -> dict[str, Any]:
    return {
        "available": available, "reason_code": reason,
        "message_zh": "运行方式可用" if available else _MODE_MESSAGES.get(reason, "当前服务不支持此运行方式。"),
    }


def _policy_rows(policy: Mapping[str, Any] | None) -> dict[tuple[str, bool], Mapping[str, Any]]:
    if policy is None or policy.get("enabled") is not True:
        return {}
    rows = policy.get("creation")
    if not isinstance(rows, list):
        raise ValueError("SCENE_POLICY_CREATION_MUST_BE_LIST")
    result: dict[tuple[str, bool], Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("SCENE_POLICY_INVALID_ROW")
        cap = row.get("capability_id")
        if not isinstance(cap, str) or not cap.strip() or type(row.get("continuous")) is not bool or type(row.get("supported")) is not bool:
            raise ValueError("SCENE_POLICY_INVALID_ROW")
        key = (cap, row["continuous"])
        if key in result:
            raise ValueError(f"SCENE_POLICY_DUPLICATE:{cap}:{row['continuous']}")
        result[key] = row
    return result


def _live_mode(capability: str | None, continuous: bool, policy: Mapping[str, Any] | None,
               rows: Mapping[tuple[str, bool], Mapping[str, Any]]) -> dict[str, Any]:
    if capability is None:
        return _mode(False, "NO_INDEPENDENT_CAPABILITY")
    if policy is None:
        return _mode(False, "POLICY_NOT_LOADED")
    if policy.get("enabled") is not True:
        return _mode(False, "SERVICE_DISABLED" if policy.get("enabled") is False else "POLICY_INVALID")
    row = rows.get((capability, continuous))
    if row is None:
        return _mode(False, "CAPABILITY_NOT_ADVERTISED")
    return _mode(row["supported"], None if row["supported"] else str(row.get("reason_code") or "CAPABILITY_NOT_ADVERTISED"))


def _metadata(object_id: str) -> tuple[str | None, str, str, str]:
    if object_id == "whole_spacecraft":
        return None, "whole_spacecraft", "spacecraft", "physical"
    if object_id == "orbit_environment":
        return "whole_spacecraft", "environment", "orbit", "environment"
    if object_id.startswith("subsystem.") and object_id[10:] in _SUBSYSTEM_NAMES:
        return "whole_spacecraft", "subsystem", "region", "physical_group"
    if object_id.startswith("component.") and object_id[10:] in _COMPONENT_PARENT:
        key = object_id[10:]
        target, physical = "part", "equipment"
        if key == "ground_station":
            target, physical = "ground", "external_equipment"
        elif key == "link_budget":
            target, physical = "relationship", "relationship"
        elif key in {"data_queue", "thermal_node", "power_sink"}:
            target, physical = "logical", "logical"
        parent = None if key == "ground_station" else f"subsystem.{_COMPONENT_PARENT[key]}"
        return parent, "component", target, physical
    raise ValueError(f"SCENE_OBJECT_REQUIRES_REVIEW:{object_id}")


def build_scene_catalog(presentation: Mapping[str, Any],
                        policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Adapt an authoritative presentation snapshot without changing it.

    policy is the current interactive-capabilities payload, including its
    boolean enabled. Missing/disabled/denied are intentionally different.
    Available means policy permits creation; it does NOT assert a physics run.
    """
    source_rows = presentation.get("objects")
    if not isinstance(source_rows, list):
        raise ValueError("SCENE_OBJECTS_MUST_BE_LIST")
    if policy is not None and not isinstance(policy, Mapping):
        raise ValueError("SCENE_POLICY_MUST_BE_MAPPING")
    policy_rows = _policy_rows(policy)
    objects: list[dict[str, Any]] = []
    known: set[str] = set()
    for source in source_rows:
        if not isinstance(source, Mapping) or not isinstance(source.get("object_id"), str):
            raise ValueError("SCENE_INVALID_OBJECT")
        object_id = source["object_id"]
        if object_id in known:
            raise ValueError(f"SCENE_DUPLICATE_OBJECT:{object_id}")
        known.add(object_id)
        parent, kind, target, physical = _metadata(object_id)
        primary = source.get("primary_capability_id")
        configuration = source.get("configuration_capability_id")
        for value in (primary, configuration):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"SCENE_INVALID_CAPABILITY:{object_id}")
        execution = primary if source.get("independently_executable") is True else None
        name = source.get("name_zh") or object_id
        if kind == "subsystem":
            name = _SUBSYSTEM_NAMES[object_id[10:]]
        objects.append({
            "object_id": object_id, "name_zh": name,
            "parent_object_id": parent, "kind": kind, "physical_class": physical,
            "scene_target": target, "spatial_anchor": None,
            "configuration_capability_id": configuration,
            "configuration_scope": "self" if configuration and configuration == execution else "parent" if configuration else "none",
            "execution_capability_id": execution,
            "source_variants": deepcopy(source.get("variants", [])),
            "modes": {
                "offline": {"available": execution is not None,
                            "reason_code": None if execution else "NO_INDEPENDENT_CAPABILITY"},
                "limited_realtime": _live_mode(execution, False, policy, policy_rows),
                "continuous_realtime": _live_mode(execution, True, policy, policy_rows),
            },
        })
    if "whole_spacecraft" not in known:
        raise ValueError("SCENE_ROOT_MISSING")
    for row in objects:
        if row["parent_object_id"] is not None and row["parent_object_id"] not in known:
            raise ValueError(f"SCENE_PARENT_MISSING:{row['object_id']}")
    objects.append({
        "object_id": "orbit_control", "name_zh": "轨道控制",
        "parent_object_id": "whole_spacecraft", "kind": "function", "physical_class": "control_function",
        "scene_target": "orbit", "spatial_anchor": None,
        "configuration_capability_id": None, "configuration_scope": "none",
        "execution_capability_id": None, "source_variants": [],
        "modes": {name: _mode(False, "NO_INDEPENDENT_CAPABILITY")
                  for name in ("offline", "limited_realtime", "continuous_realtime")},
    })
    relationship_objects = [{
        "object_id": "component.link_budget",
        "name_zh": "星地通信链路预算",
        "relationship_type": "communication_link",
        "endpoint_roles": [
            {"role": "transmitter", "display_name_zh": "发射端", "object_ref_required": True},
            {"role": "receiver", "display_name_zh": "接收端", "object_ref_required": True},
        ],
        "endpoint_binding": "task_configuration",
        "source_capability_id": "component.link_budget.v1",
    }]
    relations = []
    for source, target, kind in (
        ("orbit_environment", "orbit_control", "state_input"),
        ("orbit_control", "subsystem.propulsion", "commands"),
        ("subsystem.propulsion", "orbit_environment", "force_feedback"),
        ("orbit_control", "subsystem.adcs", "attitude_coordination"),
    ):
        if source in known | {"orbit_control"} and target in known | {"orbit_control"}:
            relations.append({"source": source, "target": target, "kind": kind})
    return {
        "schema_version": SCENE_CATALOG_VERSION,
        "source_catalog_version": presentation.get("version"),
        "objects": objects, "counts": dict(Counter(o["kind"] for o in objects)),
        "count_semantics": "product_categories_not_installed_equipment_instances",
        "relationship_objects": relationship_objects,
        "relations": relations, "relation_semantics": "design_dependencies_not_runtime_wiring",
        "policy_semantics": "creation_permission_not_execution_evidence",
    }
