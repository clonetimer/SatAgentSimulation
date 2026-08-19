"""Deterministic natural-language output selection helpers.

LLMs are allowed to express output intent in user language, but concrete
telemetry fields must come from the capability registry.  This module maps a
small, auditable set of common output intents to declared trace fields and is
used by both first-turn generation and multi-turn refinements.
"""
from __future__ import annotations

import copy
import fnmatch
import re
from dataclasses import dataclass, asdict
from typing import Any, Mapping, Sequence

from .capability_registry import get_capability


@dataclass(frozen=True)
class OutputIntentResolution:
    requested_intents: tuple[str, ...]
    fields: tuple[str, ...]
    ambiguous_intents: tuple[dict[str, Any], ...]
    unsupported_intents: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.fields or self.ambiguous_intents or self.unsupported_intents)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _contains_any(text: str, words: Sequence[str]) -> bool:
    lower = text.lower()
    return any(word.lower() in lower for word in words)


def _trace_fields_for_capability(capability_id: str) -> list[str]:
    try:
        contract = get_capability(capability_id)
    except Exception:
        return []
    outputs = contract.data.get("outputs") if isinstance(contract.data.get("outputs"), Mapping) else {}
    fields: list[str] = []
    for key in ("trace_fields", "trace", "qoi"):
        rows = outputs.get(key, []) if isinstance(outputs, Mapping) else []
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            continue
        for item in rows:
            name: str | None = None
            if isinstance(item, str):
                name = item
            elif isinstance(item, Mapping) and isinstance(item.get("name"), str):
                name = str(item["name"])
            if name and name not in fields:
                fields.append(name)
    try:
        for name in contract.operator_contract.observability.trace_fields:
            if name not in fields:
                fields.append(name)
    except Exception:
        pass
    try:
        for name in contract.operator_contract.observability.qoi:
            if name not in fields:
                fields.append(name)
    except Exception:
        pass
    return fields


def _preferred(fields: Sequence[str], predicates: Sequence[tuple[str, ...]]) -> list[str]:
    lowered = [(name, name.lower()) for name in fields]
    for group in predicates:
        matches = [name for name, low in lowered if all(token in low for token in group)]
        if matches:
            return matches
    return []


def _semantic_fields(intent: str, fields: Sequence[str]) -> list[str]:
    if intent == "pointing_error":
        return _preferred(fields, (("pointing.error",), ("pointing_error",), ("attitude_error",)))
    if intent == "reaction_wheel_speed":
        return _preferred(fields, (("rw.speed_rad_s",), ("reaction_wheel", "speed"), ("wheel", "speed")))
    if intent == "reaction_wheel_torque":
        return _preferred(fields, (("rw.command_torque",), ("rw", "torque"), ("reaction_wheel", "torque")))
    if intent == "body_rate":
        return _preferred(fields, (("body_rate_rad_s",), ("omega_bn_b_rad_s",)))
    if intent == "gyro_measurement":
        return _preferred(fields, (("gyro_measured_rad_s",), ("gyro", "measured"), ("gyro", "residual")))
    if intent == "battery_soc":
        return _preferred(fields, (("battery.soc",), ("battery_soc",), ("eps.battery_soc",)))
    if intent == "final_battery_soc":
        return _preferred(fields, (("qoi", "battery", "final_soc"), ("qoi.eps.final_soc",), ("battery.soc",)))
    if intent == "minimum_power_margin":
        return _preferred(fields, (("qoi", "power", "min_margin_w"), ("power.margin_w",), ("min_margin",)))
    if intent == "payload_generated_rate":
        return _preferred(fields, (("payload.generated_bps",), ("payload", "generated", "bps")))
    if intent == "downlink_rate":
        return _preferred(fields, (("comm.downlink_bps",), ("comm.downlink_rate_bps",), ("downlink", "bps")))
    if intent == "thermal_temperature":
        return _preferred(fields, (("thermal.payload_temp",), ("thermal", "temp")))
    if intent == "data_storage":
        return _preferred(fields, (("data.storage_bits",), ("storage", "bits")))
    if intent == "propellant_remaining":
        return _preferred(fields, (("propulsion.fuel_mass_kg",), ("propellant", "mass"), ("fuel_mass",)))
    return []


def _alias_intent(value: str) -> str | None:
    raw = str(value or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", raw).strip()
    compact = normalized.replace(" ", "")
    if "battery" in compact and "soc" in compact and "final" in compact:
        return "final_battery_soc"
    if compact == "finalsoc":
        return "final_battery_soc"
    if "power" in compact and "margin" in compact and ("min" in compact or "minimum" in compact):
        return "minimum_power_margin"
    if compact == "minimumpowermargin":
        return "minimum_power_margin"
    if any(token in compact for token in ("pointingerror", "attitudeerror", "姿态误差", "指向误差")):
        return "pointing_error"
    if ("rw" in compact or "wheel" in compact or "反作用轮" in compact or "飞轮" in compact) and "speed" in compact:
        return "reaction_wheel_speed"
    if compact.startswith("rwspeed") or compact in {"轮速", "轮转速"}:
        return "reaction_wheel_speed"
    if ("rw" in compact or "wheel" in compact or "反作用轮" in compact or "飞轮" in compact) and "torque" in compact:
        return "reaction_wheel_torque"
    if "gyro" in compact or "陀螺" in compact:
        return "gyro_measurement"
    if "bodyrate" in compact or "angularrate" in compact or "机体角速度" in compact:
        return "body_rate"
    if "batterysoc" in compact or compact in {"soc", "电池soc", "荷电状态"}:
        return "battery_soc"
    if "payload" in compact and any(token in compact for token in ("generated", "datarate", "bps")):
        return "payload_generated_rate"
    if "载荷数据率" in compact or "载荷生成率" in compact:
        return "payload_generated_rate"
    if "downlink" in compact or "下行数据率" in compact or "下行速率" in compact:
        return "downlink_rate"
    if ("thermal" in compact and "temp" in compact) or "温度" in compact:
        return "thermal_temperature"
    if ("storage" in compact and "bit" in compact) or "数据存储" in compact or "存储量" in compact:
        return "data_storage"
    if "propellant" in compact or "fuelremaining" in compact or "推进剂余量" in compact or "剩余燃料" in compact:
        return "propellant_remaining"
    return None


def _declared_or_alias_fields(value: str, fields: Sequence[str]) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    if raw in fields:
        return [raw]
    matching = [
        name for name in fields
        if fnmatch.fnmatchcase(raw, name) or fnmatch.fnmatchcase(name, raw)
    ]
    if matching:
        return matching
    intent = _alias_intent(raw)
    return _semantic_fields(intent, fields) if intent else []


def _normalize_existing_outputs(out: dict[str, Any], capability_id: str) -> list[dict[str, Any]]:
    """Replace known aliases with authoritative registry recorder fields.

    Unknown fields remain in place so planning fails explicitly rather than
    silently discarding a requested output.
    """

    fields = _trace_fields_for_capability(capability_id)
    outputs = out.get("outputs")
    if not fields or not isinstance(outputs, dict):
        return []
    replacements: list[dict[str, Any]] = []
    for key in ("qoi", "plots", "record_fields"):
        rows = outputs.get(key)
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            continue
        normalized: list[str] = []
        for item in rows:
            raw = str(item)
            resolved = _declared_or_alias_fields(raw, fields)
            if resolved:
                normalized.extend(resolved)
                if resolved != [raw]:
                    replacements.append({"path": f"outputs.{key}", "from": raw, "to": list(resolved)})
            else:
                normalized.append(raw)
        outputs[key] = list(dict.fromkeys(normalized))
    return replacements


def _matching(fields: Sequence[str], *needles: str) -> list[str]:
    out: list[str] = []
    for name in fields:
        lowered = name.lower()
        if all(needle.lower() in lowered for needle in needles):
            out.append(name)
    return out


def _axis_fields(fields: Sequence[str], base: str, *, count: int | None = None) -> list[str]:
    matches = [name for name in fields if name == base or name.startswith(base + "_")]
    if count is not None:
        suffix_re = re.compile(re.escape(base) + r"_(\d+)$")
        filtered: list[str] = []
        for name in matches:
            m = suffix_re.search(name)
            if m and int(m.group(1)) < count:
                filtered.append(name)
        return filtered
    return matches


def resolve_output_intents(text: str, capability_id: str) -> OutputIntentResolution:
    """Resolve user-facing output language to capability-declared plot fields.

    The function is intentionally conservative.  Ambiguous requests, such as
    ``姿态角``, are recorded as ambiguity metadata and mapped to the safest
    currently available observable rather than silently renaming quaternion
    components to Euler angles.
    """

    raw = str(text or "")
    fields = _trace_fields_for_capability(capability_id)
    selected: list[str] = []
    intents: list[str] = []
    ambiguous: list[dict[str, Any]] = []
    unsupported: list[str] = []

    def add(intent: str, values: Sequence[str]) -> None:
        intents.append(intent)
        for value in values:
            if value in fields and value not in selected:
                selected.append(value)

    if _contains_any(raw, ["轮速", "轮转速", "飞轮转速", "反作用轮转速", "动量轮转速", "wheel speed", "rw speed", "reaction wheel speed"]):
        candidates = _semantic_fields("reaction_wheel_speed", fields)
        add("reaction_wheel_speed", candidates)
        if not candidates:
            unsupported.append("reaction_wheel_speed")

    if _contains_any(raw, ["轮力矩", "飞轮力矩", "反作用轮力矩", "电机力矩", "wheel torque", "motor torque"]):
        candidates = _semantic_fields("reaction_wheel_torque", fields)
        add("reaction_wheel_torque", candidates)
        if not candidates:
            unsupported.append("reaction_wheel_torque")

    if _contains_any(raw, ["姿态误差", "指向误差", "pointing error", "attitude error"]):
        candidates = _semantic_fields("pointing_error", fields)
        add("pointing_error", candidates[:4])
        if not candidates:
            unsupported.append("pointing_error")

    if _contains_any(raw, ["姿态四元数", "四元数", "quaternion", "q_bn"]):
        candidates = _axis_fields(fields, "adcs.attitude.q_bn", count=4)
        add("attitude_quaternion", candidates)
        if not candidates:
            unsupported.append("attitude_quaternion")

    if _contains_any(raw, ["姿态角", "欧拉角", "俯仰", "滚转", "偏航", "euler", "roll", "pitch", "yaw"]):
        euler_candidates = [name for name in fields if any(token in name.lower() for token in ("euler", "roll", "pitch", "yaw"))]
        if euler_candidates:
            add("euler_attitude_angles", euler_candidates)
        else:
            fallback = _semantic_fields("pointing_error", fields)
            add("attitude_angle_ambiguous", fallback)
            ambiguous.append({
                "intent": "姿态角/欧拉角",
                "reason": "当前能力未声明欧拉角曲线；已选择姿态指向误差角作为安全替代。需要姿态四元数时请明确说“姿态四元数”。",
                "fallback_fields": fallback,
                "available_alternatives": [name for name in fields if name.startswith("adcs.attitude.q_bn_")][:4],
            })
            if not fallback:
                unsupported.append("euler_attitude_angles")

    if _contains_any(raw, ["角速度", "机体角速度", "body rate", "angular rate", "omega"]):
        candidates = _semantic_fields("body_rate", fields)
        add("body_rate", candidates)
        if not candidates:
            unsupported.append("body_rate")

    if _contains_any(raw, ["陀螺", "gyro"]):
        candidates = _semantic_fields("gyro_measurement", fields)
        add("gyro_measurement", candidates)
        if not candidates:
            unsupported.append("gyro_measurement")

    if _contains_any(raw, ["电池soc", "电池 soc", "荷电状态", "battery soc"]):
        candidates = _semantic_fields("battery_soc", fields)
        add("battery_soc", candidates)
        if not candidates:
            unsupported.append("battery_soc")

    if _contains_any(raw, ["final battery soc", "final soc", "battery final soc"]):
        candidates = _semantic_fields("final_battery_soc", fields)
        add("final_battery_soc", candidates)
        if not candidates:
            unsupported.append("final_battery_soc")

    if _contains_any(raw, ["minimum power margin", "min power margin", "power min margin"]):
        candidates = _semantic_fields("minimum_power_margin", fields)
        add("minimum_power_margin", candidates)
        if not candidates:
            unsupported.append("minimum_power_margin")

    if _contains_any(raw, ["载荷数据率", "载荷生成率", "payload data rate", "payload generated"]):
        candidates = _semantic_fields("payload_generated_rate", fields)
        add("payload_generated_rate", candidates)
        if not candidates:
            unsupported.append("payload_generated_rate")

    if _contains_any(raw, ["下行数据率", "下行速率", "downlink rate", "downlink delivered"]):
        candidates = _semantic_fields("downlink_rate", fields)
        add("downlink_rate", candidates)
        if not candidates:
            unsupported.append("downlink_rate")

    if _contains_any(raw, ["温度", "热控温度", "thermal temperature"]):
        candidates = _semantic_fields("thermal_temperature", fields)
        add("thermal_temperature", candidates)
        if not candidates:
            unsupported.append("thermal_temperature")

    if _contains_any(raw, ["存储量", "数据存储", "storage bits", "data storage"]):
        candidates = _semantic_fields("data_storage", fields)
        add("data_storage", candidates)
        if not candidates:
            unsupported.append("data_storage")

    return OutputIntentResolution(
        requested_intents=tuple(dict.fromkeys(intents)),
        fields=tuple(dict.fromkeys(selected)),
        ambiguous_intents=tuple(ambiguous),
        unsupported_intents=tuple(dict.fromkeys(unsupported)),
    )


def apply_output_intents_to_spec(spec: Mapping[str, Any], instruction: str) -> tuple[dict[str, Any], OutputIntentResolution]:
    """Return a copy of ``spec`` with resolved plots/qoi appended."""

    out = copy.deepcopy(dict(spec))
    model = out.get("model") if isinstance(out.get("model"), Mapping) else {}
    capability_id = str(model.get("capability_id") or out.get("capability_id") or "")
    alias_replacements = _normalize_existing_outputs(out, capability_id) if capability_id else []
    resolution = resolve_output_intents(instruction, capability_id) if capability_id else OutputIntentResolution((), (), (), ())
    if resolution.fields:
        outputs = out.setdefault("outputs", {})
        if isinstance(outputs, dict):
            for key in ("plots", "qoi"):
                current = list(outputs.get(key) or [])
                outputs[key] = list(dict.fromkeys([*current, *resolution.fields]))
    if resolution.changed or alias_replacements:
        metadata = out.setdefault("metadata", {})
        if isinstance(metadata, dict):
            agent = metadata.setdefault("agent", {})
            if isinstance(agent, dict):
                agent["output_intent_resolution"] = resolution.to_dict()
                if alias_replacements:
                    agent["output_alias_replacements"] = alias_replacements
    return out, resolution


__all__ = [
    "OutputIntentResolution",
    "resolve_output_intents",
    "apply_output_intents_to_spec",
]
