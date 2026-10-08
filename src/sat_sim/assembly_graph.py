"""Registered multi-module visual assembly contracts and validation.

V5 builds on the registered V4 multi-module graph with a formal
``simulation_ports`` contract.  Only dependency modules and signal bindings
declared by a parent Capability contract are executable.  The parent adapter
remains the runtime owner; port schema, units, timing, feedthrough/state ownership
and solver policy are machine-readable metadata used to fail closed before code
generation or execution.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Mapping, Sequence

from .capability_registry import CapabilityContract, get_capability, list_capabilities
from .simulation_ports import SIMULATION_PORT_SCHEMA_VERSION, build_simulation_port_contract
from .module_replacements import (
    MODULE_REPLACEMENT_SCHEMA_VERSION,
    build_module_replacement_contract,
    compatible_candidate,
)

ASSEMBLY_GRAPH_SCHEMA_VERSION = "sat-sim.module-assembly.v4"
V6_ASSEMBLY_GRAPH_SCHEMA_VERSION = "sat-sim.module-assembly.v3"
V5_ASSEMBLY_GRAPH_SCHEMA_VERSION = "sat-sim.module-assembly.v2"
LEGACY_ASSEMBLY_GRAPH_SCHEMA_VERSION = "sat-sim.module-assembly.v1"
ASSEMBLY_CATALOG_SCHEMA_VERSION = "sat-sim.module-assembly-catalog.v4"
ASSEMBLY_RUNTIME_SCHEMA_VERSION = "sat-sim.assembly-runtime.v1"
MAX_ASSEMBLY_NODES = 64
MAX_ASSEMBLY_EDGES = 160
MAX_ASSEMBLY_ID_LENGTH = 160
_PARENT_ALIAS = "assembly"
_PARENT_SOURCE_PREFIXES = {"TaskSpec", "parameters", "simulation", "spacecraft"}
_ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class AssemblyIssue:
    code: str
    message: str
    path: str = "$.assembly_graph"
    severity: str = "error"
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.details is None:
            payload.pop("details")
        return payload


@dataclass(frozen=True)
class AssemblyValidation:
    ok: bool
    schema_version: str
    parent_capability_id: str
    issues: list[AssemblyIssue]
    module_order: list[str]
    feedback_groups: list[list[str]]
    node_count: int
    edge_count: int
    required_binding_count: int
    port_contract_source: str = ""
    port_contract_fingerprint: str = ""
    solver_policies: tuple[str, ...] = ()
    timing_policies: tuple[str, ...] = ()
    module_contract_fingerprint: str = ""
    replacement_count: int = 0
    selected_modules: dict[str, str | None] | None = None

    @property
    def errors(self) -> list[AssemblyIssue]:
        return [item for item in self.issues if item.severity == "error"]

    @property
    def warnings(self) -> list[AssemblyIssue]:
        return [item for item in self.issues if item.severity != "error"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "schema_version": self.schema_version,
            "parent_capability_id": self.parent_capability_id,
            "issues": [item.to_dict() for item in self.issues],
            "errors": [item.to_dict() for item in self.errors],
            "warnings": [item.to_dict() for item in self.warnings],
            "module_order": list(self.module_order),
            "feedback_groups": [list(group) for group in self.feedback_groups],
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "required_binding_count": self.required_binding_count,
            "port_contract_source": self.port_contract_source,
            "port_contract_fingerprint": self.port_contract_fingerprint,
            "solver_policies": list(self.solver_policies),
            "timing_policies": list(self.timing_policies),
            "module_contract_fingerprint": self.module_contract_fingerprint,
            "replacement_count": self.replacement_count,
            "selected_modules": dict(self.selected_modules or {}),
        }


def _issue(
    issues: list[AssemblyIssue],
    code: str,
    message: str,
    *,
    path: str = "$.assembly_graph",
    severity: str = "error",
    details: dict[str, Any] | None = None,
) -> None:
    issues.append(AssemblyIssue(code=code, message=message, path=path, severity=severity, details=details))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def _field_mapping_paths(raw: Mapping[str, Any]) -> tuple[str, str]:
    source = raw.get("source") if isinstance(raw.get("source"), str) else raw.get("from")
    target = raw.get("target") if isinstance(raw.get("target"), str) else raw.get("to")
    return str(source or "").strip(), str(target or "").strip()


def _first_token(path: str) -> str | None:
    if not path:
        return None
    token = path.split(".", 1)[0].strip()
    return token if _ALIAS_RE.fullmatch(token) else None


def _safe_id(value: str) -> str:
    cooked = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return cooked or "module"


def _contract_modules(contract: CapabilityContract, raw_mappings: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = [{
        "alias": _PARENT_ALIAS,
        "role": "parent",
        "capability_id": contract.capability_id,
        "name": str(contract.data.get("name") or contract.capability_id),
        "domain": str(contract.data.get("domain") or contract.target_name or "assembly"),
        "replaceable": False,
        "runtime_owner": True,
    }]
    known = {_PARENT_ALIAS}
    for dep in contract.dependencies:
        alias = str(dep.get("alias") or "").strip()
        capability_id = str(dep.get("capability_id") or "").strip()
        if not alias or alias in known or not capability_id:
            continue
        try:
            child = get_capability(capability_id)
            name = str(child.data.get("name") or capability_id)
            domain = str(child.data.get("domain") or child.target_name or alias)
        except Exception:
            name, domain = capability_id, alias
        modules.append({
            "alias": alias,
            "role": "dependency",
            "capability_id": capability_id,
            "name": name,
            "domain": domain,
            "replaceable": False,
            "runtime_owner": False,
            "required": bool(dep.get("required", True)),
        })
        known.add(alias)

    # Composition contracts often expose embedded project modules (EPS, thermal,
    # payload, comm, propulsion...) without registering each as a child
    # Capability.  Surface those as non-replaceable internal modules so the
    # signal graph remains faithful without pretending they are standalone.
    for raw in raw_mappings:
        source, target = _field_mapping_paths(raw)
        for path in (source, target):
            token = _first_token(path)
            if not token or token in known or token in _PARENT_SOURCE_PREFIXES:
                continue
            modules.append({
                "alias": token,
                "role": "internal",
                "capability_id": None,
                "name": token.replace("_", " ").title(),
                "domain": token,
                "replaceable": False,
                "runtime_owner": False,
                "required": True,
            })
            known.add(token)
    return modules


def _default_graph(contract_payload: Mapping[str, Any]) -> dict[str, Any]:
    modules = list(contract_payload.get("modules") or [])
    bindings = list(contract_payload.get("bindings") or [])
    node_id_by_alias: dict[str, str] = {}
    nodes: list[dict[str, Any]] = []
    dependency_index = 0
    internal_index = 0
    for item in modules:
        alias = str(item["alias"])
        role = str(item.get("role") or "internal")
        node_id = f"module-{_safe_id(alias)}"
        node_id_by_alias[alias] = node_id
        if role == "parent":
            x, y = 36, 205
        elif role == "dependency":
            x, y = 330, 60 + dependency_index * 155
            dependency_index += 1
        else:
            x, y = 655, 40 + internal_index * 135
            internal_index += 1
        nodes.append({
            "id": node_id,
            "kind": "module",
            "moduleAlias": alias,
            "role": role,
            "capabilityId": item.get("capability_id"),
            "x": x,
            "y": y,
        })
    edges: list[dict[str, Any]] = []
    for item in bindings:
        if not item.get("wireable"):
            continue
        source_alias = str(item["source_alias"])
        target_alias = str(item["target_alias"])
        if source_alias not in node_id_by_alias or target_alias not in node_id_by_alias:
            continue
        edges.append({
            "id": f"edge-{item['binding_id']}",
            "bindingId": item["binding_id"],
            "source": node_id_by_alias[source_alias],
            "sourcePort": item["source_port"],
            "target": node_id_by_alias[target_alias],
            "targetPort": item["target_port"],
            "dataType": item["data_type"],
        })
    port_contract = _mapping(contract_payload.get("simulation_ports"))
    module_contract = _mapping(contract_payload.get("module_replacements"))
    return {
        "schema_version": ASSEMBLY_GRAPH_SCHEMA_VERSION,
        "parent_capability_id": contract_payload["parent_capability_id"],
        "port_contract": {
            "schema_version": port_contract.get("schema_version"),
            "fingerprint": port_contract.get("fingerprint"),
            "source": port_contract.get("source"),
        },
        "module_contract": {
            "schema_version": module_contract.get("schema_version"),
            "fingerprint": module_contract.get("fingerprint"),
            "source": module_contract.get("source"),
        },
        "nodes": nodes,
        "edges": edges,
    }


def assembly_contract(capability_id: str) -> dict[str, Any]:
    """Return an executable visual assembly contract for one parent Capability."""

    contract = get_capability(capability_id)
    composition = _mapping(contract.data.get("composition"))
    raw_mappings = [item for item in _sequence(composition.get("field_mappings")) if isinstance(item, Mapping)]
    modules = _contract_modules(contract, raw_mappings)
    port_contract = build_simulation_port_contract(contract, modules, raw_mappings)
    module_replacements = build_module_replacement_contract(contract, modules, port_contract)
    slot_by_alias = {str(item.get("module_alias") or ""): item for item in _sequence(module_replacements.get("slots")) if isinstance(item, Mapping)}
    normalized_modules: list[dict[str, Any]] = []
    for module in modules:
        item = dict(module)
        slot = slot_by_alias.get(str(item.get("alias") or ""))
        if slot:
            item["replaceable"] = bool(slot.get("replaceable"))
            item["replacement_interface_id"] = slot.get("interface_id")
            item["replacement_slot_kind"] = slot.get("slot_kind")
            item["replacement_baseline_capability_id"] = slot.get("baseline_capability_id")
            item["replacement_options"] = [dict(candidate) for candidate in _sequence(slot.get("candidates")) if isinstance(candidate, Mapping)]
        normalized_modules.append(item)
    modules = normalized_modules
    bindings = list(port_contract.get("bindings") or [])
    payload = {
        "schema_version": ASSEMBLY_CATALOG_SCHEMA_VERSION,
        "parent_capability_id": contract.capability_id,
        "name": str(contract.data.get("name") or contract.capability_id),
        "summary": str(contract.data.get("summary") or ""),
        "level": contract.target_level,
        "domain": str(contract.data.get("domain") or contract.target_name or ""),
        "runtime_owner": contract.capability_id,
        "runtime_mode": "registered_parent_adapter",
        "arbitrary_module_substitution": False,
        "constrained_module_substitution": bool(_sequence(module_replacements.get("slots"))),
        "script_export": bool(_mapping(contract.data.get("adapter")).get("script_export")),
        "runtime_run": bool(_mapping(contract.data.get("adapter")).get("runtime_run")),
        "composition_type": str(composition.get("type") or ("registered_composite" if contract.dependencies or raw_mappings else "single_capability")),
        "modules": modules,
        "bindings": bindings,
        "simulation_ports": port_contract,
        "module_replacements": module_replacements,
        "port_contract_source": port_contract.get("source"),
        "port_contract_fingerprint": port_contract.get("fingerprint"),
        "port_contract_valid": bool(_mapping(port_contract.get("validation")).get("ok", False)),
        "module_contract_fingerprint": module_replacements.get("fingerprint"),
        "module_contract_valid": bool(_mapping(module_replacements.get("validation")).get("ok", False)),
        "replaceable_module_count": sum(1 for item in modules if item.get("replaceable")),
        "unwireable_mapping_count": sum(1 for item in bindings if not item.get("wireable")),
    }
    payload["default_graph"] = _default_graph(payload)
    return payload


def assembly_catalog() -> list[dict[str, Any]]:
    """List active executable composite capabilities usable by the V5 editor."""

    out: list[dict[str, Any]] = []
    for contract in list_capabilities():
        if not contract.is_active:
            continue
        composition = _mapping(contract.data.get("composition"))
        mappings = _sequence(composition.get("field_mappings"))
        if not contract.dependencies and not mappings:
            continue
        adapter = _mapping(contract.data.get("adapter"))
        if not bool(adapter.get("runtime_run", False)):
            continue
        payload = assembly_contract(contract.capability_id)
        out.append({
            "parent_capability_id": payload["parent_capability_id"],
            "name": payload["name"],
            "summary": payload["summary"],
            "domain": payload["domain"],
            "composition_type": payload["composition_type"],
            "module_count": len(payload["modules"]),
            "binding_count": len([item for item in payload["bindings"] if item.get("wireable")]),
            "port_count": len(_sequence(_mapping(payload.get("simulation_ports")).get("ports"))),
            "port_contract_source": payload.get("port_contract_source"),
            "port_contract_valid": payload.get("port_contract_valid"),
            "module_contract_valid": payload.get("module_contract_valid"),
            "replaceable_module_count": payload.get("replaceable_module_count", 0),
            "script_export": payload["script_export"],
            "runtime_run": payload["runtime_run"],
        })
    return sorted(out, key=lambda item: (str(item["domain"]), str(item["parent_capability_id"])))


def _feedback_groups(node_ids: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    """Return strongly connected groups with >1 node (or a self-loop)."""

    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for source, target in edges:
        adjacency.setdefault(source, []).append(target)
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    groups: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlink[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in adjacency.get(node, []):
            if target not in indices:
                visit(target)
                lowlink[node] = min(lowlink[node], lowlink[target])
            elif target in on_stack:
                lowlink[node] = min(lowlink[node], indices[target])
        if lowlink[node] == indices[node]:
            component: list[str] = []
            while stack:
                item = stack.pop()
                on_stack.discard(item)
                component.append(item)
                if item == node:
                    break
            if len(component) > 1 or (len(component) == 1 and component[0] in adjacency.get(component[0], [])):
                groups.append(sorted(component))

    for node_id in node_ids:
        if node_id not in indices:
            visit(node_id)
    return sorted(groups, key=lambda group: tuple(group))


def validate_assembly_graph(
    graph: Mapping[str, Any],
    *,
    expected_parent_capability_id: str | None = None,
    require_all_bindings: bool = True,
) -> AssemblyValidation:
    """Validate a module assembly against its registered parent contract."""

    issues: list[AssemblyIssue] = []
    if not isinstance(graph, Mapping):
        _issue(issues, "ASSEMBLY_GRAPH_NOT_OBJECT", "Assembly graph must be an object.")
        return AssemblyValidation(False, ASSEMBLY_GRAPH_SCHEMA_VERSION, "", issues, [], [], 0, 0, 0)

    schema_version = str(graph.get("schema_version") or "")
    supported_schema_versions = {ASSEMBLY_GRAPH_SCHEMA_VERSION, V6_ASSEMBLY_GRAPH_SCHEMA_VERSION, V5_ASSEMBLY_GRAPH_SCHEMA_VERSION, LEGACY_ASSEMBLY_GRAPH_SCHEMA_VERSION}
    if schema_version not in supported_schema_versions:
        _issue(issues, "ASSEMBLY_SCHEMA_VERSION_UNSUPPORTED", f"Unsupported assembly graph schema version {schema_version!r}.", path="$.assembly_graph.schema_version")
    elif schema_version == LEGACY_ASSEMBLY_GRAPH_SCHEMA_VERSION:
        _issue(
            issues,
            "ASSEMBLY_SCHEMA_LEGACY_V4",
            "V4 assembly graph accepted in compatibility mode; reload to bind V5/V6 contract fingerprints.",
            path="$.assembly_graph.schema_version",
            severity="warning",
        )
    elif schema_version == V6_ASSEMBLY_GRAPH_SCHEMA_VERSION:
        _issue(
            issues,
            "ASSEMBLY_SCHEMA_LEGACY_V6",
            "V6 assembly graph accepted in compatibility mode; V7 parent-managed internal module insertion requires a V7 graph fingerprint.",
            path="$.assembly_graph.schema_version",
            severity="warning",
        )
    elif schema_version == V5_ASSEMBLY_GRAPH_SCHEMA_VERSION:
        _issue(
            issues,
            "ASSEMBLY_SCHEMA_LEGACY_V5",
            "V5 assembly graph accepted in baseline compatibility mode; constrained module replacement requires V6/V7 graph contracts.",
            path="$.assembly_graph.schema_version",
            severity="warning",
        )

    parent_capability_id = str(graph.get("parent_capability_id") or "").strip()
    if not parent_capability_id:
        _issue(issues, "ASSEMBLY_PARENT_MISSING", "Assembly graph must identify parent_capability_id.", path="$.assembly_graph.parent_capability_id")
        return AssemblyValidation(False, ASSEMBLY_GRAPH_SCHEMA_VERSION, "", issues, [], [], 0, 0, 0)
    if expected_parent_capability_id and parent_capability_id != expected_parent_capability_id:
        _issue(
            issues,
            "ASSEMBLY_PARENT_MISMATCH",
            "Assembly parent Capability does not match the submitted form Capability.",
            path="$.assembly_graph.parent_capability_id",
            details={"graph": parent_capability_id, "form": expected_parent_capability_id},
        )

    try:
        contract_payload = assembly_contract(parent_capability_id)
    except Exception as exc:
        _issue(issues, "ASSEMBLY_PARENT_UNKNOWN", str(exc), path="$.assembly_graph.parent_capability_id")
        return AssemblyValidation(False, ASSEMBLY_GRAPH_SCHEMA_VERSION, parent_capability_id, issues, [], [], 0, 0, 0)

    port_contract = _mapping(contract_payload.get("simulation_ports"))
    port_validation = _mapping(port_contract.get("validation"))
    for raw_issue in _sequence(port_validation.get("issues")):
        if not isinstance(raw_issue, Mapping):
            continue
        _issue(
            issues,
            str(raw_issue.get("code") or "ASSEMBLY_PORT_CONTRACT_INVALID"),
            str(raw_issue.get("message") or "Simulation-port contract validation issue."),
            path=str(raw_issue.get("path") or "$.simulation_ports"),
            severity=str(raw_issue.get("severity") or "error"),
            details=dict(raw_issue.get("details")) if isinstance(raw_issue.get("details"), Mapping) else None,
        )
    expected_fingerprint = str(port_contract.get("fingerprint") or "")
    if schema_version in {ASSEMBLY_GRAPH_SCHEMA_VERSION, V6_ASSEMBLY_GRAPH_SCHEMA_VERSION, V5_ASSEMBLY_GRAPH_SCHEMA_VERSION}:
        graph_port_contract = _mapping(graph.get("port_contract"))
        actual_fingerprint = str(graph_port_contract.get("fingerprint") or "")
        actual_port_schema = str(graph_port_contract.get("schema_version") or "")
        if actual_port_schema != SIMULATION_PORT_SCHEMA_VERSION:
            _issue(issues, "ASSEMBLY_PORT_SCHEMA_MISMATCH", "Assembly graph is not bound to the current simulation-port schema.", path="$.assembly_graph.port_contract.schema_version")
        if not actual_fingerprint or actual_fingerprint != expected_fingerprint:
            _issue(
                issues,
                "ASSEMBLY_PORT_CONTRACT_STALE",
                "Assembly graph port-contract fingerprint does not match the current registered Capability contract.",
                path="$.assembly_graph.port_contract.fingerprint",
                details={"expected": expected_fingerprint, "actual": actual_fingerprint},
            )

    module_contract = _mapping(contract_payload.get("module_replacements"))
    module_contract_validation = _mapping(module_contract.get("validation"))
    for raw_issue in _sequence(module_contract_validation.get("issues")):
        if isinstance(raw_issue, Mapping):
            _issue(
                issues,
                str(raw_issue.get("code") or "ASSEMBLY_MODULE_CONTRACT_INVALID"),
                str(raw_issue.get("message") or "Module replacement contract validation issue."),
                path=str(raw_issue.get("path") or "$.simulation_ports.module_slots"),
                severity=str(raw_issue.get("severity") or "error"),
                details=dict(raw_issue.get("details")) if isinstance(raw_issue.get("details"), Mapping) else None,
            )
    expected_module_fingerprint = str(module_contract.get("fingerprint") or "")
    if schema_version == ASSEMBLY_GRAPH_SCHEMA_VERSION:
        graph_module_contract = _mapping(graph.get("module_contract"))
        actual_module_schema = str(graph_module_contract.get("schema_version") or "")
        actual_module_fingerprint = str(graph_module_contract.get("fingerprint") or "")
        if actual_module_schema != MODULE_REPLACEMENT_SCHEMA_VERSION:
            _issue(issues, "ASSEMBLY_MODULE_SCHEMA_MISMATCH", "Assembly graph is not bound to the current module-replacement schema.", path="$.assembly_graph.module_contract.schema_version")
        if not actual_module_fingerprint or actual_module_fingerprint != expected_module_fingerprint:
            _issue(
                issues,
                "ASSEMBLY_MODULE_CONTRACT_STALE",
                "Assembly graph module-replacement fingerprint does not match the current registered Capability contract.",
                path="$.assembly_graph.module_contract.fingerprint",
                details={"expected": expected_module_fingerprint, "actual": actual_module_fingerprint},
            )

    raw_nodes = graph.get("nodes")
    raw_edges = graph.get("edges")
    if not isinstance(raw_nodes, list):
        _issue(issues, "ASSEMBLY_NODES_INVALID", "assembly_graph.nodes must be a list.", path="$.assembly_graph.nodes")
        raw_nodes = []
    if not isinstance(raw_edges, list):
        _issue(issues, "ASSEMBLY_EDGES_INVALID", "assembly_graph.edges must be a list.", path="$.assembly_graph.edges")
        raw_edges = []
    if len(raw_nodes) > MAX_ASSEMBLY_NODES:
        _issue(issues, "ASSEMBLY_NODE_LIMIT_EXCEEDED", f"Assembly graph supports at most {MAX_ASSEMBLY_NODES} nodes.", path="$.assembly_graph.nodes")
        raw_nodes = raw_nodes[:MAX_ASSEMBLY_NODES]
    if len(raw_edges) > MAX_ASSEMBLY_EDGES:
        _issue(issues, "ASSEMBLY_EDGE_LIMIT_EXCEEDED", f"Assembly graph supports at most {MAX_ASSEMBLY_EDGES} edges.", path="$.assembly_graph.edges")
        raw_edges = raw_edges[:MAX_ASSEMBLY_EDGES]

    module_by_alias = {str(item["alias"]): item for item in contract_payload["modules"]}
    replacement_slot_by_alias = {
        str(item.get("module_alias") or ""): item
        for item in _sequence(module_contract.get("slots"))
        if isinstance(item, Mapping)
    }
    nodes_by_id: dict[str, Mapping[str, Any]] = {}
    node_id_by_alias: dict[str, str] = {}
    selected_modules: dict[str, str | None] = {}
    replacement_count = 0
    for index, raw in enumerate(raw_nodes):
        path = f"$.assembly_graph.nodes[{index}]"
        if not isinstance(raw, Mapping):
            _issue(issues, "ASSEMBLY_NODE_INVALID", "Assembly node must be an object.", path=path)
            continue
        node_id = str(raw.get("id") or "").strip()
        alias = str(raw.get("moduleAlias") or raw.get("module_alias") or "").strip()
        if not node_id or len(node_id) > MAX_ASSEMBLY_ID_LENGTH:
            _issue(issues, "ASSEMBLY_NODE_ID_INVALID", "Assembly node id is missing or too long.", path=f"{path}.id")
            continue
        if node_id in nodes_by_id:
            _issue(issues, "ASSEMBLY_NODE_ID_DUPLICATE", f"Duplicate assembly node id {node_id!r}.", path=f"{path}.id")
            continue
        if alias not in module_by_alias:
            _issue(issues, "ASSEMBLY_MODULE_UNKNOWN", f"Module alias {alias!r} is not registered by the parent Capability.", path=f"{path}.moduleAlias")
            continue
        if alias in node_id_by_alias:
            _issue(issues, "ASSEMBLY_MODULE_DUPLICATE", f"Module alias {alias!r} appears more than once.", path=f"{path}.moduleAlias")
            continue
        expected_capability = module_by_alias[alias].get("capability_id")
        actual_capability_raw = raw.get("capabilityId") if "capabilityId" in raw else raw.get("capability_id")
        actual_capability = str(actual_capability_raw or "").strip() or None
        slot = replacement_slot_by_alias.get(alias)
        slot_kind = str(_mapping(slot).get("slot_kind") or "") if slot else ""
        candidate = compatible_candidate(slot, actual_capability or "") if slot and actual_capability else None
        if expected_capability and actual_capability != str(expected_capability):
            if schema_version not in {ASSEMBLY_GRAPH_SCHEMA_VERSION, V6_ASSEMBLY_GRAPH_SCHEMA_VERSION}:
                _issue(
                    issues,
                    "ASSEMBLY_REPLACEMENT_REQUIRES_V6_SCHEMA",
                    f"Module replacement for {alias!r} requires a V6/V7 assembly graph bound to a module contract.",
                    path=f"{path}.capabilityId",
                    details={"baseline": expected_capability, "actual": actual_capability},
                )
            elif not slot or not bool(slot.get("replaceable")):
                _issue(
                    issues,
                    "ASSEMBLY_MODULE_NOT_REPLACEABLE",
                    f"Module {alias!r} does not expose a registered replacement slot.",
                    path=f"{path}.capabilityId",
                    details={"baseline": expected_capability, "actual": actual_capability},
                )
            elif candidate is None:
                _issue(
                    issues,
                    "ASSEMBLY_MODULE_REPLACEMENT_INCOMPATIBLE",
                    f"Capability {actual_capability!r} is not an approved interface-compatible replacement for module {alias!r}.",
                    path=f"{path}.capabilityId",
                    details={
                        "baseline": expected_capability,
                        "actual": actual_capability,
                        "interface_id": slot.get("interface_id"),
                        "allowed": [item.get("capability_id") for item in _sequence(slot.get("candidates")) if isinstance(item, Mapping) and item.get("compatible")],
                    },
                )
            else:
                replacement_count += 1
                _issue(
                    issues,
                    "ASSEMBLY_MODULE_REPLACEMENT_ACTIVE",
                    f"Module {alias!r} uses registered compatible replacement {actual_capability!r} instead of baseline {expected_capability!r}.",
                    path=f"{path}.capabilityId",
                    severity="warning",
                    details={"baseline": expected_capability, "selected": actual_capability, "interface_id": slot.get("interface_id"), "slot_kind": slot_kind},
                )
        elif expected_capability is None and actual_capability not in (None, ""):
            if schema_version != ASSEMBLY_GRAPH_SCHEMA_VERSION:
                _issue(
                    issues,
                    "ASSEMBLY_INTERNAL_REPLACEMENT_REQUIRES_V7_SCHEMA",
                    f"Parent-managed internal module {alias!r} may only be replaced by a V7 assembly graph bound to the current module contract.",
                    path=f"{path}.capabilityId",
                )
            elif not slot or slot_kind != "parent_managed_internal" or not bool(slot.get("replaceable")):
                _issue(issues, "ASSEMBLY_INTERNAL_MODULE_SUBSTITUTION", f"Internal module {alias!r} is owned by the parent adapter and has no executable V7 replacement slot.", path=f"{path}.capabilityId")
            elif candidate is None:
                _issue(
                    issues,
                    "ASSEMBLY_INTERNAL_MODULE_SUBSTITUTION",
                    f"Capability {actual_capability!r} is not an approved interface-compatible replacement for parent-managed module {alias!r}.",
                    path=f"{path}.capabilityId",
                    details={"baseline": None, "actual": actual_capability, "interface_id": slot.get("interface_id")},
                )
            else:
                replacement_count += 1
                _issue(
                    issues,
                    "ASSEMBLY_INTERNAL_MODULE_REPLACEMENT_ACTIVE",
                    f"Parent-managed module {alias!r} is promoted to registered module {actual_capability!r} through its V7 interface slot.",
                    path=f"{path}.capabilityId",
                    severity="warning",
                    details={"baseline": None, "selected": actual_capability, "interface_id": slot.get("interface_id")},
                )
        selected_modules[alias] = actual_capability
        nodes_by_id[node_id] = raw
        node_id_by_alias[alias] = node_id

    for alias, module in module_by_alias.items():
        if bool(module.get("required", True)) and alias not in node_id_by_alias:
            _issue(issues, "ASSEMBLY_REQUIRED_MODULE_MISSING", f"Required module {alias!r} is missing.", details={"module_alias": alias})

    binding_by_id = {str(item["binding_id"]): item for item in contract_payload["bindings"] if item.get("wireable")}
    binding_by_ports = {(str(item["source_port"]), str(item["target_port"])): item for item in binding_by_id.values()}
    seen_edge_ids: set[str] = set()
    seen_bindings: set[str] = set()
    incoming_port_counts: dict[tuple[str, str], int] = {}
    source_port_counts: dict[tuple[str, str], int] = {}
    valid_edges: list[tuple[str, str]] = []
    valid_binding_ids: list[str] = []

    for index, raw in enumerate(raw_edges):
        path = f"$.assembly_graph.edges[{index}]"
        if not isinstance(raw, Mapping):
            _issue(issues, "ASSEMBLY_EDGE_INVALID", "Assembly edge must be an object.", path=path)
            continue
        edge_id = str(raw.get("id") or f"edge-{index}").strip()
        source = str(raw.get("source") or "").strip()
        target = str(raw.get("target") or "").strip()
        source_port = str(raw.get("sourcePort") or raw.get("source_port") or "").strip()
        target_port = str(raw.get("targetPort") or raw.get("target_port") or "").strip()
        binding_id = str(raw.get("bindingId") or raw.get("binding_id") or "").strip()
        if any(len(value) > MAX_ASSEMBLY_ID_LENGTH for value in (edge_id, source, target, source_port, target_port, binding_id)):
            _issue(issues, "ASSEMBLY_EDGE_FIELD_TOO_LONG", "Assembly edge identifier or port is too long.", path=path)
            continue
        if edge_id in seen_edge_ids:
            _issue(issues, "ASSEMBLY_EDGE_ID_DUPLICATE", f"Duplicate assembly edge id {edge_id!r}.", path=f"{path}.id")
            continue
        seen_edge_ids.add(edge_id)
        if source not in nodes_by_id or target not in nodes_by_id:
            _issue(issues, "ASSEMBLY_EDGE_NODE_UNKNOWN", "Assembly edge references an unknown node.", path=path, details={"source": source, "target": target})
            continue
        binding = binding_by_id.get(binding_id) if binding_id else binding_by_ports.get((source_port, target_port))
        if not binding:
            _issue(issues, "ASSEMBLY_BINDING_UNREGISTERED", "Signal edge is not a registered parent Capability field mapping.", path=path)
            continue
        expected_source = node_id_by_alias.get(str(binding["source_alias"]))
        expected_target = node_id_by_alias.get(str(binding["target_alias"]))
        if source != expected_source or target != expected_target or source_port != binding["source_port"] or target_port != binding["target_port"]:
            _issue(
                issues,
                "ASSEMBLY_BINDING_ENDPOINT_MISMATCH",
                f"Binding {binding['binding_id']!r} is connected to the wrong module or port.",
                path=path,
                details={
                    "expected_source": expected_source,
                    "expected_target": expected_target,
                    "expected_source_port": binding["source_port"],
                    "expected_target_port": binding["target_port"],
                },
            )
            continue
        if binding["binding_id"] in seen_bindings:
            _issue(issues, "ASSEMBLY_BINDING_DUPLICATE", f"Binding {binding['binding_id']!r} appears more than once.", path=path)
            continue
        target_key = (target, target_port)
        source_key = (source, source_port)
        target_limit = str(binding.get("target_fan_in") or "one")
        source_limit = str(binding.get("source_fan_out") or "many")
        if target_limit == "one" and incoming_port_counts.get(target_key, 0) >= 1:
            _issue(issues, "ASSEMBLY_INPUT_MULTIPLE_DRIVERS", f"Input port {target}.{target_port} has more than one driver.", path=path)
            continue
        if source_limit == "one" and source_port_counts.get(source_key, 0) >= 1:
            _issue(issues, "ASSEMBLY_OUTPUT_FANOUT_EXCEEDED", f"Output port {source}.{source_port} permits only one consumer.", path=path)
            continue
        declared_data_type = str(raw.get("dataType") or raw.get("data_type") or binding["data_type"])
        if declared_data_type != str(binding["data_type"]):
            _issue(
                issues,
                "ASSEMBLY_SIGNAL_TYPE_MISMATCH",
                f"Binding {binding['binding_id']!r} requires signal type {binding['data_type']!r}.",
                path=path,
                details={"expected": binding["data_type"], "actual": declared_data_type},
            )
            continue
        seen_bindings.add(str(binding["binding_id"]))
        incoming_port_counts[target_key] = incoming_port_counts.get(target_key, 0) + 1
        source_port_counts[source_key] = source_port_counts.get(source_key, 0) + 1
        valid_edges.append((source, target))
        valid_binding_ids.append(str(binding["binding_id"]))

    if require_all_bindings:
        for binding_id, binding in binding_by_id.items():
            if binding_id not in seen_bindings:
                _issue(
                    issues,
                    "ASSEMBLY_REQUIRED_BINDING_MISSING",
                    f"Registered signal binding {binding_id!r} is not connected.",
                    details={
                        "binding_id": binding_id,
                        "source_path": binding["source_path"],
                        "target_path": binding["target_path"],
                    },
                )

    feedback = _feedback_groups(list(nodes_by_id), valid_edges)
    if feedback:
        _issue(
            issues,
            "ASSEMBLY_REGISTERED_FEEDBACK_PRESENT",
            "Registered composition contains feedback coupling; execution order is owned by the parent adapter rather than inferred from graph DAG order.",
            severity="warning",
            details={"groups": feedback},
        )

    if feedback and str(port_contract.get("source") or "") == "explicit-v5":
        node_alias_by_id = {node_id: str(raw.get("moduleAlias") or raw.get("module_alias") or "") for node_id, raw in nodes_by_id.items()}
        for group in feedback:
            aliases = {node_alias_by_id.get(node_id, "") for node_id in group}
            loop_bindings = [
                binding_by_id[binding_id]
                for binding_id in valid_binding_ids
                if binding_id in binding_by_id
                and str(binding_by_id[binding_id].get("source_alias")) in aliases
                and str(binding_by_id[binding_id].get("target_alias")) in aliases
            ]
            has_state_break = any(
                int(_mapping(item.get("solver")).get("delay_steps") or 0) > 0
                or str(_mapping(item.get("solver")).get("policy") or "") in {"parent_managed_feedback", "parent_managed_stateful_feedback", "delayed_feedback"}
                or not bool(item.get("source_direct_feedthrough", False))
                for item in loop_bindings
            )
            if not has_state_break:
                _issue(
                    issues,
                    "ASSEMBLY_ALGEBRAIC_LOOP_UNRESOLVED",
                    "Explicit feedback group has no declared delay/state break or parent-managed feedback solver policy.",
                    details={"group": group, "bindings": [item.get("binding_id") for item in loop_bindings]},
                )

    module_order: list[str] = []
    composition = _mapping(get_capability(parent_capability_id).data.get("composition"))
    for step in _sequence(composition.get("chain")):
        if not isinstance(step, Mapping):
            continue
        alias = str(step.get("alias") or (_PARENT_ALIAS if step.get("role") == "parent" else "")).strip()
        if alias and alias in node_id_by_alias and alias not in module_order:
            module_order.append(alias)
    for item in contract_payload["modules"]:
        alias = str(item["alias"])
        if alias in node_id_by_alias and alias not in module_order:
            module_order.append(alias)

    ok = not any(item.severity == "error" for item in issues)
    return AssemblyValidation(
        ok=ok,
        schema_version=ASSEMBLY_GRAPH_SCHEMA_VERSION,
        parent_capability_id=parent_capability_id,
        issues=issues,
        module_order=module_order,
        feedback_groups=feedback,
        node_count=len(nodes_by_id),
        edge_count=len(valid_edges),
        required_binding_count=len(binding_by_id),
        port_contract_source=str(port_contract.get("source") or ""),
        port_contract_fingerprint=str(port_contract.get("fingerprint") or ""),
        solver_policies=tuple(sorted({str(_mapping(item.get("solver")).get("policy") or "parent_owned") for item in binding_by_id.values()})),
        timing_policies=tuple(sorted({
            str(_mapping(item.get("source_timing")).get("rate_policy") or "parent_owned")
            for item in binding_by_id.values()
        } | {
            str(_mapping(item.get("target_timing")).get("rate_policy") or "parent_owned")
            for item in binding_by_id.values()
        })),
        module_contract_fingerprint=expected_module_fingerprint,
        replacement_count=replacement_count,
        selected_modules=selected_modules,
    )


def assembly_runtime_metadata(graph: Mapping[str, Any], validation: AssemblyValidation | None = None) -> dict[str, Any]:
    """Return the trusted runtime selector metadata bound to an assembly graph."""

    checked = validation or validate_assembly_graph(graph, require_all_bindings=True)
    if not checked.ok:
        messages = "; ".join(item.message for item in checked.errors[:8])
        raise ValueError(f"assembly graph is not executable: {messages}")
    contract = assembly_contract(checked.parent_capability_id)
    baseline_by_alias = {str(item.get("alias") or ""): item.get("capability_id") for item in _sequence(contract.get("modules")) if isinstance(item, Mapping)}
    slot_by_alias = {str(item.get("module_alias") or ""): item for item in _sequence(_mapping(contract.get("module_replacements")).get("slots")) if isinstance(item, Mapping)}
    selected = dict(checked.selected_modules or {})
    substitutions: list[dict[str, Any]] = []
    for alias, capability_id in sorted(selected.items()):
        slot = slot_by_alias.get(alias)
        baseline = baseline_by_alias.get(alias)
        if slot and str(slot.get("slot_kind") or "") == "parent_managed_internal":
            baseline = None
        if capability_id != baseline and (capability_id is not None or baseline is not None):
            substitutions.append({
                "module_alias": alias,
                "baseline_capability_id": baseline,
                "selected_capability_id": capability_id,
                "slot_kind": str(_mapping(slot).get("slot_kind") or "dependency"),
            })
    return {
        "schema_version": ASSEMBLY_RUNTIME_SCHEMA_VERSION,
        "parent_capability_id": checked.parent_capability_id,
        "port_contract_fingerprint": checked.port_contract_fingerprint,
        "module_contract_fingerprint": checked.module_contract_fingerprint,
        "module_selections": selected,
        "substitutions": substitutions,
        "replacement_count": len(substitutions),
        "runtime_owner": checked.parent_capability_id,
    }


def validate_assembly_task_spec_binding(
    task_spec: Mapping[str, Any],
    graph: Mapping[str, Any],
    *,
    validation: AssemblyValidation | None = None,
) -> dict[str, Any]:
    """Ensure TaskSpec runtime selectors cannot drift from the validated graph."""

    checked = validation or validate_assembly_graph(graph, require_all_bindings=True)
    issues: list[AssemblyIssue] = []
    if not checked.ok:
        issues.extend(checked.errors)
        return {"ok": False, "issues": [item.to_dict() for item in issues], "errors": [item.to_dict() for item in issues]}
    expected = assembly_runtime_metadata(graph, checked)
    metadata = _mapping(task_spec.get("metadata"))
    actual = _mapping(metadata.get("visual_assembly"))
    if not actual:
        _issue(issues, "ASSEMBLY_TASK_BINDING_MISSING", "TaskSpec is not bound to the validated visual assembly.", path="$.metadata.visual_assembly")
    else:
        comparisons = (
            ("schema_version", expected["schema_version"]),
            ("parent_capability_id", expected["parent_capability_id"]),
            ("port_contract_fingerprint", expected["port_contract_fingerprint"]),
            ("module_contract_fingerprint", expected["module_contract_fingerprint"]),
        )
        for key, expected_value in comparisons:
            if actual.get(key) != expected_value:
                _issue(
                    issues,
                    "ASSEMBLY_TASK_BINDING_STALE",
                    f"TaskSpec visual assembly {key} does not match the submitted graph.",
                    path=f"$.metadata.visual_assembly.{key}",
                    details={"expected": expected_value, "actual": actual.get(key)},
                )
        actual_selections = {str(k): (str(v) if v is not None else None) for k, v in _mapping(actual.get("module_selections")).items()}
        if actual_selections != expected["module_selections"]:
            _issue(
                issues,
                "ASSEMBLY_TASK_MODULE_SELECTION_MISMATCH",
                "TaskSpec module selections do not match the validated assembly graph.",
                path="$.metadata.visual_assembly.module_selections",
                details={"expected": expected["module_selections"], "actual": actual_selections},
            )
    return {
        "ok": not any(item.severity == "error" for item in issues),
        "issues": [item.to_dict() for item in issues],
        "errors": [item.to_dict() for item in issues if item.severity == "error"],
        "expected": expected,
    }


__all__ = [
    "ASSEMBLY_GRAPH_SCHEMA_VERSION",
    "V6_ASSEMBLY_GRAPH_SCHEMA_VERSION",
    "V5_ASSEMBLY_GRAPH_SCHEMA_VERSION",
    "LEGACY_ASSEMBLY_GRAPH_SCHEMA_VERSION",
    "ASSEMBLY_RUNTIME_SCHEMA_VERSION",
    "ASSEMBLY_CATALOG_SCHEMA_VERSION",
    "MAX_ASSEMBLY_NODES",
    "MAX_ASSEMBLY_EDGES",
    "MAX_ASSEMBLY_ID_LENGTH",
    "AssemblyIssue",
    "AssemblyValidation",
    "assembly_catalog",
    "assembly_contract",
    "validate_assembly_graph",
    "assembly_runtime_metadata",
    "validate_assembly_task_spec_binding",
]
