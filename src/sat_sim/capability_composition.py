"""Utilities for capability dependency and composition metadata.

P7-B keeps capability composition explicit and machine-readable without adding a
new generic composition engine.  Contracts may declare dependencies, consumed
interfaces, produced interfaces, time-grid policy, and field mappings.  These
helpers normalize those declarations for the registry, compiler, manifest, and
Agent context.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _alias_for_capability(capability_id: str) -> str:
    parts = capability_id.split(".")
    if len(parts) >= 2:
        if parts[0] == "orbit_environment":
            return "orbit_environment"
        if parts[0] == "subsystem":
            return parts[1]
        if parts[0] == "component":
            return parts[1]
    return capability_id.replace(".", "_")


def normalize_dependencies(contract_data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return normalized dependency entries for a capability contract.

    Preferred P7-B shape is a top-level ``dependencies`` list.  Older contracts
    can still expose ``implementation.composition`` as a list of child
    capability IDs; this function converts that legacy shape into the same
    normalized representation.
    """

    raw = contract_data.get("dependencies")
    out: list[dict[str, Any]] = []
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        for item in raw:
            if isinstance(item, str):
                out.append({"capability_id": item, "alias": _alias_for_capability(item), "required": True})
            elif isinstance(item, Mapping):
                cid = item.get("capability_id") or item.get("id")
                if isinstance(cid, str) and cid.strip():
                    entry = dict(item)
                    entry["capability_id"] = cid.strip()
                    entry.setdefault("alias", _alias_for_capability(cid.strip()))
                    entry.setdefault("required", True)
                    out.append(entry)
        return out

    implementation = _mapping(contract_data.get("implementation"))
    legacy = implementation.get("composition")
    if isinstance(legacy, Sequence) and not isinstance(legacy, (str, bytes, bytearray)):
        for item in legacy:
            if isinstance(item, str) and item.strip():
                out.append({"capability_id": item.strip(), "alias": _alias_for_capability(item.strip()), "required": True, "source": "implementation.composition"})
    return out


def dependency_ids(contract_data: Mapping[str, Any]) -> list[str]:
    """Return child dependency capability IDs."""

    return [str(item["capability_id"]) for item in normalize_dependencies(contract_data) if item.get("capability_id")]


def trace_field_names(contract_data: Mapping[str, Any]) -> list[str]:
    """Return declared trace field names from ``outputs.trace``."""

    outputs = _mapping(contract_data.get("outputs"))
    trace = outputs.get("trace")
    names: list[str] = []
    if isinstance(trace, Sequence) and not isinstance(trace, (str, bytes, bytearray)):
        for item in trace:
            if isinstance(item, Mapping) and isinstance(item.get("name"), str):
                names.append(str(item["name"]))
    return names


def _interface_block(contract_data: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    raw = contract_data.get(key)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            out.append({"name": item})
        elif isinstance(item, Mapping):
            out.append(dict(item))
    return out


def contract_interface_summary(contract_data: Mapping[str, Any]) -> dict[str, Any]:
    """Return the P7-B Agent/registry-facing interface summary."""

    time_grid = contract_data.get("time_grid") if isinstance(contract_data.get("time_grid"), Mapping) else {}
    composition = contract_data.get("composition") if isinstance(contract_data.get("composition"), Mapping) else {}
    return {
        "dependencies": normalize_dependencies(contract_data),
        "consumes": _interface_block(contract_data, "consumes"),
        "produces": _interface_block(contract_data, "produces"),
        "time_grid": dict(time_grid),
        "composition": dict(composition),
        "trace_fields": trace_field_names(contract_data),
    }


def composition_metadata(capability_id: str, contract_data: Mapping[str, Any], *, task_spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build serializable composition metadata for compiled plans/manifests."""

    deps = normalize_dependencies(contract_data)
    composition = dict(_mapping(contract_data.get("composition")))
    time_grid = dict(_mapping(contract_data.get("time_grid")))
    consumes = _interface_block(contract_data, "consumes")
    produces = _interface_block(contract_data, "produces")
    simulation = _mapping((task_spec or {}).get("simulation"))
    resolved_time_grid = {
        "duration_s": simulation.get("duration_s"),
        "sample_s": simulation.get("sample_s"),
        "policy": time_grid.get("policy", "single_parent_time_grid"),
    }
    chain = [{"capability_id": capability_id, "role": "parent"}]
    chain.extend({"capability_id": item["capability_id"], "role": "child", "alias": item.get("alias")} for item in deps)
    return {
        "capability_id": capability_id,
        "schema_version": "p7b.composition.v1",
        "composition_type": composition.get("type", "single_capability" if not deps else "deterministic_sequential"),
        "dependencies": deps,
        "composition_chain": chain,
        "time_grid": time_grid,
        "resolved_time_grid": resolved_time_grid,
        "consumes": consumes,
        "produces": produces,
        "field_mappings": list(composition.get("field_mappings", [])) if isinstance(composition.get("field_mappings"), list) else [],
        "steps": list(composition.get("steps", [])) if isinstance(composition.get("steps"), list) else [],
    }


__all__ = [
    "composition_metadata",
    "contract_interface_summary",
    "dependency_ids",
    "normalize_dependencies",
    "trace_field_names",
]
