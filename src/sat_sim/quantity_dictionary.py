"""Reviewed object/quantity names with exact Schema field and unit bindings.

The first reviewed batch is the complete reaction-wheel component surface.
Other capabilities are inventoried as unreviewed, never lexically relabelled.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "sat.quantity-dictionary.v1"
RW = "component.reaction_wheel.v1"
# kind, exact source leaf, Chinese quantity, raw unit, owner scope, aggregation.
_RW_DEFINITIONS = (
    ("trace", "speed_rad_s_0", "角速度", "rad/s", "instance", "instantaneous"),
    ("trace", "applied_torque_nm_0", "实际电机力矩", "N*m", "instance", "instantaneous"),
    ("trace", "momentum_norm_nms", "合角动量模", "N*m*s", "group", "instantaneous"),
    ("trace", "rotational_energy_j", "总转动动能", "J", "group", "instantaneous"),
    ("trace", "nominal_max_speed_rad_s_0", "标称角速度上限", "rad/s", "instance", "instantaneous"),
    ("trace", "effective_max_speed_rad_s_0", "有效角速度上限", "rad/s", "instance", "instantaneous"),
    ("trace", "nominal_max_torque_nm_0", "标称电机力矩上限", "N*m", "instance", "instantaneous"),
    ("trace", "effective_max_torque_nm_0", "有效电机力矩上限", "N*m", "instance", "instantaneous"),
    ("trace", "nominal_damping_nms_0", "标称黏性阻尼系数", "N*m*s", "instance", "instantaneous"),
    ("trace", "damping_nms_0", "有效黏性阻尼系数", "N*m*s", "instance", "instantaneous"),
    ("trace", "num_wheels", "数量", "count", "group", "instantaneous"),
    ("trace", "max_abs_speed_rad_s", "最大绝对角速度", "rad/s", "group", "maximum_over_wheels"),
    ("trace", "command_torque_nm_0", "指令电机力矩", "N*m", "instance", "instantaneous"),
    ("summary", "initial_speed_rad_s_0", "初始角速度", "rad/s", "instance", "initial"),
    ("summary", "final_speed_rad_s_0", "末时刻角速度", "rad/s", "instance", "final"),
    ("summary", "max_abs_speed_rad_s", "全程最大绝对角速度", "rad/s", "group", "maximum_over_time_and_wheels"),
    ("summary", "final_momentum_norm_nms", "末时刻合角动量模", "N*m*s", "group", "final"),
    ("summary", "final_rotational_energy_j", "末时刻总转动动能", "J", "group", "final"),
    ("summary", "nominal_max_speed_rad_s_0", "末时刻标称角速度上限", "rad/s", "instance", "final"),
    ("summary", "effective_max_speed_rad_s_0", "末时刻有效角速度上限", "rad/s", "instance", "final"),
    ("summary", "nominal_max_torque_nm_0", "末时刻标称电机力矩上限", "N*m", "instance", "final"),
    ("summary", "effective_max_torque_nm_0", "末时刻有效电机力矩上限", "N*m", "instance", "final"),
    ("summary", "nominal_damping_nms_0", "末时刻标称黏性阻尼系数", "N*m*s", "instance", "final"),
    ("summary", "effective_damping_nms_0", "末时刻有效黏性阻尼系数", "N*m*s", "instance", "final"),
    ("summary", "min_speed_rad_s_0", "全程最小角速度", "rad/s", "instance", "minimum_over_time"),
    ("summary", "max_speed_rad_s_0", "全程最大角速度", "rad/s", "instance", "maximum_over_time"),
    ("parameter", "physics_backend", "计算模型", "none", "group", "configuration"),
    ("parameter", "num_wheels", "数量", "count", "group", "configuration"),
    ("parameter", "wheel_inertia_kg_m2", "转动惯量", "kg*m^2", "group", "configuration"),
    ("parameter", "max_motor_torque_nm", "电机力矩上限", "N*m", "group", "configuration"),
    ("parameter", "max_speed_rad_s", "角速度上限", "rad/s", "group", "configuration"),
    ("parameter", "damping_nms", "黏性阻尼系数", "N*m*s", "group", "configuration"),
    ("parameter", "initial_wheel_speeds_rad_s", "初始角速度", "rad/s", "group", "configuration"),
    ("parameter", "command_torque_nm", "指令电机力矩", "N*m", "group", "configuration"),
    ("parameter", "torque_profile_nm", "指令电机力矩时序", "N*m", "group", "configuration"),
)


def _reviewed_rows() -> list[dict[str, Any]]:
    result = []
    prefixes = {"trace": "adcs.reaction_wheel.", "summary": "qoi.adcs.reaction_wheel.", "parameter": "parameters.values."}
    for kind, leaf, quantity, unit, scope, aggregation in _RW_DEFINITIONS:
        owner = "反作用轮1" if scope == "instance" else "反作用轮组"
        result.append({
            "entry_id": f"{RW}:{kind}:{prefixes[kind]}{leaf}",
            "kind": kind, "source_capability_id": RW, "source_field": prefixes[kind] + leaf,
            "object_id": "component.reaction_wheel", "instance_index": 0 if scope == "instance" else None,
            "owner_scope": scope, "quantity_name_zh": quantity,
            "display_name_zh": f"{owner} · {quantity}", "unit": unit,
            "aggregation": aggregation, "review_status": "reviewed", "selectable": True,
            "visibility_tier": "diagnostic" if leaf == "physics_backend" else "common",
            "provenance": "P9-A: component.reaction_wheel.v1 + ReactionWheelAdapter + components.reaction_wheel.model",
        })
    return result


REVIEWED_QUANTITIES = _reviewed_rows()


def _source_rows(capability_id: str) -> dict[tuple[str, str], dict[str, Any]]:
    from .form_schema import capability_form_schema

    schema = capability_form_schema(capability_id)
    result = {}
    for kind, key in (("trace", "trace_options"), ("summary", "summary_options")):
        for row in schema["outputs"][key]:
            result[(kind, row["name"])] = row
    for field in schema["fields"]:
        if field["path"].startswith("parameters.values."):
            result[("parameter", field["path"])] = field
    return result


def validate_dictionary(rows: Iterable[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Verify every reviewed binding; stale fields/units must fail the gate."""
    errors, sources, seen = [], {}, set()
    for row in REVIEWED_QUANTITIES if rows is None else rows:
        cid, kind, field = row["source_capability_id"], row["kind"], row["source_field"]
        key = (cid, kind, field)
        if key in seen:
            errors.append({"code": "DUPLICATE_BINDING", "key": key})
        seen.add(key)
        if cid not in sources:
            sources[cid] = _source_rows(cid)
        source = sources[cid].get((kind, field))
        if source is None:
            errors.append({"code": "SOURCE_FIELD_MISSING", "key": key})
        elif row["unit"] != source.get("unit"):
            errors.append({"code": "UNIT_MISMATCH", "key": key, "source_unit": source.get("unit")})
    return {"ok": not errors, "reviewed_bindings": len(seen), "errors": errors}


def quantity_catalog(capability_id: str) -> dict[str, Any]:
    """Return coverage, not a false claim that every registered name is reviewed."""
    sources = _source_rows(capability_id)
    definitions = {(r["kind"], r["source_field"]): r for r in REVIEWED_QUANTITIES
                   if r["source_capability_id"] == capability_id}
    reviewed, unreviewed = [], []
    for (kind, field), source in sources.items():
        definition = definitions.get((kind, field))
        if definition is not None:
            if definition["unit"] != source.get("unit"):
                raise ValueError(f"DICTIONARY_UNIT_MISMATCH:{capability_id}:{field}")
            row = deepcopy(definition)
            row["value_type"] = source.get("type") or source.get("dtype") or "number"
            reviewed.append(row)
        else:
            unreviewed.append({
                "kind": kind, "source_capability_id": capability_id, "source_field": field,
                "unit": source.get("unit"), "display_name_zh": None,
                "review_status": "unreviewed", "selectable": False,
            })
    return {
        "schema_version": SCHEMA_VERSION, "capability_id": capability_id,
        "reviewed": reviewed, "unreviewed": unreviewed,
        "coverage": {"registered": len(sources), "reviewed": len(reviewed), "unreviewed": len(unreviewed)},
    }


def lookup_quantity(capability_id: str, kind: str, source_field: str) -> dict[str, Any] | None:
    return next((row for row in quantity_catalog(capability_id)["reviewed"]
                 if row["kind"] == kind and row["source_field"] == source_field), None)
