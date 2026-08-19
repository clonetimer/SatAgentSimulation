"""V24 deterministic TaskSpec resolver and validated execution DAG.

This module turns a CanonicalTaskSpec into two immutable planning artifacts:

``ResolvedSpec``
    Registry-grounded capability, dependency, effect and output bindings.

``ExecutionPlan``
    A constrained acyclic workflow with explicit PASS/FAIL/INCONCLUSIVE/CANCEL
    branches.  V24 only builds and validates the plan; V25 executes it.

The implementation is intentionally conservative.  It never invents a missing
physical capability or an effect/QoI relationship.  Unsupported or ambiguous
bindings remain errors and block compilation/execution.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .capability_registry import find_effect_owners, get_capability
from .operator_contract import OperatorContract, OperatorEffect
from .task_models import canonicalize_task_spec
from .task_spec import spec_sha256
from .task_validator import validate_task_spec
from .agent_guards import evaluate_agent_guards
from .effect_evidence import EffectEvidenceClassification, get_effect_evidence_profile
from .parameter_consumption import audit_parameter_consumption

RESOLVED_SPEC_VERSION = "v24.resolved-spec.v1"
EXECUTION_PLAN_VERSION = "v24.execution-plan.v1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapabilityBinding(_Strict):
    capability_id: str
    role: Literal["primary", "dependency"]
    level: str
    owner: str
    public_adapter: str
    execution_mode: Literal["primary_adapter", "owned_by_primary"]
    lifecycle_status: str = "unknown"
    dependency_alias: str | None = None
    dependency_required: bool = True
    depth: int = Field(default=0, ge=0)


class DependencyEdge(_Strict):
    parent_capability_id: str
    child_capability_id: str
    alias: str
    required: bool = True


class DependencyGraph(_Strict):
    primary_capability_id: str
    nodes: list[CapabilityBinding]
    edges: list[DependencyEdge] = Field(default_factory=list)
    topological_order: list[str]
    execution_owner_capability_id: str

    @model_validator(mode="after")
    def validate_graph(self) -> "DependencyGraph":
        node_ids = [node.capability_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("dependency graph contains duplicate capability nodes")
        known = set(node_ids)
        if self.primary_capability_id not in known:
            raise ValueError("primary capability is missing from dependency graph")
        if set(self.topological_order) != known:
            raise ValueError("topological_order must contain every dependency node exactly once")
        for edge in self.edges:
            if edge.parent_capability_id not in known or edge.child_capability_id not in known:
                raise ValueError("dependency edge references an unknown capability")
        return self


class EffectBinding(_Strict):
    event_id: str
    event_kind: Literal["fault", "degradation", "constraint"]
    requested_effect: str
    requested_target: str
    owner: str
    capability_id: str
    implementation: str
    adapter: str
    verification: Literal["declared", "candidate", "not_observable"]
    evidence_fields: list[str] = Field(default_factory=list)
    binding_status: Literal["resolved", "unresolved", "ambiguous"] = "resolved"


class OutputBinding(_Strict):
    requested_field: str
    bound_field: str | None = None
    capability_id: str | None = None
    owner: str | None = None
    source_kind: Literal["qoi", "trace", "produces", "unresolved"] = "unresolved"
    explicitly_requested: bool = True
    binding_status: Literal["resolved", "unresolved", "ambiguous"] = "resolved"


class ResolvedAssurance(_Strict):
    parameter_profile: str
    claim_level: str
    validation_profile: str
    allow_proxy: bool
    capability_trust_level: str
    capability_fidelity_level: str
    capability_can_claim_high_fidelity: bool
    limitations: list[str] = Field(default_factory=list)


class ResolvedSpec(_Strict):
    schema_version: Literal[RESOLVED_SPEC_VERSION] = RESOLVED_SPEC_VERSION
    task_id: str
    canonical_task_spec_sha256: str
    requested_level: str
    resolved_level: str
    primary_capability_id: str
    primary_owner: str
    primary_adapter: str
    required_couplings: list[str] = Field(default_factory=list)
    supported_couplings: list[str] = Field(default_factory=list)
    source_modules: list[str] = Field(default_factory=list)
    implementation_class: str = "unknown"
    capability_limitations: list[str] = Field(default_factory=list)
    parameter_consumption: dict[str, Any] = Field(default_factory=dict)
    dependency_graph: DependencyGraph
    effect_bindings: list[EffectBinding] = Field(default_factory=list)
    output_bindings: list[OutputBinding] = Field(default_factory=list)
    assurance: ResolvedAssurance
    resource_locks: list[str] = Field(default_factory=list)
    runtime_timeout_s: float = Field(default=300.0, gt=0)
    resolved_spec_sha256: str = ""


class PlanNode(_Strict):
    node_id: str
    node_type: Literal[
        "task_validation",
        "guard_validation",
        "dependency_resolution",
        "causal_requirement_validation",
        "parameter_consumption_validation",
        "effect_binding",
        "output_binding",
        "resource_acquire",
        "execute_primary_adapter",
        "evidence_collection",
        "qoi_validation",
        "claim_report",
        "resource_release",
        "terminal_success",
        "terminal_execution_failure",
        "terminal_mission_failure",
        "terminal_inconclusive",
        "terminal_cancelled",
    ]
    handler: str
    binding_status: Literal["available", "runtime_contract", "terminal"] = "available"
    capability_id: str | None = None
    adapter: str | None = None
    timeout_s: float | None = Field(default=None, gt=0)
    resource_locks: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanEdge(_Strict):
    from_node: str
    to_node: str
    condition: Literal[
        "SUCCESS",
        "FAILURE",
        "PASS",
        "FAIL",
        "INCONCLUSIVE",
        "CANCELLED",
        "ALWAYS",
    ]


class ExecutionPlan(_Strict):
    schema_version: Literal[EXECUTION_PLAN_VERSION] = EXECUTION_PLAN_VERSION
    task_id: str
    canonical_task_spec_sha256: str
    resolved_spec_sha256: str
    primary_capability_id: str
    entry_node: str
    nodes: list[PlanNode]
    edges: list[PlanEdge]
    terminal_nodes: list[str]
    plan_sha256: str = ""


@dataclass(frozen=True)
class PlanIssue:
    severity: Literal["error", "warning"]
    code: str
    path: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlanValidationReport:
    ok: bool
    issues: tuple[PlanIssue, ...] = field(default_factory=tuple)

    @property
    def errors(self) -> tuple[PlanIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[PlanIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [issue.to_dict() for issue in self.issues],
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


@dataclass(frozen=True)
class PlanningResult:
    ok: bool
    resolved_spec: ResolvedSpec | None
    execution_plan: ExecutionPlan | None
    validation: PlanValidationReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "resolved_spec": self.resolved_spec.model_dump(mode="json") if self.resolved_spec else None,
            "execution_plan": self.execution_plan.model_dump(mode="json") if self.execution_plan else None,
            "validation": self.validation.to_dict(),
        }


def _stable_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalized_level(level: str) -> str:
    value = str(level or "").strip().lower().replace("-", "_")
    return "orbit_environment" if value == "integrated" else value


def infer_explicit_request_level(text: str) -> str | None:
    """Infer only explicit level words; do not infer a level from domain terms."""

    lower = text.lower()
    rules: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("whole_spacecraft", ("whole spacecraft", "whole-spacecraft", "整星", "全星")),
        ("orbit_environment", ("orbit environment", "轨道环境")),
        ("subsystem", ("subsystem", "分系统")),
        ("component", ("component level", "component-level", "部件级", "单部件")),
    )
    matches = [level for level, words in rules if any(word in lower for word in words)]
    return matches[0] if len(set(matches)) == 1 else None


def _request_text(spec: Mapping[str, Any]) -> str:
    metadata = spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}
    agent = metadata.get("agent") if isinstance(metadata.get("agent"), Mapping) else {}
    return str(agent.get("request_text") or agent.get("request") or "")


def _target_key(value: str) -> str:
    text = re.sub(r"\[[^\]]*\]", "", value.lower())
    tokens = re.findall(r"[a-z0-9]+", text)
    joined = "".join(tokens)
    replacements = {
        "reactionwheels": "reactionwheel",
        "wheels": "wheel",
        "batteries": "battery",
        "solarpanels": "solarpanel",
    }
    for old, new in replacements.items():
        joined = joined.replace(old, new)
    return joined


def _target_matches(target: str, owner: str, capability_id: str) -> bool:
    t = _target_key(target)
    candidates = {_target_key(owner), _target_key(capability_id), _target_key(capability_id.split(".")[-2] if "." in capability_id else capability_id)}
    return any(candidate and (candidate in t or t in candidate) for candidate in candidates)


def _declared_event_target_matches(target: str, capability_id: str) -> bool:
    """Match a physical event target only when the capability declares its scope.

    Composite capabilities own the executable effect contract while routing the
    event to subsystem/component targets.  The allow-list is deliberately kept
    in the capability contract so this exception cannot weaken target ownership
    checks for unrelated capabilities.
    """

    if not target:
        return False
    contract = get_capability(capability_id)
    raw_prefixes = contract.data.get("event_target_prefixes")
    if not isinstance(raw_prefixes, Sequence) or isinstance(raw_prefixes, (str, bytes)):
        return False
    target_key = _target_key(target)
    for prefix in raw_prefixes:
        prefix_key = _target_key(str(prefix))
        if prefix_key and (target_key == prefix_key or target_key.startswith(prefix_key)):
            return True
    return False


def _effect_target_matches(target: str, owner: str, capability_id: str) -> bool:
    return _target_matches(target, owner, capability_id) or _declared_event_target_matches(target, capability_id)


def _dependency_graph(primary_capability_id: str, issues: list[PlanIssue]) -> DependencyGraph | None:
    nodes: dict[str, CapabilityBinding] = {}
    edges: list[DependencyEdge] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    order: list[str] = []

    def visit(capability_id: str, *, role: Literal["primary", "dependency"], depth: int, alias: str | None = None, required: bool = True) -> None:
        if capability_id in visiting:
            issues.append(PlanIssue("error", "DEPENDENCY_CYCLE", "$.dependency_graph", f"Dependency cycle detected at {capability_id}.", {"capability_id": capability_id}))
            return
        if capability_id in visited:
            return
        try:
            contract = get_capability(capability_id)
        except Exception as exc:
            issues.append(PlanIssue("error", "DEPENDENCY_CAPABILITY_UNKNOWN", "$.dependency_graph", str(exc), {"capability_id": capability_id}))
            return
        visiting.add(capability_id)
        op = contract.operator_contract
        nodes[capability_id] = CapabilityBinding(
            capability_id=capability_id,
            role=role,
            level=_normalized_level(op.level),
            owner=op.owner,
            public_adapter=op.public_adapter,
            execution_mode="primary_adapter" if role == "primary" else "owned_by_primary",
            lifecycle_status=contract.lifecycle_status,
            dependency_alias=alias,
            dependency_required=required,
            depth=depth,
        )
        for dependency in sorted(op.dependencies, key=lambda item: str(item.get("capability_id") or "")):
            child = str(dependency.get("capability_id") or "").strip()
            if not child:
                issues.append(PlanIssue("error", "DEPENDENCY_ID_MISSING", "$.dependency_graph", f"{capability_id} contains a dependency without capability_id."))
                continue
            dep_alias = str(dependency.get("alias") or child.replace(".", "_"))
            dep_required = bool(dependency.get("required", True))
            edges.append(DependencyEdge(parent_capability_id=capability_id, child_capability_id=child, alias=dep_alias, required=dep_required))
            visit(child, role="dependency", depth=depth + 1, alias=dep_alias, required=dep_required)
        visiting.remove(capability_id)
        visited.add(capability_id)
        order.append(capability_id)

    visit(primary_capability_id, role="primary", depth=0)
    if primary_capability_id not in nodes:
        return None
    return DependencyGraph(
        primary_capability_id=primary_capability_id,
        nodes=[nodes[key] for key in sorted(nodes)],
        edges=sorted(edges, key=lambda edge: (edge.parent_capability_id, edge.child_capability_id, edge.alias)),
        topological_order=order,
        execution_owner_capability_id=primary_capability_id,
    )


def _effect_candidates(effect_id: str, graph: DependencyGraph) -> list[tuple[str, OperatorContract, OperatorEffect]]:
    needle = effect_id.strip().lower()
    out: list[tuple[str, OperatorContract, OperatorEffect]] = []
    for capability_id in graph.topological_order:
        op = get_capability(capability_id).operator_contract
        for effect in op.effects:
            names = {effect.effect_id.lower(), *(alias.lower() for alias in effect.aliases)}
            if needle in names:
                out.append((capability_id, op, effect))
    return out


def _resolve_effects(spec: Mapping[str, Any], graph: DependencyGraph, issues: list[PlanIssue]) -> list[EffectBinding]:
    events = spec.get("events") if isinstance(spec.get("events"), Mapping) else {}
    bindings: list[EffectBinding] = []
    for kind, key in (("fault", "faults"), ("degradation", "degradations"), ("constraint", "constraints")):
        raw_events = events.get(key) if isinstance(events.get(key), Sequence) else []
        for index, item in enumerate(raw_events):
            if not isinstance(item, Mapping):
                continue
            event_id = str(item.get("id") or f"{kind}_{index}")
            effect_id = str(item.get("effect") or "").strip()
            target = str(item.get("target") or "").strip()
            candidates = [entry for entry in _effect_candidates(effect_id, graph) if entry[2].kind == kind]
            if target:
                narrowed = [entry for entry in candidates if _effect_target_matches(target, entry[2].owner, entry[0])]
                if narrowed:
                    candidates = narrowed
            if not candidates:
                global_owners = find_effect_owners(effect_id, active_only=False)
                code = "EFFECT_OWNER_OUTSIDE_DEPENDENCY_GRAPH" if global_owners else "UNSUPPORTED_EFFECT"
                message = (
                    f"Effect {effect_id!r} is registered, but its owner is outside the selected capability dependency graph."
                    if global_owners
                    else f"Effect {effect_id!r} is not registered by any capability."
                )
                issues.append(PlanIssue("error", code, f"$.events.{key}[{index}]", message, {"event_id": event_id, "target": target, "global_owners": global_owners}))
                continue
            if len(candidates) > 1:
                issues.append(PlanIssue("error", "EFFECT_BINDING_AMBIGUOUS", f"$.events.{key}[{index}]", f"Effect {effect_id!r} matches multiple owners in the dependency graph.", {"event_id": event_id, "candidates": [cid for cid, _, _ in candidates]}))
                continue
            capability_id, op, effect = candidates[0]
            if not _effect_target_matches(target, effect.owner, capability_id):
                issues.append(PlanIssue("error", "EFFECT_TARGET_OWNER_MISMATCH", f"$.events.{key}[{index}].target", f"Target {target!r} does not match effect owner {effect.owner!r}.", {"event_id": event_id, "capability_id": capability_id}))
                continue
            evidence_profile = get_effect_evidence_profile(capability_id, effect.effect_id)
            if evidence_profile and (
                evidence_profile.classification == EffectEvidenceClassification.OUT_OF_SCOPE
                or not evidence_profile.executable
            ):
                issues.append(PlanIssue(
                    "error",
                    "EFFECT_OUT_OF_SCOPE",
                    f"$.events.{key}[{index}]",
                    f"Effect {effect_id!r} is registered for boundary reporting but is not executable; model extension is required.",
                    {
                        "event_id": event_id,
                        "capability_id": capability_id,
                        "classification": evidence_profile.classification.value,
                        "rationale": evidence_profile.rationale,
                    },
                ))
                continue
            assurance = spec.get("assurance") if isinstance(spec.get("assurance"), Mapping) else {}
            if (
                evidence_profile
                and evidence_profile.classification == EffectEvidenceClassification.PROJECT_PROXY
                and not bool(assurance.get("allow_proxy", False))
            ):
                issues.append(PlanIssue(
                    "error",
                    "PROXY_EFFECT_NOT_ALLOWED",
                    f"$.events.{key}[{index}]",
                    f"Effect {effect_id!r} is implemented as a project proxy and requires assurance.allow_proxy=true.",
                    {
                        "event_id": event_id,
                        "capability_id": capability_id,
                        "classification": evidence_profile.classification.value,
                    },
                ))
                continue
            bindings.append(EffectBinding(
                event_id=event_id,
                event_kind=kind,  # type: ignore[arg-type]
                requested_effect=effect_id,
                requested_target=target,
                owner=effect.owner,
                capability_id=capability_id,
                implementation=effect.implementation,
                adapter=op.public_adapter,
                verification=effect.verification,
                evidence_fields=list(effect.evidence_fields),
            ))
            if effect.verification == "candidate":
                issues.append(PlanIssue("warning", "EFFECT_EVIDENCE_CANDIDATE", f"$.effect_bindings[{event_id}]", f"Effect {effect_id!r} is bound, but its evidence fields remain candidate evidence until V26 runtime validation.", {"evidence_fields": list(effect.evidence_fields)}))
            if effect.verification == "not_observable":
                issues.append(PlanIssue("error", "EFFECT_NOT_OBSERVABLE", f"$.effect_bindings[{event_id}]", f"Effect {effect_id!r} has no observable evidence fields."))
    return bindings


def _field_matches(requested: str, declared: str) -> bool:
    return requested == declared or fnmatch.fnmatchcase(requested, declared) or fnmatch.fnmatchcase(declared, requested)


def _available_output_fields(graph: DependencyGraph) -> list[tuple[str, str, str, str]]:
    out: list[tuple[str, str, str, str]] = []
    for capability_id in graph.topological_order:
        op = get_capability(capability_id).operator_contract
        for source_kind, fields in (
            ("qoi", op.observability.qoi),
            ("trace", op.observability.trace_fields),
            ("produces", op.observability.produces),
        ):
            for field_name in fields:
                out.append((capability_id, op.owner, source_kind, field_name))
    return out


def _resolve_outputs(spec: Mapping[str, Any], graph: DependencyGraph, issues: list[PlanIssue]) -> list[OutputBinding]:
    outputs = spec.get("outputs") if isinstance(spec.get("outputs"), Mapping) else {}
    requested = [str(item) for item in (outputs.get("qoi") or []) if str(item).strip()]
    explicit = bool(requested)
    primary_op = get_capability(graph.primary_capability_id).operator_contract
    if not requested:
        requested = list(primary_op.observability.qoi)
        if not requested:
            requested = list(primary_op.observability.trace_fields)
    available = _available_output_fields(graph)
    bindings: list[OutputBinding] = []
    for index, field_name in enumerate(requested):
        candidates = [entry for entry in available if _field_matches(field_name, entry[3])]
        exact = [entry for entry in candidates if entry[3] == field_name]
        if exact:
            candidates = exact
        elif candidates and not any(token in field_name for token in ("*", "?", "[")):
            # A concrete field can match nested wildcard declarations such as
            # ``thermal.*`` and ``thermal.node.*``.  The narrower declaration
            # is authoritative; treating both as peers makes valid registry
            # outputs spuriously ambiguous.
            specificity = max(
                len(entry[3].replace("*", "").replace("?", ""))
                for entry in candidates
            )
            candidates = [
                entry
                for entry in candidates
                if len(entry[3].replace("*", "").replace("?", "")) == specificity
            ]
        primary = [entry for entry in candidates if entry[0] == graph.primary_capability_id]
        if primary:
            candidates = primary
        # The same physical field may be declared in multiple contract sections
        # (for example trace + produces).  Collapse exact duplicates and prefer
        # authoritative QoI declarations, then trace, then produced interfaces.
        source_rank = {"qoi": 0, "trace": 1, "produces": 2}
        dedup: dict[tuple[str, str], tuple[str, str, str, str]] = {}
        for entry in candidates:
            key = (entry[0], entry[3])
            current = dedup.get(key)
            if current is None or source_rank.get(entry[2], 99) < source_rank.get(current[2], 99):
                dedup[key] = entry
        unique = sorted(
            dedup.values(),
            key=lambda entry: (
                0 if entry[0] == graph.primary_capability_id else 1,
                source_rank.get(entry[2], 99),
                entry[0],
                entry[3],
            ),
        )
        if not unique:
            issues.append(PlanIssue("error", "REQUIRED_QOI_MISSING", f"$.outputs.qoi[{index}]", f"Requested output {field_name!r} is not declared by the primary capability or its dependency closure."))
            bindings.append(OutputBinding(requested_field=field_name, explicitly_requested=explicit, binding_status="unresolved"))
            continue
        # If all remaining candidates identify one declared field, source-section
        # duplication is not an ambiguity.  Prefer the primary capability and the
        # strongest source kind deterministically.
        declared_fields = {entry[3] for entry in unique}
        # Glob requests such as ``qoi.*`` intentionally select a set of
        # observable fields.  They are not ambiguous: expand them into one
        # deterministic binding per declared field.  Exact non-glob requests
        # that still match multiple fields remain an error.
        is_glob_request = any(token in field_name for token in ("*", "?", "["))
        if len(declared_fields) > 1 and is_glob_request:
            for capability_id, owner, source_kind, bound in unique:
                bindings.append(OutputBinding(
                    requested_field=field_name,
                    bound_field=bound,
                    capability_id=capability_id,
                    owner=owner,
                    source_kind=source_kind,  # type: ignore[arg-type]
                    explicitly_requested=explicit,
                ))
            continue
        if len(declared_fields) > 1:
            issues.append(PlanIssue("error", "OUTPUT_BINDING_AMBIGUOUS", f"$.outputs.qoi[{index}]", f"Requested output {field_name!r} matches multiple declared fields.", {"candidates": [entry[3] for entry in unique]}))
            bindings.append(OutputBinding(requested_field=field_name, explicitly_requested=explicit, binding_status="ambiguous"))
            continue
        capability_id, owner, source_kind, bound = unique[0]
        bindings.append(OutputBinding(
            requested_field=field_name,
            bound_field=bound,
            capability_id=capability_id,
            owner=owner,
            source_kind=source_kind,  # type: ignore[arg-type]
            explicitly_requested=explicit,
        ))
    return bindings


def _resolved_spec(spec: Mapping[str, Any], issues: list[PlanIssue]) -> ResolvedSpec | None:
    canonical = canonicalize_task_spec(spec)
    model = canonical.get("model") if isinstance(canonical.get("model"), Mapping) else {}
    capability_id = str(model.get("capability_id") or "").strip()
    if not capability_id:
        issues.append(PlanIssue("error", "PRIMARY_CAPABILITY_MISSING", "$.model.capability_id", "Canonical TaskSpec does not select a primary capability."))
        return None
    try:
        contract = get_capability(capability_id)
    except Exception as exc:
        issues.append(PlanIssue("error", "PRIMARY_CAPABILITY_UNKNOWN", "$.model.capability_id", str(exc)))
        return None
    op = contract.operator_contract
    requested_level = _normalized_level(str((canonical.get("simulation") or {}).get("level") or ""))
    resolved_level = _normalized_level(op.level)
    explicit_level = infer_explicit_request_level(_request_text(canonical))
    if explicit_level and explicit_level != requested_level:
        issues.append(PlanIssue("error", "REQUEST_LEVEL_CONFLICT", "$.simulation.level", f"Original request explicitly asks for {explicit_level}, but TaskSpec contains {requested_level}.", {"request_text": _request_text(canonical)}))
    if requested_level != resolved_level:
        issues.append(PlanIssue("error", "CAPABILITY_LEVEL_MISMATCH", "$.model.capability_id", f"Capability {capability_id} is level {resolved_level}, but TaskSpec requests {requested_level}."))

    graph = _dependency_graph(capability_id, issues)
    if graph is None:
        return None
    effects = _resolve_effects(canonical, graph, issues)
    output_bindings = _resolve_outputs(canonical, graph, issues)
    assurance = canonical.get("assurance") if isinstance(canonical.get("assurance"), Mapping) else {}
    mission = canonical.get("mission") if isinstance(canonical.get("mission"), Mapping) else {}
    integration = contract.integration_contract
    required_couplings = [str(item) for item in (mission.get("required_couplings") or [])]
    missing_couplings = sorted(set(required_couplings) - set(integration.supported_couplings))
    parameter_audit = audit_parameter_consumption(canonical)
    for path in parameter_audit.unknown_paths:
        issues.append(PlanIssue(
            "error", "PARAMETER_PATH_UNCONSUMED", path,
            "Parameter path is not declared by the selected capability and would not be consumed.",
        ))
    for path in parameter_audit.conflict_paths:
        issues.append(PlanIssue(
            "error", "PARAMETER_ALIAS_CONFLICT", path,
            "Canonical and compatibility parameter aliases contain conflicting values.",
        ))
    if parameter_audit.provenance_gaps:
        issues.append(PlanIssue(
            "warning", "PARAMETER_PROVENANCE_INCOMPLETE", "$.provenance.fields",
            f"{len(parameter_audit.provenance_gaps)} consumed parameter paths lack explicit provenance.",
            {"paths": list(parameter_audit.provenance_gaps)},
        ))
    if missing_couplings:
        issues.append(PlanIssue(
            "error", "MISSING_PHYSICAL_CAUSAL_LINK", "$.mission.required_couplings",
            f"Capability {capability_id!r} does not provide required physical couplings.",
            {"missing_couplings": missing_couplings, "supported_couplings": integration.supported_couplings},
        ))
    resolved = ResolvedSpec(
        task_id=str((canonical.get("task") or {}).get("id") or ""),
        canonical_task_spec_sha256=spec_sha256(canonical),
        requested_level=requested_level,
        resolved_level=resolved_level,
        primary_capability_id=capability_id,
        primary_owner=op.owner,
        primary_adapter=op.public_adapter,
        required_couplings=required_couplings,
        supported_couplings=list(integration.supported_couplings),
        source_modules=list(integration.source_modules),
        implementation_class=integration.implementation_class,
        capability_limitations=list(integration.limitations),
        parameter_consumption=parameter_audit.to_dict(),
        dependency_graph=graph,
        effect_bindings=effects,
        output_bindings=output_bindings,
        assurance=ResolvedAssurance(
            parameter_profile=str(assurance.get("parameter_profile") or "demo"),
            claim_level=str(assurance.get("claim_level") or "analysis_only"),
            validation_profile=str(assurance.get("validation_profile") or "default"),
            allow_proxy=bool(assurance.get("allow_proxy", False)),
            capability_trust_level=op.assurance.trust_level,
            capability_fidelity_level=op.assurance.fidelity_level,
            capability_can_claim_high_fidelity=op.assurance.can_claim_high_fidelity,
            limitations=list(op.assurance.limitations),
        ),
        resource_locks=list(op.execution.resource_locks),
        runtime_timeout_s=op.execution.timeout_s,
    )
    payload = resolved.model_dump(mode="json", exclude={"resolved_spec_sha256"})
    return resolved.model_copy(update={"resolved_spec_sha256": _stable_hash(payload)})


def _build_execution_plan(resolved: ResolvedSpec) -> ExecutionPlan:
    primary = resolved.primary_capability_id
    locks = list(resolved.resource_locks)
    nodes = [
        PlanNode(node_id="validate_task", node_type="task_validation", handler="v24.stage.task_validation", binding_status="available"),
        PlanNode(node_id="validate_guards", node_type="guard_validation", handler="v24.stage.guard_validation", binding_status="available"),
        PlanNode(node_id="resolve_dependencies", node_type="dependency_resolution", handler="v24.stage.dependency_resolution", binding_status="available", metadata={"dependency_order": resolved.dependency_graph.topological_order}),
        PlanNode(node_id="validate_causal_requirements", node_type="causal_requirement_validation", handler="v0564.stage.causal_requirement_validation", binding_status="available", metadata={"required_couplings": list(resolved.required_couplings), "supported_couplings": list(resolved.supported_couplings), "source_modules": list(resolved.source_modules), "implementation_class": resolved.implementation_class}),
        PlanNode(node_id="validate_parameter_consumption", node_type="parameter_consumption_validation", handler="v0566.stage.parameter_consumption_validation", binding_status="available", metadata=resolved.parameter_consumption),
        PlanNode(node_id="bind_effects", node_type="effect_binding", handler="v24.stage.effect_binding", binding_status="available", metadata={"binding_count": len(resolved.effect_bindings)}),
        PlanNode(node_id="bind_outputs", node_type="output_binding", handler="v24.stage.output_binding", binding_status="available", metadata={"binding_count": len(resolved.output_bindings)}),
        PlanNode(node_id="acquire_resources", node_type="resource_acquire", handler="v25.runtime.resource_acquire", binding_status="runtime_contract", resource_locks=locks),
        PlanNode(node_id="execute_primary", node_type="execute_primary_adapter", handler="v25.runtime.execute_primary_adapter", binding_status="runtime_contract", capability_id=primary, adapter=resolved.primary_adapter, timeout_s=resolved.runtime_timeout_s, resource_locks=locks, metadata={"dependency_execution_mode": "owned_by_primary_adapter"}),
        PlanNode(node_id="collect_evidence", node_type="evidence_collection", handler="v25.runtime.evidence_collection", binding_status="runtime_contract", metadata={"required_fields": sorted({field for binding in resolved.effect_bindings for field in binding.evidence_fields})}),
        PlanNode(node_id="validate_qoi", node_type="qoi_validation", handler="v25.runtime.qoi_validation", binding_status="runtime_contract", metadata={"output_bindings": [binding.model_dump(mode="json") for binding in resolved.output_bindings]}),
        PlanNode(node_id="build_claim_report", node_type="claim_report", handler="v26.runtime.claim_report", binding_status="runtime_contract"),
        PlanNode(node_id="release_resources", node_type="resource_release", handler="v25.runtime.resource_release", binding_status="runtime_contract", resource_locks=locks),
        PlanNode(node_id="terminal_success", node_type="terminal_success", handler="terminal", binding_status="terminal"),
        PlanNode(node_id="terminal_execution_failure", node_type="terminal_execution_failure", handler="terminal", binding_status="terminal"),
        PlanNode(node_id="terminal_mission_failure", node_type="terminal_mission_failure", handler="terminal", binding_status="terminal"),
        PlanNode(node_id="terminal_inconclusive", node_type="terminal_inconclusive", handler="terminal", binding_status="terminal"),
        PlanNode(node_id="terminal_cancelled", node_type="terminal_cancelled", handler="terminal", binding_status="terminal"),
    ]
    edges: list[PlanEdge] = []
    validation_chain = ["validate_task", "validate_guards", "resolve_dependencies", "validate_causal_requirements", "validate_parameter_consumption", "bind_effects", "bind_outputs", "acquire_resources"]
    for current, nxt in zip(validation_chain, validation_chain[1:]):
        edges.append(PlanEdge(from_node=current, to_node=nxt, condition="SUCCESS"))
        edges.append(PlanEdge(from_node=current, to_node="terminal_execution_failure", condition="FAILURE"))
    edges.extend([
        PlanEdge(from_node="acquire_resources", to_node="execute_primary", condition="SUCCESS"),
        PlanEdge(from_node="acquire_resources", to_node="terminal_execution_failure", condition="FAILURE"),
        PlanEdge(from_node="acquire_resources", to_node="terminal_cancelled", condition="CANCELLED"),
        PlanEdge(from_node="execute_primary", to_node="collect_evidence", condition="SUCCESS"),
        PlanEdge(from_node="execute_primary", to_node="release_resources", condition="FAILURE"),
        PlanEdge(from_node="execute_primary", to_node="release_resources", condition="CANCELLED"),
        PlanEdge(from_node="collect_evidence", to_node="validate_qoi", condition="SUCCESS"),
        PlanEdge(from_node="collect_evidence", to_node="release_resources", condition="FAILURE"),
        PlanEdge(from_node="collect_evidence", to_node="release_resources", condition="INCONCLUSIVE"),
        PlanEdge(from_node="validate_qoi", to_node="build_claim_report", condition="PASS"),
        PlanEdge(from_node="validate_qoi", to_node="build_claim_report", condition="FAIL"),
        PlanEdge(from_node="validate_qoi", to_node="build_claim_report", condition="INCONCLUSIVE"),
        PlanEdge(from_node="build_claim_report", to_node="release_resources", condition="SUCCESS"),
        PlanEdge(from_node="build_claim_report", to_node="release_resources", condition="FAILURE"),
        # Release routing is outcome-aware; V25 carries the preceding outcome in runtime state.
        PlanEdge(from_node="release_resources", to_node="terminal_success", condition="PASS"),
        PlanEdge(from_node="release_resources", to_node="terminal_execution_failure", condition="FAILURE"),
        PlanEdge(from_node="release_resources", to_node="terminal_mission_failure", condition="FAIL"),
        PlanEdge(from_node="release_resources", to_node="terminal_inconclusive", condition="INCONCLUSIVE"),
        PlanEdge(from_node="release_resources", to_node="terminal_cancelled", condition="CANCELLED"),
    ])
    plan = ExecutionPlan(
        task_id=resolved.task_id,
        canonical_task_spec_sha256=resolved.canonical_task_spec_sha256,
        resolved_spec_sha256=resolved.resolved_spec_sha256,
        primary_capability_id=primary,
        entry_node="validate_task",
        nodes=nodes,
        edges=edges,
        terminal_nodes=["terminal_success", "terminal_execution_failure", "terminal_mission_failure", "terminal_inconclusive", "terminal_cancelled"],
    )
    payload = plan.model_dump(mode="json", exclude={"plan_sha256"})
    return plan.model_copy(update={"plan_sha256": _stable_hash(payload)})


def validate_execution_plan(plan: ExecutionPlan, resolved: ResolvedSpec | None = None) -> PlanValidationReport:
    issues: list[PlanIssue] = []
    node_ids = [node.node_id for node in plan.nodes]
    known = set(node_ids)
    if len(node_ids) != len(known):
        issues.append(PlanIssue("error", "PLAN_NODE_DUPLICATE", "$.nodes", "ExecutionPlan contains duplicate node IDs."))
    expected_plan_hash = _stable_hash(plan.model_dump(mode="json", exclude={"plan_sha256"}))
    if plan.plan_sha256 != expected_plan_hash:
        issues.append(PlanIssue("error", "PLAN_HASH_MISMATCH", "$.plan_sha256", "ExecutionPlan hash does not match its canonical content.", {"expected": expected_plan_hash, "actual": plan.plan_sha256}))
    if plan.entry_node not in known:
        issues.append(PlanIssue("error", "PLAN_ENTRY_UNKNOWN", "$.entry_node", "ExecutionPlan entry node is unknown."))
    for edge in plan.edges:
        if edge.from_node not in known or edge.to_node not in known:
            issues.append(PlanIssue("error", "PLAN_EDGE_UNKNOWN_NODE", "$.edges", "ExecutionPlan edge references an unknown node.", edge.model_dump(mode="json")))

    adjacency: dict[str, list[str]] = {node: [] for node in known}
    for edge in plan.edges:
        if edge.from_node in adjacency and edge.to_node in known:
            adjacency[edge.from_node].append(edge.to_node)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            issues.append(PlanIssue("error", "PLAN_CYCLE", "$.edges", f"ExecutionPlan contains a cycle at {node}."))
            return
        if node in visited:
            return
        visiting.add(node)
        for child in adjacency.get(node, []):
            visit(child)
        visiting.remove(node)
        visited.add(node)

    if plan.entry_node in known:
        visit(plan.entry_node)
    unreachable = sorted(known - visited)
    if unreachable:
        issues.append(PlanIssue("error", "PLAN_NODE_UNREACHABLE", "$.nodes", "ExecutionPlan contains unreachable nodes.", {"nodes": unreachable}))
    edge_keys = [(edge.from_node, edge.to_node, edge.condition) for edge in plan.edges]
    if len(edge_keys) != len(set(edge_keys)):
        issues.append(PlanIssue("error", "PLAN_EDGE_DUPLICATE", "$.edges", "ExecutionPlan contains duplicate conditional edges."))

    required_conditions = {
        "execute_primary": {"SUCCESS", "FAILURE", "CANCELLED"},
        "validate_qoi": {"PASS", "FAIL", "INCONCLUSIVE"},
        "release_resources": {"PASS", "FAILURE", "FAIL", "INCONCLUSIVE", "CANCELLED"},
    }
    for node_id, expected in required_conditions.items():
        actual = {edge.condition for edge in plan.edges if edge.from_node == node_id}
        missing = sorted(expected - actual)
        if missing:
            issues.append(PlanIssue("error", "PLAN_BRANCH_INCOMPLETE", f"$.nodes[{node_id}]", f"Plan node {node_id!r} is missing required branches.", {"missing_conditions": missing, "actual_conditions": sorted(actual)}))

    execute_nodes = [node for node in plan.nodes if node.node_type == "execute_primary_adapter"]
    if len(execute_nodes) != 1:
        issues.append(PlanIssue("error", "PLAN_PRIMARY_EXECUTION_COUNT_INVALID", "$.nodes", "ExecutionPlan must contain exactly one primary adapter execution node.", {"count": len(execute_nodes)}))
    if any(node.node_type == "execute_primary_adapter" and node.capability_id != plan.primary_capability_id for node in execute_nodes):
        issues.append(PlanIssue("error", "PLAN_PRIMARY_EXECUTION_OWNER_INVALID", "$.nodes", "Primary execution node must be owned by the primary capability."))

    for terminal in plan.terminal_nodes:
        if terminal not in known:
            issues.append(PlanIssue("error", "PLAN_TERMINAL_UNKNOWN", "$.terminal_nodes", f"Terminal node {terminal!r} is unknown."))
        if any(edge.from_node == terminal for edge in plan.edges):
            issues.append(PlanIssue("error", "PLAN_TERMINAL_HAS_OUTGOING_EDGE", "$.edges", f"Terminal node {terminal!r} has outgoing edges."))
    expected_terminals = {"terminal_success", "terminal_execution_failure", "terminal_mission_failure", "terminal_inconclusive", "terminal_cancelled"}
    if set(plan.terminal_nodes) != expected_terminals:
        issues.append(PlanIssue("error", "PLAN_TERMINAL_SET_INCOMPLETE", "$.terminal_nodes", "ExecutionPlan must include success, execution failure, mission failure, inconclusive and cancelled terminals."))
    if resolved is not None:
        expected_resolved_hash = _stable_hash(resolved.model_dump(mode="json", exclude={"resolved_spec_sha256"}))
        if resolved.resolved_spec_sha256 != expected_resolved_hash:
            issues.append(PlanIssue("error", "RESOLVED_SPEC_HASH_MISMATCH", "$.resolved_spec.resolved_spec_sha256", "ResolvedSpec hash does not match its canonical content.", {"expected": expected_resolved_hash, "actual": resolved.resolved_spec_sha256}))
        if plan.resolved_spec_sha256 != resolved.resolved_spec_sha256:
            issues.append(PlanIssue("error", "PLAN_RESOLVED_HASH_MISMATCH", "$.resolved_spec_sha256", "ExecutionPlan does not reference the supplied ResolvedSpec hash."))
        if plan.primary_capability_id != resolved.primary_capability_id:
            issues.append(PlanIssue("error", "PLAN_PRIMARY_CAPABILITY_MISMATCH", "$.primary_capability_id", "ExecutionPlan primary capability differs from ResolvedSpec."))
        unresolved_effects = [binding.event_id for binding in resolved.effect_bindings if binding.binding_status != "resolved"]
        unresolved_outputs = [binding.requested_field for binding in resolved.output_bindings if binding.binding_status != "resolved"]
        if unresolved_effects:
            issues.append(PlanIssue("error", "PLAN_EFFECT_BINDING_UNRESOLVED", "$.resolved_spec.effect_bindings", "ExecutionPlan contains unresolved effect bindings.", {"events": unresolved_effects}))
        if unresolved_outputs:
            issues.append(PlanIssue("error", "PLAN_OUTPUT_BINDING_UNRESOLVED", "$.resolved_spec.output_bindings", "ExecutionPlan contains unresolved output bindings.", {"outputs": unresolved_outputs}))
    return PlanValidationReport(ok=not any(issue.severity == "error" for issue in issues), issues=tuple(issues))


def plan_task_spec(spec: Mapping[str, Any]) -> PlanningResult:
    issues: list[PlanIssue] = []
    validation = validate_task_spec(spec)
    for issue in validation.issues:
        issues.append(PlanIssue(
            "error" if issue.severity == "error" else "warning",
            issue.code or "TASKSPEC_VALIDATION_ISSUE",
            issue.path,
            issue.message,
            {"source": "task_validator"},
        ))
    hard_validation_errors = [issue for issue in validation.errors if issue.code not in {"capability"}]
    if hard_validation_errors:
        report = PlanValidationReport(False, tuple(issues))
        return PlanningResult(False, None, None, report)
    guards = evaluate_agent_guards(spec)
    for issue in guards.issues:
        issues.append(PlanIssue(
            "error" if issue.severity == "error" else "warning",
            issue.code,
            issue.path,
            issue.message,
            {"source": "agent_guards"},
        ))
    try:
        resolved = _resolved_spec(spec, issues)
    except Exception as exc:
        issues.append(PlanIssue("error", "RESOLVED_SPEC_BUILD_FAILED", "$", str(exc)))
        resolved = None
    if resolved is None or any(issue.severity == "error" for issue in issues):
        report = PlanValidationReport(False, tuple(issues))
        return PlanningResult(False, resolved, None, report)
    plan = _build_execution_plan(resolved)
    plan_report = validate_execution_plan(plan, resolved)
    all_issues = tuple([*issues, *plan_report.issues])
    report = PlanValidationReport(not any(issue.severity == "error" for issue in all_issues), all_issues)
    return PlanningResult(report.ok, resolved, plan if report.ok else None, report)



def resolved_spec_schema() -> dict[str, Any]:
    schema = ResolvedSpec.model_json_schema(mode="validation")
    schema["$id"] = "https://example.local/sat-sim/resolved-spec-v24.schema.json"
    schema["title"] = "Satellite Simulation ResolvedSpec V24"
    return schema


def execution_plan_schema() -> dict[str, Any]:
    schema = ExecutionPlan.model_json_schema(mode="validation")
    schema["$id"] = "https://example.local/sat-sim/execution-plan-v24.schema.json"
    schema["title"] = "Satellite Simulation ExecutionPlan V24"
    return schema

def attach_plan_metadata(compiled: Any, result: PlanningResult) -> Any:
    """Attach V24 hashes to a frozen ``CompiledTask`` without creating a dependency cycle."""

    if not result.ok or result.resolved_spec is None or result.execution_plan is None:
        return compiled
    metadata = dict(getattr(compiled, "metadata", {}) or {})
    metadata["v24_planning"] = {
        "resolved_spec_schema_version": result.resolved_spec.schema_version,
        "resolved_spec_sha256": result.resolved_spec.resolved_spec_sha256,
        "execution_plan_schema_version": result.execution_plan.schema_version,
        "execution_plan_sha256": result.execution_plan.plan_sha256,
        "primary_capability_id": result.resolved_spec.primary_capability_id,
    }
    return replace(compiled, metadata=metadata)


__all__ = [
    "RESOLVED_SPEC_VERSION",
    "EXECUTION_PLAN_VERSION",
    "CapabilityBinding",
    "DependencyEdge",
    "DependencyGraph",
    "EffectBinding",
    "OutputBinding",
    "ResolvedSpec",
    "PlanNode",
    "PlanEdge",
    "ExecutionPlan",
    "PlanIssue",
    "PlanValidationReport",
    "PlanningResult",
    "infer_explicit_request_level",
    "plan_task_spec",
    "validate_execution_plan",
    "resolved_spec_schema",
    "execution_plan_schema",
    "attach_plan_metadata",
]
