"""Deterministic Agent permission, assurance and claim guards."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .capability_registry import get_capability
from .reason_codes import ReasonCode
from .task_models import canonicalize_task_spec

ALLOWED_AGENT_TOOLS: tuple[str, ...] = (
    "list_capabilities",
    "get_capability_contract",
    "get_taskspec_schema",
    "validate_taskspec",
    "migrate_taskspec",
    "resolve_taskspec",
    "build_execution_plan",
    "validate_execution_plan",
    "compile_taskspec",
    "export_reproducible_script",
    "prepare_registered_run",
    "commit_execution_plan",
    "run_registered_simulation",
    "inspect_run_artifacts",
    "verify_run_bundle",
    "evaluate_validation_outcome",
    "build_claim_report",
)

FORBIDDEN_AGENT_TOOLS: tuple[str, ...] = (
    "write_arbitrary_python",
    "execute_arbitrary_python",
    "import_private_module",
    "construct_basilisk_object_directly",
    "modify_simulation_source_code",
    "write_basilisk_output_message",
)

PROFILE_RANK = {
    "demo": 0,
    "analysis_only": 0,
    "engineering_estimate": 1,
    "ground_calibrated": 2,
    "flight_correlated": 3,
}


@dataclass(frozen=True)
class GuardIssue:
    code: str
    severity: str
    path: str
    message: str
    evidence: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentGuardReport:
    ok: bool
    issues: tuple[GuardIssue, ...] = field(default_factory=tuple)
    allowed_claims: tuple[str, ...] = field(default_factory=tuple)
    forbidden_claims: tuple[str, ...] = field(default_factory=tuple)
    tool_policy: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [item.to_dict() for item in self.issues],
            "allowed_claims": list(self.allowed_claims),
            "forbidden_claims": list(self.forbidden_claims),
            "tool_policy": dict(self.tool_policy),
        }


def agent_tool_policy_payload() -> dict[str, Any]:
    return {
        "policy_version": "v26.agent-tool-policy.v3",
        "allowed_tools": list(ALLOWED_AGENT_TOOLS),
        "forbidden_tools": list(FORBIDDEN_AGENT_TOOLS),
        "arbitrary_code_execution": False,
        "private_api_access": False,
        "direct_basilisk_object_construction": False,
    }


def validate_agent_tool(tool_name: str) -> GuardIssue | None:
    if tool_name in ALLOWED_AGENT_TOOLS:
        return None
    code = ReasonCode.ARBITRARY_CODE_TOOL_FORBIDDEN
    if "private" in tool_name or tool_name.startswith("_"):
        code = ReasonCode.PRIVATE_API_TOOL_FORBIDDEN
    return GuardIssue(str(code), "error", "$.agent.tool", f"Agent tool {tool_name!r} is not permitted by the semantic tool whitelist.")


def evaluate_agent_guards(spec: Mapping[str, Any]) -> AgentGuardReport:
    canonical = canonicalize_task_spec(spec)
    assurance = canonical.get("assurance", {})
    parameters = canonical.get("parameters", {})
    model = canonical.get("model", {})
    events = canonical.get("events", {})
    issues: list[GuardIssue] = []

    parameter_profile = str(parameters.get("profile") or assurance.get("parameter_profile") or "demo")
    claim_level = str(assurance.get("claim_level") or "analysis_only")
    if PROFILE_RANK.get(claim_level, 99) > PROFILE_RANK.get(parameter_profile, -1):
        issues.append(GuardIssue(
            str(ReasonCode.CLAIM_EXCEEDS_PARAMETER_PROFILE),
            "error",
            "$.assurance.claim_level",
            f"claim_level={claim_level!r} exceeds parameter_profile={parameter_profile!r}.",
            {"claim_level": claim_level, "parameter_profile": parameter_profile},
        ))

    allow_proxy = bool(assurance.get("allow_proxy", False))
    for kind in ("faults", "degradations", "constraints"):
        for idx, event in enumerate(events.get(kind, []) or []):
            if event.get("implementation") == "proxy" and not allow_proxy:
                issues.append(GuardIssue(
                    str(ReasonCode.PROXY_NOT_ALLOWED),
                    "error",
                    f"$.events.{kind}[{idx}].implementation",
                    "Proxy implementation was requested but assurance.allow_proxy is false.",
                ))

    capability_id = model.get("capability_id")
    capability = None
    if capability_id:
        try:
            capability = get_capability(str(capability_id))
        except Exception as exc:
            issues.append(GuardIssue(str(ReasonCode.CAPABILITY_UNKNOWN), "error", "$.model.capability_id", str(exc)))
    if capability is not None:
        can_claim_hf = bool(capability.data.get("can_claim_high_fidelity", False))
        fidelity = str(assurance.get("fidelity_level") or "")
        if fidelity in {"high", "high_fidelity", "engineering_high_fidelity"} and not can_claim_hf:
            issues.append(GuardIssue(
                str(ReasonCode.HIGH_FIDELITY_CLAIM_BLOCKED),
                "error",
                "$.assurance.fidelity_level",
                f"Capability {capability.capability_id} does not permit a high-fidelity claim.",
                capability.data.get("claim_guardrail") or (capability.data.get("backend_boundary") or {}).get("claim_guardrail"),
            ))

    allowed_claims = ["taskspec_schema_valid", "capability_plan_compilable"]
    forbidden_claims = ["flight_validated", "hardware_calibrated", "certified_high_fidelity"]
    if parameter_profile == "engineering_estimate" and not issues:
        allowed_claims.append("engineering_parameter_profile")
    if parameter_profile == "ground_calibrated" and not issues:
        allowed_claims.append("ground_calibrated_parameter_profile")
    if claim_level == "flight_correlated" and not issues:
        allowed_claims.append("flight_correlated_parameter_profile")
    if capability is not None and capability.data.get("can_claim_high_fidelity", False) and not issues:
        allowed_claims.append("capability_declared_high_fidelity")

    return AgentGuardReport(
        ok=not any(item.severity == "error" for item in issues),
        issues=tuple(issues),
        allowed_claims=tuple(allowed_claims),
        forbidden_claims=tuple(forbidden_claims),
        tool_policy=agent_tool_policy_payload(),
    )


__all__ = [
    "ALLOWED_AGENT_TOOLS",
    "FORBIDDEN_AGENT_TOOLS",
    "GuardIssue",
    "AgentGuardReport",
    "agent_tool_policy_payload",
    "validate_agent_tool",
    "evaluate_agent_guards",
]
