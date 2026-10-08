"""Registered module-interface contracts, module library, and insertion policy.

V7 extends the V6 dependency-substitution layer with parent-managed internal slots.
A parent Capability may expose named module slots under ``simulation_ports.module_slots``.
A candidate is eligible only when it is explicitly allow-listed, active and runnable,
and its ``simulation_module_interface`` matches the parent slot's mapped formal ports.

This module never imports or executes candidate adapter class paths.  Runtime
selection remains owned by the registered parent adapter.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

from .capability_registry import CapabilityContract, get_capability

MODULE_INTERFACE_SCHEMA_VERSION = "sat-sim.module-interface.v1"
MODULE_REPLACEMENT_SCHEMA_VERSION = "sat-sim.module-replacements.v2"
LEGACY_MODULE_REPLACEMENT_SCHEMA_VERSION = "sat-sim.module-replacements.v1"
MODULE_LIBRARY_SCHEMA_VERSION = "sat-sim.module-library.v1"
MAX_MODULE_SLOTS = 32
MAX_REPLACEMENT_CANDIDATES = 32


@dataclass(frozen=True)
class ModuleReplacementIssue:
    code: str
    message: str
    path: str = "$.simulation_ports.module_slots"
    severity: str = "error"
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.details is None:
            payload.pop("details")
        return payload


def _issue(
    issues: list[ModuleReplacementIssue],
    code: str,
    message: str,
    *,
    path: str = "$.simulation_ports.module_slots",
    severity: str = "error",
    details: dict[str, Any] | None = None,
) -> None:
    issues.append(ModuleReplacementIssue(code, message, path, severity, details))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def _normalized_payload(value: Any) -> dict[str, Any]:
    payload = _mapping(value)
    return {
        "schema_id": str(payload.get("schema_id") or ""),
        "dtype": str(payload.get("dtype") or ""),
        "shape": str(payload.get("shape") or ""),
        "unit": str(payload.get("unit") or ""),
    }


def _normalized_timing(value: Any) -> dict[str, Any]:
    timing = _mapping(value)
    return {
        "domain": str(timing.get("domain") or ""),
        "rate_policy": str(timing.get("rate_policy") or ""),
        "sample_period_source": str(timing.get("sample_period_source") or ""),
    }


def _fingerprint(payload: Mapping[str, Any]) -> str:
    """Fingerprint only executable replacement semantics, not display metadata."""

    normalized_slots: list[dict[str, Any]] = []
    for raw_slot in _sequence(payload.get("slots")):
        if not isinstance(raw_slot, Mapping):
            continue
        candidates = []
        for raw_candidate in _sequence(raw_slot.get("candidates")):
            if not isinstance(raw_candidate, Mapping):
                continue
            candidates.append({
                "capability_id": str(raw_candidate.get("capability_id") or ""),
                "interface_id": str(raw_candidate.get("interface_id") or ""),
                "compatible": bool(raw_candidate.get("compatible")),
                "is_baseline": bool(raw_candidate.get("is_baseline")),
                "issue_codes": sorted(
                    str(issue.get("code") or "")
                    for issue in _sequence(raw_candidate.get("issues"))
                    if isinstance(issue, Mapping)
                ),
            })
        candidates.sort(key=lambda item: item["capability_id"])
        normalized_slots.append({
            "module_alias": str(raw_slot.get("module_alias") or ""),
            "interface_id": str(raw_slot.get("interface_id") or ""),
            "baseline_capability_id": str(raw_slot.get("baseline_capability_id") or ""),
            "replacement_policy": str(raw_slot.get("replacement_policy") or ""),
            "runtime_selector": str(raw_slot.get("runtime_selector") or ""),
            "slot_kind": str(raw_slot.get("slot_kind") or "dependency"),
            "port_map": dict(_mapping(raw_slot.get("port_map"))),
            "replaceable": bool(raw_slot.get("replaceable")),
            "candidates": candidates,
        })
    normalized_slots.sort(key=lambda item: item["module_alias"])
    core = {
        "schema_version": payload.get("schema_version"),
        "parent_capability_id": payload.get("parent_capability_id"),
        "slots": normalized_slots,
    }
    raw = json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(raw).hexdigest()


def normalize_module_interface(contract: CapabilityContract) -> dict[str, Any]:
    """Return a normalized standalone module interface for one Capability."""

    raw = _mapping(contract.data.get("simulation_module_interface"))
    schema_version = str(raw.get("schema_version") or "")
    interface_id = str(raw.get("interface_id") or "").strip()
    issues: list[ModuleReplacementIssue] = []
    if not raw:
        _issue(issues, "MODULE_INTERFACE_MISSING", f"Capability {contract.capability_id!r} has no simulation_module_interface.", path="$.simulation_module_interface")
    elif schema_version != MODULE_INTERFACE_SCHEMA_VERSION:
        _issue(
            issues,
            "MODULE_INTERFACE_SCHEMA_UNSUPPORTED",
            f"Capability {contract.capability_id!r} uses unsupported module-interface schema {schema_version!r}.",
            path="$.simulation_module_interface.schema_version",
        )
    if not interface_id:
        _issue(issues, "MODULE_INTERFACE_ID_MISSING", "simulation_module_interface.interface_id is required.", path="$.simulation_module_interface.interface_id")

    ports: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(_sequence(raw.get("ports"))):
        path = f"$.simulation_module_interface.ports[{index}]"
        if not isinstance(item, Mapping):
            _issue(issues, "MODULE_INTERFACE_PORT_INVALID", "Module-interface port must be an object.", path=path)
            continue
        port_id = str(item.get("port_id") or "").strip()
        direction = str(item.get("direction") or "").strip()
        if not port_id:
            _issue(issues, "MODULE_INTERFACE_PORT_ID_MISSING", "Module-interface port_id is required.", path=f"{path}.port_id")
            continue
        if port_id in by_id:
            _issue(issues, "MODULE_INTERFACE_PORT_DUPLICATE", f"Duplicate module-interface port {port_id!r}.", path=f"{path}.port_id")
            continue
        if direction not in {"input", "output"}:
            _issue(issues, "MODULE_INTERFACE_PORT_DIRECTION_INVALID", "Module-interface direction must be input or output.", path=f"{path}.direction")
        payload = _normalized_payload(item.get("payload"))
        timing = _normalized_timing(item.get("timing"))
        if not all(payload.values()):
            _issue(issues, "MODULE_INTERFACE_PAYLOAD_INCOMPLETE", "Module-interface payload must declare schema_id/dtype/shape/unit.", path=f"{path}.payload")
        if not all(timing.values()):
            _issue(issues, "MODULE_INTERFACE_TIMING_INCOMPLETE", "Module-interface timing must declare domain/rate_policy/sample_period_source.", path=f"{path}.timing")
        if direction == "input":
            if "fan_in" not in item or str(item.get("fan_in") or "") not in {"one", "many"}:
                _issue(issues, "MODULE_INTERFACE_FANIN_MISSING", "Input module-interface port must explicitly declare fan_in=one|many.", path=f"{path}.fan_in")
        elif direction == "output":
            if "direct_feedthrough" not in item or not isinstance(item.get("direct_feedthrough"), bool):
                _issue(issues, "MODULE_INTERFACE_FEEDTHROUGH_MISSING", "Output module-interface port must explicitly declare boolean direct_feedthrough.", path=f"{path}.direct_feedthrough")
            if "fan_out" not in item or str(item.get("fan_out") or "") not in {"one", "many"}:
                _issue(issues, "MODULE_INTERFACE_FANOUT_MISSING", "Output module-interface port must explicitly declare fan_out=one|many.", path=f"{path}.fan_out")
        port = {
            "port_id": port_id,
            "direction": direction,
            "payload": payload,
            "timing": timing,
            "direct_feedthrough": bool(item.get("direct_feedthrough", False)),
            "fan_in": str(item.get("fan_in") or "one"),
            "fan_out": str(item.get("fan_out") or "many"),
            "description": str(item.get("description") or ""),
        }
        ports.append(port)
        by_id[port_id] = port

    return {
        "schema_version": MODULE_INTERFACE_SCHEMA_VERSION,
        "interface_id": interface_id,
        "ports": ports,
        "validation": {
            "ok": not any(issue.severity == "error" for issue in issues),
            "issues": [issue.to_dict() for issue in issues],
            "errors": [issue.to_dict() for issue in issues if issue.severity == "error"],
            "warnings": [issue.to_dict() for issue in issues if issue.severity != "error"],
        },
    }


def _candidate_compatibility(
    capability_id: str,
    *,
    slot: Mapping[str, Any],
    parent_ports: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    issues: list[ModuleReplacementIssue] = []
    try:
        candidate = get_capability(capability_id)
    except Exception as exc:
        _issue(issues, "MODULE_REPLACEMENT_CAPABILITY_UNKNOWN", str(exc), details={"capability_id": capability_id})
        return {"capability_id": capability_id, "name": capability_id, "compatible": False, "issues": [item.to_dict() for item in issues]}

    baseline_capability_id = str(slot.get("baseline_capability_id") or "")
    if not candidate.is_active and capability_id != baseline_capability_id:
        _issue(issues, "MODULE_REPLACEMENT_CAPABILITY_INACTIVE", f"Candidate {capability_id!r} is not active.")
    adapter = _mapping(candidate.data.get("adapter"))
    if not bool(adapter.get("runtime_run", False)):
        _issue(issues, "MODULE_REPLACEMENT_NOT_RUNNABLE", f"Candidate {capability_id!r} is not runtime-runnable.")

    interface = normalize_module_interface(candidate)
    for raw_issue in _sequence(_mapping(interface.get("validation")).get("issues")):
        if isinstance(raw_issue, Mapping):
            _issue(
                issues,
                str(raw_issue.get("code") or "MODULE_INTERFACE_INVALID"),
                str(raw_issue.get("message") or "Candidate module interface is invalid."),
                path=str(raw_issue.get("path") or "$.simulation_module_interface"),
                severity=str(raw_issue.get("severity") or "error"),
                details=dict(raw_issue.get("details")) if isinstance(raw_issue.get("details"), Mapping) else None,
            )
    required_interface = str(slot.get("interface_id") or "")
    if str(interface.get("interface_id") or "") != required_interface:
        _issue(
            issues,
            "MODULE_INTERFACE_ID_MISMATCH",
            f"Candidate {capability_id!r} does not implement interface {required_interface!r}.",
            details={"required": required_interface, "actual": interface.get("interface_id")},
        )

    candidate_ports = {str(item.get("port_id")): item for item in _sequence(interface.get("ports")) if isinstance(item, Mapping)}
    port_map = _mapping(slot.get("port_map"))
    for interface_port_id, parent_port_id in port_map.items():
        candidate_port = candidate_ports.get(str(interface_port_id))
        parent_port = parent_ports.get(str(parent_port_id))
        path = f"$.simulation_ports.module_slots[{slot.get('module_alias')}].port_map.{interface_port_id}"
        if candidate_port is None:
            _issue(issues, "MODULE_INTERFACE_REQUIRED_PORT_MISSING", f"Candidate {capability_id!r} does not expose interface port {interface_port_id!r}.", path=path)
            continue
        if parent_port is None:
            _issue(issues, "MODULE_SLOT_PARENT_PORT_UNKNOWN", f"Parent slot maps to unknown simulation port {parent_port_id!r}.", path=path)
            continue
        for key, expected, actual in (
            ("direction", parent_port.get("direction"), candidate_port.get("direction")),
            ("payload", _normalized_payload(parent_port.get("payload")), _normalized_payload(candidate_port.get("payload"))),
            ("timing", _normalized_timing(parent_port.get("timing")), _normalized_timing(candidate_port.get("timing"))),
        ):
            if expected != actual:
                _issue(
                    issues,
                    f"MODULE_INTERFACE_{key.upper()}_MISMATCH",
                    f"Candidate {capability_id!r} port {interface_port_id!r} is incompatible with parent port {parent_port_id!r} ({key}).",
                    path=path,
                    details={"expected": expected, "actual": actual},
                )
        if str(parent_port.get("direction")) == "output":
            if bool(parent_port.get("direct_feedthrough", False)) != bool(candidate_port.get("direct_feedthrough", False)):
                _issue(issues, "MODULE_INTERFACE_FEEDTHROUGH_MISMATCH", f"Candidate {capability_id!r} changes direct-feedthrough semantics for {interface_port_id!r}.", path=path)
            required_fan_out = str(parent_port.get("fan_out") or "many")
            actual_fan_out = str(candidate_port.get("fan_out") or "many")
            if required_fan_out == "many" and actual_fan_out != "many":
                _issue(issues, "MODULE_INTERFACE_FANOUT_INCOMPATIBLE", f"Candidate {capability_id!r} cannot satisfy required fan-out for {interface_port_id!r}.", path=path)
        else:
            required_fan_in = str(parent_port.get("fan_in") or "one")
            actual_fan_in = str(candidate_port.get("fan_in") or "one")
            if required_fan_in == "many" and actual_fan_in != "many":
                _issue(issues, "MODULE_INTERFACE_FANIN_INCOMPATIBLE", f"Candidate {capability_id!r} cannot satisfy required fan-in for {interface_port_id!r}.", path=path)

    return {
        "capability_id": capability_id,
        "name": str(candidate.data.get("name") or capability_id),
        "domain": str(candidate.data.get("domain") or candidate.target_name or ""),
        "interface_id": str(interface.get("interface_id") or ""),
        "compatible": not any(issue.severity == "error" for issue in issues),
        "issues": [item.to_dict() for item in issues],
    }


def build_module_replacement_contract(
    parent: CapabilityContract,
    modules: list[dict[str, Any]],
    simulation_ports: Mapping[str, Any],
) -> dict[str, Any]:
    """Build normalized trusted replacement slots for a parent Capability."""

    raw_simulation_ports = _mapping(parent.data.get("simulation_ports"))
    raw_slots = _sequence(raw_simulation_ports.get("module_slots"))
    issues: list[ModuleReplacementIssue] = []
    if len(raw_slots) > MAX_MODULE_SLOTS:
        _issue(issues, "MODULE_SLOT_LIMIT_EXCEEDED", f"At most {MAX_MODULE_SLOTS} module slots are allowed.")
        raw_slots = raw_slots[:MAX_MODULE_SLOTS]

    module_by_alias = {str(item.get("alias") or ""): item for item in modules}
    parent_ports = {str(item.get("port_id") or ""): item for item in _sequence(simulation_ports.get("ports")) if isinstance(item, Mapping)}
    slots: list[dict[str, Any]] = []
    seen_aliases: set[str] = set()
    for index, raw in enumerate(raw_slots):
        path = f"$.simulation_ports.module_slots[{index}]"
        if not isinstance(raw, Mapping):
            _issue(issues, "MODULE_SLOT_INVALID", "Module slot must be an object.", path=path)
            continue
        alias = str(raw.get("module_alias") or "").strip()
        interface_id = str(raw.get("interface_id") or "").strip()
        baseline = str(raw.get("baseline_capability_id") or "").strip()
        policy = str(raw.get("replacement_policy") or "").strip()
        runtime_selector = str(raw.get("runtime_selector") or "").strip()
        if not alias or alias not in module_by_alias:
            _issue(issues, "MODULE_SLOT_ALIAS_UNKNOWN", f"Module slot alias {alias!r} is not part of the parent assembly.", path=f"{path}.module_alias")
            continue
        if alias in seen_aliases:
            _issue(issues, "MODULE_SLOT_ALIAS_DUPLICATE", f"Duplicate module slot alias {alias!r}.", path=f"{path}.module_alias")
            continue
        seen_aliases.add(alias)
        module = module_by_alias[alias]
        role = str(module.get("role") or "")
        slot_kind = str(raw.get("slot_kind") or ("dependency" if role == "dependency" else "parent_managed_internal")).strip()
        if slot_kind not in {"dependency", "parent_managed_internal"}:
            _issue(issues, "MODULE_SLOT_KIND_UNSUPPORTED", "slot_kind must be 'dependency' or 'parent_managed_internal'.", path=f"{path}.slot_kind")
        if role == "dependency":
            if slot_kind != "dependency":
                _issue(issues, "MODULE_SLOT_KIND_ROLE_MISMATCH", "Dependency modules must use slot_kind='dependency'.", path=f"{path}.slot_kind")
            if baseline != str(module.get("capability_id") or ""):
                _issue(issues, "MODULE_SLOT_BASELINE_MISMATCH", f"Slot baseline {baseline!r} does not match registered dependency {module.get('capability_id')!r}.", path=f"{path}.baseline_capability_id")
        elif role == "internal":
            if slot_kind != "parent_managed_internal":
                _issue(issues, "MODULE_SLOT_KIND_ROLE_MISMATCH", "Parent-owned internal modules must use slot_kind='parent_managed_internal'.", path=f"{path}.slot_kind")
            if baseline:
                _issue(issues, "MODULE_SLOT_INTERNAL_BASELINE_MUST_BE_EMPTY", "Parent-managed internal slots preserve the parent implementation as the baseline and must not declare baseline_capability_id.", path=f"{path}.baseline_capability_id")
        else:
            _issue(issues, "MODULE_SLOT_ROLE_UNSUPPORTED", "Only dependency or parent-owned internal modules may be exposed as replacement slots.", path=f"{path}.module_alias")
        if not interface_id:
            _issue(issues, "MODULE_SLOT_INTERFACE_MISSING", "Module slot interface_id is required.", path=f"{path}.interface_id")
        if policy != "allowlist-compatible":
            _issue(issues, "MODULE_SLOT_POLICY_UNSUPPORTED", "V7 replacement_policy must be 'allowlist-compatible'.", path=f"{path}.replacement_policy")
        if not runtime_selector:
            _issue(issues, "MODULE_SLOT_RUNTIME_SELECTOR_MISSING", "V7 module slot must declare runtime_selector.", path=f"{path}.runtime_selector")
        port_map = dict(_mapping(raw.get("port_map")))
        if not port_map:
            _issue(issues, "MODULE_SLOT_PORT_MAP_MISSING", "Module slot must map at least one module-interface port to a parent simulation port.", path=f"{path}.port_map")
        for parent_port_id in port_map.values():
            parent_port = parent_ports.get(str(parent_port_id))
            if parent_port is None:
                _issue(issues, "MODULE_SLOT_PARENT_PORT_UNKNOWN", f"Slot {alias!r} references unknown parent port {parent_port_id!r}.", path=f"{path}.port_map")
            elif str(parent_port.get("module_alias") or "") != alias:
                _issue(issues, "MODULE_SLOT_PORT_ALIAS_MISMATCH", f"Parent port {parent_port_id!r} belongs to module {parent_port.get('module_alias')!r}, not slot {alias!r}.", path=f"{path}.port_map")

        allowed = [str(item).strip() for item in _sequence(raw.get("allowed_capability_ids")) if str(item).strip()]
        if len(set(allowed)) != len(allowed):
            _issue(issues, "MODULE_REPLACEMENT_CANDIDATE_DUPLICATE", f"Slot {alias!r} contains duplicate allowed_capability_ids.", path=f"{path}.allowed_capability_ids")
            allowed = list(dict.fromkeys(allowed))
        if not allowed:
            _issue(issues, "MODULE_REPLACEMENT_CANDIDATES_MISSING", f"Slot {alias!r} must declare at least one allowed capability.", path=f"{path}.allowed_capability_ids")
        if baseline and baseline not in allowed:
            allowed.insert(0, baseline)
        if len(allowed) > MAX_REPLACEMENT_CANDIDATES:
            _issue(issues, "MODULE_REPLACEMENT_CANDIDATE_LIMIT_EXCEEDED", f"Slot {alias!r} supports at most {MAX_REPLACEMENT_CANDIDATES} candidates.", path=f"{path}.allowed_capability_ids")
            allowed = allowed[:MAX_REPLACEMENT_CANDIDATES]
        candidates = [
            _candidate_compatibility(capability_id, slot={"module_alias": alias, "interface_id": interface_id, "baseline_capability_id": baseline, "port_map": port_map}, parent_ports=parent_ports)
            for capability_id in allowed
        ]
        for candidate in candidates:
            candidate["is_baseline"] = candidate["capability_id"] == baseline
        baseline_candidate = next((item for item in candidates if item.get("is_baseline")), None)
        if baseline and (baseline_candidate is None or not baseline_candidate.get("compatible")):
            _issue(issues, "MODULE_SLOT_BASELINE_INTERFACE_INVALID", f"Baseline capability {baseline!r} does not satisfy slot interface {interface_id!r}.", path=path)
        slots.append({
            "module_alias": alias,
            "interface_id": interface_id,
            "baseline_capability_id": baseline or None,
            "replacement_policy": policy,
            "runtime_selector": runtime_selector,
            "slot_kind": slot_kind,
            "port_map": port_map,
            "candidates": candidates,
            "replaceable": any(item.get("compatible") and not item.get("is_baseline") for item in candidates),
        })

    payload = {
        "schema_version": MODULE_REPLACEMENT_SCHEMA_VERSION,
        "parent_capability_id": parent.capability_id,
        "source": "explicit-v7" if raw_slots else "none",
        "slots": slots,
    }
    payload["fingerprint"] = _fingerprint(payload)
    payload["validation"] = {
        "ok": not any(issue.severity == "error" for issue in issues),
        "issues": [item.to_dict() for item in issues],
        "errors": [item.to_dict() for item in issues if item.severity == "error"],
        "warnings": [item.to_dict() for item in issues if item.severity != "error"],
    }
    return payload



def module_library_catalog() -> dict[str, Any]:
    """Return active runtime-runnable capabilities that expose a valid module interface.

    The catalog is intentionally interface-centric.  Inclusion does not mean a module
    may be inserted anywhere; parent replacement slots remain the authority that
    decides whether a candidate is compatible and executable in a given assembly.
    """

    from .capability_registry import list_capabilities

    modules: list[dict[str, Any]] = []
    for contract in list_capabilities():
        if not contract.is_active:
            continue
        adapter = _mapping(contract.data.get("adapter"))
        if not bool(adapter.get("runtime_run", False)):
            continue
        if not _mapping(contract.data.get("simulation_module_interface")):
            continue
        interface = normalize_module_interface(contract)
        validation = _mapping(interface.get("validation"))
        modules.append({
            "capability_id": contract.capability_id,
            "name": str(contract.data.get("name") or contract.capability_id),
            "domain": str(contract.data.get("domain") or contract.target_name or ""),
            "level": contract.target_level,
            "interface_id": str(interface.get("interface_id") or ""),
            "ports": [dict(item) for item in _sequence(interface.get("ports")) if isinstance(item, Mapping)],
            "compatible_contract": bool(validation.get("ok", False)),
            "validation": dict(validation),
            "product_tier": contract.product_tier,
        })
    modules.sort(key=lambda item: (item["domain"], item["interface_id"], item["capability_id"]))
    return {
        "schema_version": MODULE_LIBRARY_SCHEMA_VERSION,
        "module_count": len(modules),
        "modules": modules,
    }

def compatible_candidate(slot: Mapping[str, Any], capability_id: str) -> Mapping[str, Any] | None:
    for candidate in _sequence(slot.get("candidates")):
        if isinstance(candidate, Mapping) and str(candidate.get("capability_id") or "") == capability_id and bool(candidate.get("compatible")):
            return candidate
    return None


__all__ = [
    "MODULE_INTERFACE_SCHEMA_VERSION",
    "MODULE_REPLACEMENT_SCHEMA_VERSION",
    "LEGACY_MODULE_REPLACEMENT_SCHEMA_VERSION",
    "MODULE_LIBRARY_SCHEMA_VERSION",
    "ModuleReplacementIssue",
    "normalize_module_interface",
    "build_module_replacement_contract",
    "module_library_catalog",
    "compatible_candidate",
]
