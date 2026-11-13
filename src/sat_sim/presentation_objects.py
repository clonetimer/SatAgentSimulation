"""Additive P9 object semantics; never a second capability or physics registry."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any, Mapping

SCHEMA_VERSION = "sat.scene-object-catalog.v1"
_COMPONENT_PARENTS = {
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
# These catalog objects are not identifiable pieces of satellite hardware.
_LOGICAL_COMPONENTS = {"data_queue", "link_budget", "power_sink", "thermal_node"}
_POLICY_MESSAGES = {
    "SUPERVISED_NATIVE_ONLY_WHOLE_CONTINUOUS": "当前运行环境仅支持整星持续实时仿真。",
    "CONTINUOUS_MODE_REQUIRES_WHOLE_UNIFIED_RUNTIME": "持续实时仿真需要选择整星。",
}


def _mode(status: str, reason: str = "") -> dict[str, Any]:
    return {
        "status": status, "selectable": status == "supported", "reason_zh": reason,
        "runtime_check_required": True,
    }


def _live_mode(capability_id: str | None, continuous: bool,
               policy: Mapping[str, Any] | None, enabled: bool) -> dict[str, Any]:
    if not capability_id:
        return _mode("not_registered", "该对象暂不支持独立实时仿真。")
    if enabled is not True or (policy is not None and "enabled" in policy and policy["enabled"] is not True):
        return _mode("service_disabled", "实时服务未开启。")
    if policy is None:
        return _mode("discovery_unavailable", "暂时无法读取实时运行方式，请重试。")
    creation = policy.get("creation")
    rows = creation if isinstance(creation, list) else []
    matches = [row for row in rows if isinstance(row, Mapping)
               and row.get("capability_id") == capability_id
               and row.get("continuous") is continuous]
    if len(matches) != 1:
        return _mode("not_registered", "该对象暂不支持此实时运行方式。")
    support = matches[0]
    if support.get("supported") is True:
        return _mode("supported")
    code = support.get("reason_code")
    result = _mode("policy_blocked", _POLICY_MESSAGES.get(code, "当前运行环境不支持此方式。"))
    result["reason_code"] = code  # Diagnostic metadata, not default UI copy.
    return result


def build_scene_object_catalog(
    presentation: Mapping[str, Any], policy: Mapping[str, Any] | None,
    *, service_enabled: bool,
) -> dict[str, Any]:
    """Project real catalog identities into a tree, preserving legacy counts.

    `selectable` is a discovery/policy result, NOT a native-runtime test result.
    No position or GLB node is assigned until an asset is independently mapped.
    """
    objects: list[dict[str, Any]] = []
    for source in presentation.get("objects", []):
        row = deepcopy(source)
        object_id, level = row["object_id"], row["level"]
        key = object_id.removeprefix("component.")
        parent = None if level == "whole_spacecraft" else "whole_spacecraft"
        if level == "component":
            if key not in _COMPONENT_PARENTS:
                raise ValueError(f"SCENE_PARENT_NOT_REVIEWED:{object_id}")
            parent = "subsystem." + _COMPONENT_PARENTS[key]
        kind = {"whole_spacecraft": "spacecraft", "orbit_environment": "environment"}.get(level, level)
        spatial_kind = "hardware"
        if level == "orbit_environment":
            spatial_kind = "trajectory"
        elif level == "subsystem" or key in _LOGICAL_COMPONENTS:
            spatial_kind = "functional_region"
        elif key == "ground_station":
            spatial_kind = "ground_site"
        row.update({
            "parent_object_id": parent, "object_kind": kind,
            "spatial": {"kind": spatial_kind, "anchor_id": None, "asset_node": None},
            "related_object_ids": [],
        })
        if object_id == "subsystem.adcs":
            row["name_zh"] = "姿态控制分系统"
        capability = row.get("primary_capability_id")
        independent = row.get("independently_executable") is True and bool(capability)
        row["configuration"] = {
            "capability_id": row.get("configuration_capability_id"),
            "scope": "independent" if independent else "parent_integration",
        }
        row["execution"] = {
            "capability_id": capability if independent else None,
            "modes": {
                "offline": _mode("supported") if independent else _mode("not_registered", "该对象暂不支持独立运行。"),
                "limited_realtime": _live_mode(capability if independent else None, False, policy, service_enabled),
                "continuous_realtime": _live_mode(capability if independent else None, True, policy, service_enabled),
            },
        }
        objects.append(row)
    objects.append({
        "object_id": "orbit_control", "name_zh": "轨道控制", "level": "functional",
        "object_kind": "function", "parent_object_id": "whole_spacecraft",
        "related_object_ids": ["orbit_environment", "subsystem.adcs", "subsystem.propulsion"],
        "actuator_dependency_ids": ["component.thruster"], "control_capability_id": None,
        "configuration": {"capability_id": None, "scope": "unavailable"},
        "spatial": {"kind": "maneuver_plan", "anchor_id": None, "asset_node": None},
        "execution": {"capability_id": None, "modes": {
            name: _mode("not_registered", "轨道控制暂不支持独立运行。")
            for name in ("offline", "limited_realtime", "continuous_realtime")
        }},
    })
    by_id = {row["object_id"]: row for row in objects}
    if len(by_id) != len(objects):
        raise ValueError("DUPLICATE_SCENE_OBJECT_ID")
    for row in objects:
        parent = row["parent_object_id"]
        visited = {row["object_id"]}
        while parent:
            if parent not in by_id or parent in visited:
                raise ValueError(f"INVALID_SCENE_PARENT:{row['object_id']}:{parent}")
            visited.add(parent)
            parent = by_id[parent]["parent_object_id"]
        row["children"] = [child["object_id"] for child in objects
                           if child["parent_object_id"] == row["object_id"]]
    return {
        "schema_version": SCHEMA_VERSION, "source_catalog_version": presentation.get("version"),
        "objects": objects, "counts": dict(Counter(row["level"] for row in objects)),
    }
