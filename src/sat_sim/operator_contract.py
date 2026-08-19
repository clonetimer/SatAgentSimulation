"""V22 normalized executable operator contracts.

Capability YAML files accumulated over several simulation releases.  They already
contain the authoritative adapter, mode, parameter, dependency and output
metadata, but not every file uses one identical layout.  This module builds one
strict, machine-readable ``OperatorContract`` view without inventing physics.

Explicit ``operator`` blocks in a capability YAML override derived values.  All
other values are conservatively derived and carry provenance in ``derivation``.
The normalized view is intended for Agent prompts, pre-flight checks and V24
execution planning; the underlying capability YAML remains the source of truth.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .capability_composition import contract_interface_summary, normalize_dependencies
from .effect_evidence import EffectEvidenceClassification, get_effect_evidence_profile

OPERATOR_CONTRACT_VERSION = "v22.operator-contract.v1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OperatorEffect(_Strict):
    effect_id: str = Field(min_length=1)
    kind: Literal["nominal", "fault", "degradation", "constraint"]
    owner: str = Field(min_length=1)
    implementation: Literal[
        "basilisk_native",
        "source_native",
        "proxy",
        "orchestration",
        "unknown",
    ] = "unknown"
    aliases: list[str] = Field(default_factory=list)
    evidence_fields: list[str] = Field(default_factory=list)
    verification: Literal["declared", "candidate", "not_observable"] = "candidate"


class OperatorPreconditions(_Strict):
    required_inputs: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    required_assets: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class OperatorObservability(_Strict):
    qoi: list[str] = Field(default_factory=list)
    trace_fields: list[str] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)
    effect_evidence: dict[str, list[str]] = Field(default_factory=dict)


class OperatorExecutionPolicy(_Strict):
    runtime_run: bool = False
    script_export: bool = False
    idempotent: bool = True
    timeout_s: float = Field(default=300.0, gt=0)
    retryable_failures: list[str] = Field(
        default_factory=lambda: ["worker_unavailable", "transient_output_io_failure"]
    )
    non_retryable_failures: list[str] = Field(
        default_factory=lambda: [
            "unsupported_effect",
            "capability_validation_failed",
            "mission_requirement_failed",
            "runtime_injection_unverified",
        ]
    )
    resource_locks: list[str] = Field(default_factory=lambda: ["run_output_directory"])


class OperatorAssuranceBoundary(_Strict):
    trust_level: str = "unknown"
    fidelity_level: str = "unknown"
    backend_type: str = "unknown"
    basilisk_required: bool = False
    can_claim_high_fidelity: bool = False
    parameter_profile_ceiling: str | None = None
    claim_level_ceiling: str | None = None
    delegated_to_parameter_guard: bool = True
    delegated_to_claim_guard: bool = True
    limitations: list[str] = Field(default_factory=list)


class OperatorContract(_Strict):
    schema_version: Literal[OPERATOR_CONTRACT_VERSION] = OPERATOR_CONTRACT_VERSION
    capability_id: str = Field(min_length=1)
    level: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    domain: str | None = None
    public_adapter: str = Field(min_length=1)
    public_entrypoints: list[str] = Field(default_factory=list)
    preconditions: OperatorPreconditions = Field(default_factory=OperatorPreconditions)
    effects: list[OperatorEffect] = Field(default_factory=list)
    observability: OperatorObservability = Field(default_factory=OperatorObservability)
    dependencies: list[dict[str, Any]] = Field(default_factory=list)
    execution: OperatorExecutionPolicy = Field(default_factory=OperatorExecutionPolicy)
    assurance: OperatorAssuranceBoundary = Field(default_factory=OperatorAssuranceBoundary)
    lifecycle: dict[str, Any] = Field(default_factory=dict)
    derivation: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_effect_ids(self) -> "OperatorContract":
        ids = [effect.effect_id for effect in self.effects]
        if len(ids) != len(set(ids)):
            raise ValueError("operator effect_id values must be unique")
        return self


@dataclass(frozen=True)
class OperatorContractIssue:
    severity: Literal["error", "warning"]
    capability_id: str
    path: str
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return [value]


def _field_names(block: Any) -> list[str]:
    out: list[str] = []
    for item in _list(block):
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, Mapping) and isinstance(item.get("name"), str):
            out.append(str(item["name"]).strip())
    return list(dict.fromkeys(name for name in out if name))


def _implementation_kind(data: Mapping[str, Any]) -> str:
    explicit = _mapping(data.get("operator")).get("implementation")
    if explicit in {"basilisk_native", "source_native", "proxy", "orchestration", "unknown"}:
        return str(explicit)
    implementation = _mapping(data.get("implementation"))
    boundary = _mapping(data.get("backend_boundary"))
    source = _mapping(data.get("source_binding"))
    backend_type = str(data.get("backend_type") or boundary.get("backend_type") or "").lower()
    if bool(implementation.get("basilisk_required")) or bool(boundary.get("current_agent_path_uses_basilisk")):
        return "basilisk_native"
    if "proxy" in backend_type:
        return "proxy"
    if str(source.get("mode") or "").lower() == "source_native":
        return "source_native"
    if backend_type or data.get("adapter"):
        return "orchestration"
    return "unknown"


def _required_inputs(data: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for name, payload in _mapping(data.get("simulation_parameters")).items():
        if isinstance(payload, Mapping) and payload.get("required"):
            out.append(f"simulation.{name}")
    for item in _list(data.get("consumes")):
        if isinstance(item, Mapping) and item.get("required") and item.get("name"):
            out.append(str(item["name"]))
    for name, payload in _mapping(data.get("parameters")).items():
        if isinstance(payload, Mapping) and payload.get("required"):
            out.append(f"parameters.{name}")
    return list(dict.fromkeys(out))


def _explicit_effects(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = _mapping(data.get("operator")).get("effects")
    return [dict(item) for item in _list(raw) if isinstance(item, Mapping)]


def _derived_effects(data: Mapping[str, Any], *, owner: str, evidence: list[str]) -> list[dict[str, Any]]:
    modes = _mapping(data.get("modes"))
    implementation = _implementation_kind(data)
    effects: list[dict[str, Any]] = []
    nominal = _mapping(modes.get("nominal"))
    if nominal.get("supported", True):
        effects.append({
            "effect_id": "nominal",
            "kind": "nominal",
            "owner": owner,
            "implementation": implementation,
            "evidence_fields": evidence,
            "verification": "declared" if evidence else "not_observable",
        })
    for key, kind, type_key in (
        ("fault", "fault", "fault_types"),
        ("degradation", "degradation", "degradation_types"),
        ("constraint", "constraint", "constraint_types"),
    ):
        payload = _mapping(modes.get(key))
        if not payload.get("supported"):
            continue
        for effect_id in _list(payload.get(type_key)):
            if not isinstance(effect_id, str) or not effect_id.strip():
                continue
            effects.append({
                "effect_id": effect_id.strip(),
                "kind": kind,
                "owner": owner,
                "implementation": implementation,
                "evidence_fields": evidence,
                # Existing contracts declare outputs but often do not yet make a
                # one-to-one effect/QoI assertion.  Treat them as candidate
                # evidence until V24/V26 binds and validates them explicitly.
                "verification": "candidate" if evidence else "not_observable",
            })
    return effects


def normalize_operator_contract(data: Mapping[str, Any]) -> OperatorContract:
    """Build one strict OperatorContract from a capability contract mapping."""

    capability_id = str(data.get("capability_id") or "").strip()
    level = str(data.get("level") or "").strip()
    operator = _mapping(data.get("operator"))
    owner = str(operator.get("owner") or data.get("target") or data.get("domain") or "").strip()
    adapter = _mapping(data.get("adapter"))
    public_adapter = str(adapter.get("class_path") or "").strip()
    interface = contract_interface_summary(data)
    outputs = _mapping(data.get("outputs"))
    qoi = _field_names(outputs.get("qoi")) or _field_names(outputs.get("summary"))
    trace = _field_names(outputs.get("trace_fields")) or _field_names(outputs.get("trace")) or list(interface.get("trace_fields") or [])
    produces = _field_names(data.get("produces"))
    candidate_evidence = list(dict.fromkeys([*qoi, *trace, *produces]))

    effect_payloads = _explicit_effects(data) or _derived_effects(
        data, owner=owner or "unknown", evidence=candidate_evidence
    )
    effect_evidence = {
        str(item.get("effect_id")): list(item.get("evidence_fields") or [])
        for item in effect_payloads
        if item.get("effect_id")
    }

    explicit_pre = _mapping(operator.get("preconditions"))
    preconditions = {
        "required_inputs": list(explicit_pre.get("required_inputs") or _required_inputs(data)),
        "required_capabilities": list(
            explicit_pre.get("required_capabilities")
            or [str(item.get("capability_id")) for item in normalize_dependencies(data) if item.get("required", True)]
        ),
        "required_assets": list(explicit_pre.get("required_assets") or []),
        "constraints": list(explicit_pre.get("constraints") or []),
    }

    explicit_obs = _mapping(operator.get("observability"))
    observability = {
        "qoi": list(explicit_obs.get("qoi") or qoi),
        "trace_fields": list(explicit_obs.get("trace_fields") or explicit_obs.get("trace") or trace),
        "produces": list(explicit_obs.get("produces") or produces),
        "effect_evidence": dict(explicit_obs.get("effect_evidence") or effect_evidence),
    }

    explicit_exec = _mapping(operator.get("execution"))
    execution = {
        "runtime_run": bool(explicit_exec.get("runtime_run", adapter.get("runtime_run", False))),
        "script_export": bool(explicit_exec.get("script_export", adapter.get("script_export", False))),
        "idempotent": bool(explicit_exec.get("idempotent", True)),
        "timeout_s": float(explicit_exec.get("timeout_s", 300.0)),
        "retryable_failures": list(explicit_exec.get("retryable_failures") or ["worker_unavailable", "transient_output_io_failure"]),
        "non_retryable_failures": list(explicit_exec.get("non_retryable_failures") or [
            "unsupported_effect", "capability_validation_failed", "mission_requirement_failed", "runtime_injection_unverified"
        ]),
        "resource_locks": list(explicit_exec.get("resource_locks") or ["run_output_directory"]),
    }

    implementation = _mapping(data.get("implementation"))
    boundary = _mapping(data.get("backend_boundary"))
    explicit_assurance = _mapping(operator.get("assurance"))
    assurance = {
        "trust_level": str(data.get("trust_level") or "unknown"),
        "fidelity_level": str(data.get("fidelity_level") or "unknown"),
        "backend_type": str(data.get("backend_type") or boundary.get("backend_type") or "unknown"),
        "basilisk_required": bool(implementation.get("basilisk_required", boundary.get("basilisk_required", False))),
        "can_claim_high_fidelity": bool(data.get("can_claim_high_fidelity", boundary.get("can_claim_high_fidelity", False))),
        "parameter_profile_ceiling": explicit_assurance.get("parameter_profile_ceiling"),
        "claim_level_ceiling": explicit_assurance.get("claim_level_ceiling"),
        "delegated_to_parameter_guard": bool(explicit_assurance.get("delegated_to_parameter_guard", True)),
        "delegated_to_claim_guard": bool(explicit_assurance.get("delegated_to_claim_guard", True)),
        "limitations": list(data.get("known_physics_limits") or []),
    }

    public_api = [str(item) for item in _list(_mapping(data.get("source_binding")).get("public_api")) if str(item).strip()]
    return OperatorContract.model_validate({
        "schema_version": OPERATOR_CONTRACT_VERSION,
        "capability_id": capability_id,
        "level": level,
        "owner": owner,
        "domain": str(data.get("domain")) if data.get("domain") is not None else None,
        "public_adapter": public_adapter,
        "public_entrypoints": public_api,
        "preconditions": preconditions,
        "effects": effect_payloads,
        "observability": observability,
        "dependencies": normalize_dependencies(data),
        "execution": execution,
        "assurance": assurance,
        "lifecycle": dict(_mapping(data.get("lifecycle"))),
        "derivation": {
            "mode": "explicit_operator_block" if operator else "derived_from_capability_contract",
            "source_schema_version": data.get("schema_version"),
            "effect_qoi_binding_status": "explicit" if _mapping(operator.get("observability")).get("effect_evidence") else "candidate_only",
        },
    })


def validate_operator_contract(contract: OperatorContract) -> list[OperatorContractIssue]:
    issues: list[OperatorContractIssue] = []
    cid = contract.capability_id
    if not contract.public_adapter:
        issues.append(OperatorContractIssue("error", cid, "$.public_adapter", "OPERATOR_PUBLIC_ADAPTER_MISSING", "Capability does not expose a public adapter."))
    if not contract.effects:
        issues.append(OperatorContractIssue("error", cid, "$.effects", "OPERATOR_EFFECTS_MISSING", "Operator contract has no nominal/fault/degradation effects."))
    for effect in contract.effects:
        profile = get_effect_evidence_profile(cid, effect.effect_id)
        intentional_out_of_scope = bool(
            profile and profile.classification == EffectEvidenceClassification.OUT_OF_SCOPE
        )
        if effect.kind != "nominal" and not effect.evidence_fields and not intentional_out_of_scope:
            issues.append(OperatorContractIssue(
                "warning", cid, f"$.effects[{effect.effect_id}].evidence_fields",
                "OPERATOR_EFFECT_EVIDENCE_MISSING",
                f"Effect {effect.effect_id!r} has no declared observable evidence field.",
            ))
        elif effect.kind != "nominal" and effect.verification == "candidate":
            issues.append(OperatorContractIssue(
                "warning", cid, f"$.effects[{effect.effect_id}].verification",
                "OPERATOR_EFFECT_EVIDENCE_CANDIDATE",
                f"Effect {effect.effect_id!r} uses candidate output evidence and requires explicit V24/V26 binding.",
            ))
    if contract.execution.runtime_run and not contract.observability.qoi and not contract.observability.trace_fields:
        issues.append(OperatorContractIssue(
            "warning", cid, "$.observability", "OPERATOR_OBSERVABILITY_EMPTY",
            "Runnable capability declares no QoI or trace fields.",
        ))
    return issues


__all__ = [
    "OPERATOR_CONTRACT_VERSION",
    "OperatorContract",
    "OperatorContractIssue",
    "OperatorEffect",
    "normalize_operator_contract",
    "validate_operator_contract",
]
