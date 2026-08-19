"""V23 unified TaskSpec creation workflow.

Natural language, schema-driven form payloads, direct YAML/JSON TaskSpecs and
incremental patches all converge on the same CanonicalTaskSpec v1 contract.
Only natural-language input may call a model backend.  Every path is validated
against the authoritative capability registry and Agent guards.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml

from .agent_facade import AgentFacadeRequest, AgentFacadeResult, AgentFacadeStep, run_agent
from .agent_guards import AgentGuardReport, evaluate_agent_guards
from .capability_agent import requested_health_mode
from .capability_registry import get_capability
from .model_router import ModelRouteConfig, ModelRouteDecision, RAG_BOUNDARY_POLICY, route_model
from .model_providers import ModelProviderRegistry
from .nl_outputs import apply_output_intents_to_spec
from .reason_codes import ReasonCode
from .script_exporter import ScriptExportResult, export_runner_script
from .task_compiler import CompiledTask, compile_task_spec
from .task_models import CANONICAL_TASK_SPEC_VERSION, canonicalize_task_spec
from .task_spec import spec_sha256, write_json
from .task_validator import ValidationResult, validate_task_spec
from .execution_planner import PlanningResult, attach_plan_metadata, plan_task_spec
from .coupling_requirements import attach_required_couplings

UNIFIED_AGENT_VERSION = "v24.unified-agent.v3"
InputKind = Literal["natural_language", "form", "task_spec", "patch"]


@dataclass(frozen=True)
class UnifiedAgentRequest:
    input_kind: InputKind = "natural_language"
    request_text: str = ""
    form_data: Mapping[str, Any] | None = None
    task_spec: Mapping[str, Any] | None = None
    base_task_spec: Mapping[str, Any] | None = None
    patch: Mapping[str, Any] | None = None
    output_dir: str | Path = "generated_tasks"
    examples_dir: str | Path = "examples"
    backend: str = "auto"
    local_backend: str = "template"
    remote_backend: str | None = None
    model_command: str | None = None
    model_name: str | None = None
    model_base_url: str | None = None
    model_api_key_env: str = "OPENAI_API_KEY"
    model_timeout_s: float = 60.0
    model_temperature: float | None = None
    model_seed: int | None = None
    model_max_output_tokens: int | None = None
    model_structured_output: str = "json_object"
    provider_id: str | None = None
    provider_config_path: str | Path | None = None
    routing_mode: Literal["legacy", "auto", "local", "remote"] = "legacy"
    task_id: str | None = None
    output_root: str | Path | None = None
    script_output: str | Path | None = None
    max_repair_attempts: int = 2
    compile_if_valid: bool = True
    experience_reuse_enabled: bool = False
    experience_store_root: str | Path | None = None
    experience_tenant_id: str | None = None
    experience_project_id: str | None = None


@dataclass(frozen=True)
class UnifiedAgentResult:
    ok: bool
    request: UnifiedAgentRequest
    route: ModelRouteDecision
    task_spec: dict[str, Any]
    validation: ValidationResult
    guards: AgentGuardReport
    compiled: CompiledTask | None = None
    script: ScriptExportResult | None = None
    facade_result: AgentFacadeResult | None = None
    files: dict[str, str] = field(default_factory=dict)
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    planning: PlanningResult | None = None

    def to_dict(self) -> dict[str, Any]:
        request = asdict(self.request)
        for key in (
            "output_dir",
            "examples_dir",
            "output_root",
            "script_output",
            "provider_config_path",
            "experience_store_root",
        ):
            if request.get(key) is not None:
                request[key] = str(request[key])
        for key in ("form_data", "task_spec", "base_task_spec", "patch"):
            if request.get(key) is not None:
                request[key] = dict(request[key])
        return {
            "ok": self.ok,
            "unified_agent_version": UNIFIED_AGENT_VERSION,
            "request": request,
            "route": self.route.to_dict(),
            "task_spec": self.task_spec,
            "task_spec_sha256": spec_sha256(self.task_spec) if self.task_spec else None,
            "validation": self.validation.to_dict(),
            "guards": self.guards.to_dict(),
            "compiled": self.compiled.to_dict() if self.compiled else None,
            "script": self.script.to_dict() if self.script else None,
            "facade_result": self.facade_result.to_dict() if self.facade_result else None,
            "files": dict(self.files),
            "reason_codes": list(self.reason_codes),
            "rag_boundary": RAG_BOUNDARY_POLICY,
            "planning": self.planning.to_dict() if self.planning else None,
        }


def _slug(text: str, default: str = "simulation_task") -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", text.strip()).strip("_.-")
    return (value[:96] or default)


def _mapping(value: Any) -> dict[str, Any]:
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _nested(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    return _mapping(mapping.get(key))


def _set_nested(mapping: dict[str, Any], dotted_path: str, value: Any) -> None:
    """Set a dotted path in a nested mapping without overwriting user values."""

    parts = [part for part in dotted_path.split(".") if part]
    if not parts:
        return
    current = mapping
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current.setdefault(parts[-1], copy.deepcopy(value))


def _contract_defaults(
    contract: Any,
    section: str,
    *,
    prefix: str,
    target: dict[str, Any],
) -> list[str]:
    """Apply registered defaults for dotted capability input sections.

    Whole-spacecraft contracts commonly place their public inputs in
    ``spacecraft_inputs`` and ``orbit_environment_inputs`` rather than the
    generic ``parameters`` section.  V28 release closure makes schema-driven
    forms honor those defaults as well, while preserving user-provided values.
    """

    applied: list[str] = []
    for name, payload in _mapping(contract.data.get(section)).items():
        if not isinstance(payload, Mapping) or "default" not in payload:
            continue
        path = str(name)
        if prefix and path.startswith(prefix + "."):
            path = path[len(prefix) + 1 :]
        before = copy.deepcopy(target)
        _set_nested(target, path, payload.get("default"))
        if target != before:
            applied.append(str(name))
    return applied


def _event_payload(item: Mapping[str, Any], *, kind: str, index: int, duration_s: float) -> dict[str, Any]:
    src = dict(item)
    effect = src.get("effect") or src.get("fault_type") or src.get("degradation_type") or src.get("constraint_type") or src.get("type")
    start = float(src.get("start_s", src.get("onset_time_s", 0.0)) or 0.0)
    end = src.get("end_s")
    if end is None and src.get("duration_s") not in (None, -1, -1.0):
        end = min(duration_s, start + float(src.get("duration_s") or 0.0))
    return {
        "id": str(src.get("id") or src.get(f"{kind}_id") or f"{kind}_{index + 1}"),
        "event_type": kind,
        "target": str(src.get("target") or "unspecified"),
        "effect": str(effect or "unspecified"),
        "start_s": start,
        **({"end_s": float(end)} if end is not None else {}),
        **({"magnitude": src.get("magnitude")} if src.get("magnitude") is not None else {}),
        **({"scale": src.get("scale")} if src.get("scale") is not None else {}),
        "implementation": str(src.get("implementation") or "auto"),
        "delivery": str(src.get("delivery") or ("legacy_fault" if kind == "fault" else "runtime_constraint" if kind == "constraint" else "legacy_degradation")),
        **({"target_type": src.get("target_type")} if src.get("target_type") else {}),
        "parameters": _mapping(src.get("parameters")),
    }


def _mode_for_form_events(
    *,
    faults: list[dict[str, Any]],
    degradations: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
    requested_mode: Any,
) -> str:
    """Derive the canonical target mode from actual form event content.

    Schema forms are initialized with ``nominal``.  Keeping that placeholder
    after users add events breaks downstream mode contracts, so non-empty event
    categories are authoritative.  Explicit non-nominal mode remains accepted
    only when it agrees with the event-derived mode; validation handles whether
    the selected capability supports that mode.
    """

    categories = [
        ("fault", bool(faults)),
        ("degradation", bool(degradations)),
        ("constraint", bool(constraints)),
    ]
    active = [name for name, present in categories if present]
    if len(active) > 1:
        derived = "mixed"
    elif active:
        derived = active[0]
    else:
        derived = "nominal"

    requested = str(requested_mode or "").strip().lower()
    if not active:
        return requested or "nominal"
    if requested in {derived, "mixed"} and derived == "mixed":
        return "mixed"
    return derived


def _form_to_canonical(form: Mapping[str, Any], request: UnifiedAgentRequest) -> dict[str, Any]:
    src = copy.deepcopy(dict(form))
    if src.get("schema_version") == CANONICAL_TASK_SPEC_VERSION:
        out = canonicalize_task_spec(src)
    else:
        model_src = _nested(src, "model")
        capability_id = str(model_src.get("capability_id") or src.get("capability_id") or "").strip()
        if not capability_id:
            raise ValueError("form input requires model.capability_id or capability_id")
        contract = get_capability(capability_id)
        sim_src = _nested(src, "simulation")
        duration_s = float(sim_src.get("duration_s", src.get("duration_s", 600.0)) or 600.0)
        sample_s = float(sim_src.get("sample_s", src.get("sample_s", min(60.0, duration_s))) or min(60.0, duration_s))
        level = str(sim_src.get("level") or src.get("level") or contract.target_level)
        task_src = _nested(src, "task")
        task_id = str(request.task_id or task_src.get("id") or src.get("task_id") or _slug(str(task_src.get("name") or capability_id)))
        parameter_src = _nested(src, "parameters")
        assurance_src = _nested(src, "assurance")
        profile = str(parameter_src.get("profile") or assurance_src.get("parameter_profile") or src.get("parameter_profile") or "demo")

        event_src = _nested(src, "events")
        fault_items = event_src.get("faults", src.get("faults", []))
        degradation_items = event_src.get("degradations", src.get("degradations", []))
        constraint_items = event_src.get("constraints", src.get("constraints", []))
        faults = [_event_payload(item, kind="fault", index=i, duration_s=duration_s) for i, item in enumerate(fault_items or []) if isinstance(item, Mapping)]
        degradations = [_event_payload(item, kind="degradation", index=i, duration_s=duration_s) for i, item in enumerate(degradation_items or []) if isinstance(item, Mapping)]
        constraints = [_event_payload(item, kind="constraint", index=i, duration_s=duration_s) for i, item in enumerate(constraint_items or []) if isinstance(item, Mapping)]
        mode = _mode_for_form_events(
            faults=faults,
            degradations=degradations,
            constraints=constraints,
            requested_mode=_nested(model_src, "target").get("mode") or src.get("mode"),
        )
        output_src = _nested(src, "outputs")
        output_root = str(request.output_root or output_src.get("output_root") or Path("datasets") / task_id)
        values = _mapping(parameter_src.get("values"))
        if not values:
            values = _mapping(src.get("parameter_values")) or {
                key: value for key, value in parameter_src.items()
                if key not in {"profile", "overrides", "registry_id", "registry_hash"}
            }
        defaulted_parameters: list[str] = []
        for name, payload in _mapping(contract.data.get("parameters")).items():
            if name in values or not isinstance(payload, Mapping) or "default" not in payload:
                continue
            values[name] = copy.deepcopy(payload.get("default"))
            defaulted_parameters.append(str(name))

        spacecraft_values = _mapping(model_src.get("spacecraft") or src.get("spacecraft"))
        orbit_environment_values = _mapping(model_src.get("orbit_environment") or src.get("orbit_environment"))
        # Orbit/environment capabilities expose their primary configuration through
        # parameters.values in the Schema form. Mirror those values into the
        # canonical model.orbit_environment payload required by the runtime adapter.
        if level == "orbit_environment" and not orbit_environment_values:
            orbit_environment_values = copy.deepcopy(values)
        defaulted_spacecraft = _contract_defaults(
            contract,
            "spacecraft_inputs",
            prefix="spacecraft",
            target=spacecraft_values,
        )
        defaulted_orbit_environment = _contract_defaults(
            contract,
            "orbit_environment_inputs",
            prefix="orbit_environment",
            target=orbit_environment_values,
        )
        # Whole-spacecraft validation requires an explicit spacecraft object.
        # When a capability contract has no spacecraft defaults at all, retain a
        # minimal, auditable marker instead of silently producing an invalid form.
        if level == "whole_spacecraft" and not spacecraft_values:
            spacecraft_values = {"mission": {"configuration_source": "form_default"}}
            defaulted_spacecraft.append("spacecraft.mission.configuration_source")

        out = canonicalize_task_spec({
            "schema_version": CANONICAL_TASK_SPEC_VERSION,
            "task": {
                "id": task_id,
                "name": task_src.get("name") or task_id,
                "description": task_src.get("description") or src.get("description") or "",
                "tags": list(task_src.get("tags") or src.get("tags") or []),
            },
            "simulation": {
                "level": level,
                **({"subsystem": sim_src.get("subsystem") or contract.target_name} if level == "subsystem" else {}),
                "duration_s": duration_s,
                "step_s": sim_src.get("step_s") or sample_s,
                "sample_s": sample_s,
                "random_seed": sim_src.get("random_seed", src.get("random_seed", 0)),
                "backend": sim_src.get("backend", "selective_unified_basilisk_assembly"),
                **({"epoch_utc": sim_src.get("epoch_utc")} if sim_src.get("epoch_utc") else {}),
            },
            "mission": _mapping(src.get("mission")),
            "parameters": {
                "profile": profile,
                "values": values,
                "overrides": list(parameter_src.get("overrides") or []),
                **({"registry_id": parameter_src.get("registry_id")} if parameter_src.get("registry_id") else {}),
            },
            "events": {"faults": faults, "degradations": degradations, "constraints": constraints},
            "outputs": {
                "output_root": output_root,
                "trace_format": output_src.get("trace_format", "csv"),
                "qoi": list(output_src.get("qoi") or src.get("qoi") or []),
                "files": list(output_src.get("files") or []),
                "plots": list(output_src.get("plots") or []),
                "telemetry_streams": [
                    copy.deepcopy(dict(item))
                    for item in (output_src.get("telemetry_streams") or [])
                    if isinstance(item, Mapping)
                ],
                "fmea": _mapping(output_src.get("fmea")),
                "include_summary": bool(output_src.get("include_summary", True)),
                "include_trace": bool(output_src.get("include_trace", True)),
                "include_labels": bool(output_src.get("include_labels", True)),
                "include_manifest": bool(output_src.get("include_manifest", True)),
            },
            "assurance": {
                "fidelity_level": assurance_src.get("fidelity_level", "declared_by_capability"),
                "claim_level": assurance_src.get("claim_level", src.get("claim_level", "analysis_only")),
                "validation_profile": assurance_src.get("validation_profile", "default"),
                "parameter_profile": profile,
                "allow_proxy": bool(assurance_src.get("allow_proxy", src.get("allow_proxy", False))),
            },
            "model": {
                "capability_id": capability_id,
                "target": {
                    "level": level,
                    "name": _nested(model_src, "target").get("name") or contract.target_name,
                    "mode": mode,
                },
                "spacecraft": spacecraft_values,
                "orbit_environment": orbit_environment_values,
                "config": _mapping(model_src.get("config")) or values,
                "legacy_degradations": _mapping(model_src.get("legacy_degradations")),
                "campaign": _mapping(model_src.get("campaign")),
                "simulation_extra": _mapping(model_src.get("simulation_extra")),
                "validation": _mapping(model_src.get("validation") or src.get("validation")),
            },
            "provenance": {
                "fields": {
                    "task.id": {"source": "user_provided" if (task_src.get("id") or src.get("task_id") or request.task_id) else "form_default", "evidence": "form"},
                    "simulation.level": {"source": "user_provided" if (sim_src.get("level") or src.get("level")) else "form_default", "evidence": "form/capability contract"},
                    "simulation.duration_s": {"source": "user_provided" if (sim_src.get("duration_s") or src.get("duration_s")) else "form_default", "evidence": "form"},
                    "simulation.sample_s": {"source": "user_provided" if (sim_src.get("sample_s") or src.get("sample_s")) else "form_default", "evidence": "form"},
                    "model.capability_id": {"source": "user_provided", "evidence": "form"},
                    **{
                        f"parameters.values.{name}": {
                            "source": "form_default",
                            "evidence": f"Capability Registry default: {capability_id}.parameters.{name}",
                        }
                        for name in defaulted_parameters
                    },
                    **{
                        f"model.{name}": {
                            "source": "form_default",
                            "evidence": f"Capability Registry default: {capability_id}.{name}",
                        }
                        for name in [*defaulted_spacecraft, *defaulted_orbit_environment]
                    },
                },
                "assumptions": [
                    *[
                        {
                            "path": f"parameters.values.{name}",
                            "value": values[name],
                            "reason": f"Applied registered default from {capability_id}",
                            "status": "accepted",
                        }
                        for name in defaulted_parameters
                    ],
                    *[
                        {
                            "path": f"model.{name}",
                            "value": None,
                            "reason": f"Applied registered default from {capability_id}",
                            "status": "accepted",
                        }
                        for name in [*defaulted_spacecraft, *defaulted_orbit_environment]
                    ],
                ],
                "pending_confirmations": [],
            },
            "metadata": {
                **_mapping(src.get("metadata")),
                "agent": {
                    "unified_agent_version": UNIFIED_AGENT_VERSION,
                    "input_kind": "form",
                    "registry_authority": True,
                    "rag_policy": RAG_BOUNDARY_POLICY["policy_version"],
                },
            },
        })
    out, _attached_couplings = attach_required_couplings(out, request.request_text)
    metadata = out.setdefault("metadata", {})
    metadata["agent"] = {
        **_mapping(metadata.get("agent")),
        "unified_agent_version": UNIFIED_AGENT_VERSION,
        "input_kind": "form",
        "registry_authority": True,
        "rag_policy": RAG_BOUNDARY_POLICY["policy_version"],
    }
    return canonicalize_task_spec(out)


def normalize_form_task_spec(
    form: Mapping[str, Any],
    *,
    task_id: str | None = None,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    """Normalize a Schema form to Canonical TaskSpec without invoking an Agent or writing files.

    This is the deterministic entry used by static scenario templates and other
    product surfaces that already have a complete, registry-backed form.
    """

    request = UnifiedAgentRequest(
        input_kind="form",
        form_data=form,
        task_id=task_id,
        output_root=output_root,
        compile_if_valid=False,
    )
    return _form_to_canonical(form, request)


def _merge_patch(target: Any, patch: Any) -> Any:
    """RFC 7396-style JSON Merge Patch."""
    if not isinstance(patch, Mapping):
        return copy.deepcopy(patch)
    result = copy.deepcopy(dict(target)) if isinstance(target, Mapping) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = _merge_patch(result.get(key), value)
    return result


def _leaf_paths(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, Mapping):
        out: list[str] = []
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.extend(_leaf_paths(child, path))
        return out
    return [prefix] if prefix else []


def _normalize_direct_input(request: UnifiedAgentRequest) -> tuple[dict[str, Any], str]:
    if request.input_kind == "form":
        if request.form_data is None:
            raise ValueError("form input requires form_data")
        return _form_to_canonical(request.form_data, request), ReasonCode.FORM_INPUT_NORMALIZED.value
    if request.input_kind == "task_spec":
        if request.task_spec is None:
            raise ValueError("task_spec input requires task_spec")
        out = canonicalize_task_spec(request.task_spec)
        metadata = out.setdefault("metadata", {})
        metadata["agent"] = {
            **_mapping(metadata.get("agent")),
            "unified_agent_version": UNIFIED_AGENT_VERSION,
            "input_kind": "task_spec",
            "registry_authority": True,
            "rag_policy": RAG_BOUNDARY_POLICY["policy_version"],
        }
        return canonicalize_task_spec(out), ReasonCode.TASKSPEC_INPUT_NORMALIZED.value
    if request.input_kind == "patch":
        if request.base_task_spec is None or request.patch is None:
            raise ValueError("patch input requires base_task_spec and patch")
        base = canonicalize_task_spec(request.base_task_spec)
        merged = _merge_patch(base, request.patch)
        merged["schema_version"] = CANONICAL_TASK_SPEC_VERSION
        provenance = merged.setdefault("provenance", {})
        fields = provenance.setdefault("fields", {})
        for path in _leaf_paths(request.patch):
            if path and not path.startswith("provenance"):
                fields[path] = {"source": "user_provided", "evidence": "TaskSpec merge patch"}
        metadata = merged.setdefault("metadata", {})
        metadata["agent"] = {
            **_mapping(metadata.get("agent")),
            "unified_agent_version": UNIFIED_AGENT_VERSION,
            "input_kind": "patch",
            "base_task_spec_sha256": spec_sha256(base),
            "registry_authority": True,
            "rag_policy": RAG_BOUNDARY_POLICY["policy_version"],
        }
        return canonicalize_task_spec(merged), ReasonCode.TASKSPEC_PATCH_APPLIED.value
    raise ValueError(f"unsupported direct input kind: {request.input_kind}")




def _stable_validation_reason_codes(validation: ValidationResult) -> list[str]:
    """Map legacy validator issue labels to stable product reason codes.

    Capability adapters predate the V20 reason-code catalog and may emit labels
    such as ``enum`` or ``capability_fault``.  Product reports must not expose
    those implementation labels as their only diagnosis.
    """

    codes: list[str] = []
    for issue in validation.errors:
        path = str(issue.path or "")
        message = str(issue.message or "").lower()
        code = str(issue.code or "")
        if "unknown fault_type" in message or "unknown degradation_type" in message or "unsupported for" in message:
            codes.append(ReasonCode.UNSUPPORTED_EFFECT.value)
        elif ("target_type" in path or ".target" in path) and ("must be" in message or "target" in message):
            codes.append(ReasonCode.EFFECT_TARGET_OWNER_MISMATCH.value)
        elif code == "required":
            codes.append(ReasonCode.REQUIRED_USER_INPUT.value)
        elif code:
            codes.append(code)
    return list(dict.fromkeys(codes))



_REFINEMENT_EFFECT_INTENT_RE = re.compile(
    r"故障|失效|卡滞|堵转|异常|退化|漂移|噪声增加|偏置|中断|关机|fault|failure|degrad|jam|stuck|bias|dropout|loss",
    re.IGNORECASE,
)
_REFINEMENT_OUTPUT_INTENT_RE = re.compile(r"输出|曲线|指标|qoi|trace|plot|遥测", re.IGNORECASE)
_REFINEMENT_ASSURANCE_INTENT_RE = re.compile(r"保真|声明等级|验证配置|参数配置|claim|fidelity|assurance", re.IGNORECASE)
_REFINEMENT_NAME_RE = re.compile(r"(?:命名为|名称(?:改)?为|叫做|任务名(?:改)?为)\s*[“\"']?([^”\"'，。\n]+)", re.IGNORECASE)


def _time_value_seconds(text: str, patterns: tuple[str, ...]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        value = float(match.group(1))
        unit = (match.group(2) or "秒").lower()
        if unit in {"分钟", "分", "min", "mins", "minute", "minutes"}:
            value *= 60.0
        elif unit in {"小时", "时", "h", "hr", "hour", "hours"}:
            value *= 3600.0
        return value
    return None



_CRITICAL_LOCKED_PATHS = (
    "model.capability_id",
    "model.target.level",
    "model.target.name",
    "target.level",
    "target.name",
    "simulation.level",
    "simulation.subsystem",
    "simulation.backend",
)


def _get_path(mapping: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = mapping
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _force_path(mapping: dict[str, Any], dotted_path: str, value: Any) -> None:
    if value is None:
        return
    parts = [part for part in dotted_path.split(".") if part]
    current: dict[str, Any] = mapping
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = copy.deepcopy(value)


def _canonicalize_candidate_for_refinement(base: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize a model refinement draft after restoring locked identity fields.

    Local models often return a partial or near-canonical object.  Refinement mode
    must not let such drafts delete capability identity, target ownership or the
    subsystem required by the existing task.  This repair is performed before
    Pydantic validation so that a missing ``simulation.subsystem`` cannot block a
    harmless output-only instruction.
    """

    draft = copy.deepcopy(dict(candidate)) if isinstance(candidate, Mapping) else {}
    for path in _CRITICAL_LOCKED_PATHS:
        base_value = _get_path(base, path)
        if base_value is not None:
            _force_path(draft, path, base_value)
    # Canonical drafts use model.*, while some local prompts still return legacy
    # top-level capability/target fields.  Keep both coherent until migration.
    cap = _get_path(base, "model.capability_id")
    is_canonical = draft.get("schema_version") == CANONICAL_TASK_SPEC_VERSION and "model" in draft
    if cap and not is_canonical:
        draft.setdefault("capability_id", cap)
    target_level = _get_path(base, "target.level") or _get_path(base, "model.target.level")
    target_name = _get_path(base, "target.name") or _get_path(base, "model.target.name")
    if target_level or target_name:
        if is_canonical:
            model = draft.setdefault("model", {})
            if isinstance(model, dict):
                target = model.setdefault("target", {})
                if isinstance(target, dict):
                    if target_level is not None:
                        target.setdefault("level", target_level)
                    if target_name is not None:
                        target.setdefault("name", target_name)
                    target.setdefault("mode", _get_path(base, "model.target.mode") or "nominal")
            draft.pop("capability_id", None)
            draft.pop("target", None)
        else:
            target = draft.setdefault("target", {})
            if isinstance(target, dict):
                if target_level is not None:
                    target.setdefault("level", target_level)
                if target_name is not None:
                    target.setdefault("name", target_name)
                target.setdefault("mode", _get_path(base, "target.mode") or _get_path(base, "model.target.mode") or "nominal")
    return canonicalize_task_spec(draft)


def _record_patch_ops(metadata: dict[str, Any], changed_paths: list[str]) -> None:
    ops = metadata.setdefault("natural_language_patch_ops", [])
    if not isinstance(ops, list):
        ops = []
        metadata["natural_language_patch_ops"] = ops
    for path in sorted(set(changed_paths)):
        ops.append({"op": "replace_or_append", "path": "/" + path.replace(".", "/")})


def _experience_advisory_payload(request: UnifiedAgentRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "enabled": bool(request.experience_reuse_enabled),
        "role": "advisory_only",
        "approved_experience_advisories": [],
        "lesson_ids": [],
        "registry_validator_planner_authoritative": True,
    }
    if not request.experience_reuse_enabled:
        return payload
    if not (
        request.experience_store_root
        and request.experience_tenant_id
        and request.experience_project_id
    ):
        raise ValueError("experience reuse requires store root, tenant_id and project_id")

    capability_id = ""
    mode = None
    if request.input_kind == "natural_language":
        from .capability_planner import plan_capability_for_request

        plan = plan_capability_for_request(request.request_text)
        capability_id = str(plan.selected_capability_id or "")
        mode = requested_health_mode(request.request_text)
    else:
        source = request.task_spec or request.form_data or request.base_task_spec or {}
        model = source.get("model") if isinstance(source, Mapping) else {}
        if isinstance(model, Mapping):
            capability_id = str(model.get("capability_id") or "")
            target = model.get("target")
            if isinstance(target, Mapping):
                mode = str(target.get("mode") or "") or None
        if not capability_id and isinstance(source, Mapping):
            capability_id = str(source.get("capability_id") or "")
            mode = mode or (str(source.get("mode") or "") or None)
    if not capability_id:
        payload["reason"] = "capability_not_resolved"
        return payload

    from .experience import ExperienceScope, ExperienceStore
    from .experience_lessons import ExperienceLessonService

    scope = ExperienceScope(
        tenant_id=request.experience_tenant_id,
        project_id=request.experience_project_id,
    )
    service = ExperienceLessonService(ExperienceStore(request.experience_store_root))
    lessons = service.retrieve_approved(
        scope=scope,
        capability_id=capability_id,
        mode=mode,
        task_spec_version=CANONICAL_TASK_SPEC_VERSION,
    )
    advisories = [
        {
            "lesson_id": lesson.lesson_id,
            "lesson_sha256": lesson.lesson_sha256,
            "kind": lesson.kind,
            "capability_ids": list(lesson.capability_ids),
            "modes": list(lesson.modes),
            "effects": list(lesson.effects),
            "advisory": lesson.advisory,
            "safety_labels": list(lesson.safety_labels),
        }
        for lesson in lessons
    ]
    payload.update(
        {
            "capability_id": capability_id,
            "mode": mode,
            "approved_experience_advisories": advisories,
            "lesson_ids": [item["lesson_id"] for item in advisories],
            "hit_count": len(advisories),
            "influence_attribution": (
                "presented_to_model"
                if advisories and request.input_kind == "natural_language"
                else "available_but_no_model_generation"
                if advisories
                else "no_approved_match"
            ),
        }
    )
    return payload

def _merge_natural_language_refinement(
    base_task_spec: Mapping[str, Any],
    candidate_task_spec: Mapping[str, Any],
    instruction: str,
) -> tuple[dict[str, Any], list[str]]:
    """Apply a guarded incremental update to an existing canonical TaskSpec.

    Refinement mode intentionally keeps the selected capability and target fixed.
    A user who wants a different simulation object can uncheck “基于当前配置继续补充”
    and create a new TaskSpec.  This prevents a small instruction such as changing
    the duration from silently replacing a reaction-wheel task with an ADCS task.
    """

    base = canonicalize_task_spec(base_task_spec)
    candidate = _canonicalize_candidate_for_refinement(base, candidate_task_spec)
    out = copy.deepcopy(base)
    changed: list[str] = []
    text = str(instruction or "")

    base_capability = str(base.get("model", {}).get("capability_id") or "")
    candidate_capability = str(candidate.get("model", {}).get("capability_id") or "")
    same_capability = base_capability == candidate_capability

    duration = _time_value_seconds(text, (
        r"(?:仿真时长|运行时长|持续时间|运行时间|仿真时间)\D{0,16}(\d+(?:\.\d+)?)\s*(秒|s|sec|分钟|分|min|小时|时|h)?",
        r"(?:运行|仿真)\s*(\d+(?:\.\d+)?)\s*(秒|s|sec|分钟|分|min|小时|时|h)",
    ))
    sample = _time_value_seconds(text, (
        r"(?:输出采样间隔|采样间隔|采样周期|输出间隔)\D{0,16}(\d+(?:\.\d+)?)\s*(秒|s|sec|分钟|分|min)?",
    ))
    step = _time_value_seconds(text, (
        r"(?:积分步长|仿真步长|计算步长|步长)\D{0,16}(\d+(?:\.\d+)?)\s*(秒|s|sec)?",
    ))
    for key, value in (("duration_s", duration), ("sample_s", sample), ("step_s", step)):
        if value is not None:
            out.setdefault("simulation", {})[key] = value
            changed.append(f"simulation.{key}")

    if re.search(r"随机种子|random\s*seed|seed", text, re.IGNORECASE):
        match = re.search(r"(?:随机种子|random\s*seed|seed)\D{0,8}(-?\d+)", text, re.IGNORECASE)
        if match:
            out.setdefault("simulation", {})["random_seed"] = int(match.group(1))
            changed.append("simulation.random_seed")

    # Only accept generated parameter values when the model respected the current
    # capability.  Unknown keys are never introduced into the existing contract.
    if same_capability:
        base_values = out.setdefault("parameters", {}).setdefault("values", {})
        candidate_values = candidate.get("parameters", {}).get("values", {})
        if isinstance(base_values, dict) and isinstance(candidate_values, Mapping):
            for key in tuple(base_values):
                if key in candidate_values and candidate_values[key] != base_values[key]:
                    base_values[key] = copy.deepcopy(candidate_values[key])
                    changed.append(f"parameters.values.{key}")

    if _REFINEMENT_EFFECT_INTENT_RE.search(text):
        candidate_events = candidate.get("events")
        if isinstance(candidate_events, Mapping):
            out["events"] = copy.deepcopy(dict(candidate_events))
            changed.append("events")

    if _REFINEMENT_OUTPUT_INTENT_RE.search(text):
        before_outputs = copy.deepcopy(out.get("outputs"))
        out, output_resolution = apply_output_intents_to_spec(out, text)
        if out.get("outputs") != before_outputs:
            changed.append("outputs.plots")
        if output_resolution.changed:
            changed.append("metadata.agent.output_intent_resolution")
        # Model-provided output lists are intentionally not copied during
        # refinements.  Output fields must be resolved deterministically from the
        # current capability registry; otherwise template defaults from a
        # different route can leak fields such as label.health_state into the
        # current ADCS task.

    if _REFINEMENT_ASSURANCE_INTENT_RE.search(text):
        candidate_assurance = candidate.get("assurance")
        if isinstance(candidate_assurance, Mapping):
            out["assurance"] = copy.deepcopy(dict(candidate_assurance))
            changed.append("assurance")

    name_match = _REFINEMENT_NAME_RE.search(text)
    if name_match:
        out.setdefault("task", {})["name"] = name_match.group(1).strip()
        changed.append("task.name")

    metadata = out.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        out["metadata"] = metadata
    history = metadata.setdefault("natural_language_refinements", [])
    if not isinstance(history, list):
        history = []
        metadata["natural_language_refinements"] = history
    for path in _CRITICAL_LOCKED_PATHS:
        _force_path(out, path, _get_path(base, path))
    _record_patch_ops(metadata, changed)
    history.append({
        "instruction": text,
        "base_capability_id": base_capability,
        "candidate_capability_id": candidate_capability,
        "capability_locked": True,
        "locked_paths": list(_CRITICAL_LOCKED_PATHS),
        "changed_paths": sorted(set(changed)),
    })
    return canonicalize_task_spec(out), sorted(set(changed))


def _rebuild_refined_facade(
    facade: AgentFacadeResult,
    refined: dict[str, Any],
    changed_paths: list[str],
    request: UnifiedAgentRequest,
    output_dir: Path,
) -> AgentFacadeResult:
    task_yaml = output_dir / "task_spec.yaml"
    task_yaml.write_text(yaml.safe_dump(refined, sort_keys=False, allow_unicode=True), encoding="utf-8")
    validation = validate_task_spec(refined)
    guards = evaluate_agent_guards(refined)
    planning: PlanningResult | None = None
    compiled: CompiledTask | None = None
    script: ScriptExportResult | None = None
    if validation.ok and guards.ok:
        planning = plan_task_spec(refined)
    if validation.ok and guards.ok and planning is not None and planning.ok and request.compile_if_valid:
        compiled = attach_plan_metadata(compile_task_spec(refined), planning)
        script_path = Path(request.script_output) if request.script_output else output_dir / "generated_simulation.py"
        script = export_runner_script(task_yaml, script_path, kind="capability-python", output_root=request.output_root)

    files = dict(facade.files)
    files["task_spec_yaml"] = str(task_yaml)
    files["task_spec_json"] = str(write_json(output_dir / "task_spec.json", refined))
    files["validation"] = str(write_json(output_dir / "validation.json", validation.to_dict()))
    files["guard_report"] = str(write_json(output_dir / "guard_report.json", guards.to_dict()))
    if planning is not None:
        files["plan_validation"] = str(write_json(output_dir / "plan_validation.json", planning.validation.to_dict()))
        if planning.resolved_spec is not None:
            resolved = planning.resolved_spec.model_dump(mode="json")
            files["resolved_spec_json"] = str(write_json(output_dir / "resolved_spec.json", resolved))
            resolved_yaml = output_dir / "resolved_spec.yaml"
            resolved_yaml.write_text(yaml.safe_dump(resolved, sort_keys=False, allow_unicode=True), encoding="utf-8")
            files["resolved_spec_yaml"] = str(resolved_yaml)
        if planning.execution_plan is not None:
            files["execution_plan"] = str(write_json(output_dir / "execution_plan.json", planning.execution_plan.model_dump(mode="json")))
    if compiled is not None:
        files["compiled_task"] = str(write_json(output_dir / "compiled_task.json", compiled.to_dict()))
    if script is not None:
        files["generated_script"] = str(script.output_path)
        files["script_export"] = str(write_json(output_dir / "script_export.json", script.to_dict()))

    ok = validation.ok and guards.ok and planning is not None and planning.ok and (compiled is not None or not request.compile_if_valid)
    step = AgentFacadeStep(
        "natural_language_refinement_guard",
        "complete" if ok else "failed",
        "Locked the current capability and applied only bounded incremental changes.",
        reason_codes=("NATURAL_LANGUAGE_REFINEMENT_APPLIED", "REFINEMENT_CAPABILITY_LOCKED"),
        payload={"changed_paths": changed_paths},
    )
    planning_issue_codes = tuple(item.code for item in planning.validation.issues) if planning is not None else ()
    validation_step = AgentFacadeStep(
        "refinement_validation",
        "complete" if validation.ok and guards.ok and (planning is None or planning.ok) else "failed",
        "Revalidated the refined TaskSpec against the current capability registry and execution planner.",
        reason_codes=tuple(dict.fromkeys([
            *(item.code for item in validation.issues),
            *(item.code for item in guards.issues),
            *planning_issue_codes,
        ])),
    )
    preserved_steps = tuple(item for item in facade.steps if item.name not in {"canonicalize", "guard", "plan", "compile"})
    refined_reason_codes = tuple(dict.fromkeys([
        "NATURAL_LANGUAGE_REFINEMENT_APPLIED",
        "REFINEMENT_CAPABILITY_LOCKED",
        *(item.code for item in validation.issues),
        *(item.code for item in guards.issues),
        *planning_issue_codes,
    ]))
    result = AgentFacadeResult(
        ok=ok,
        request=facade.request,
        task_spec=refined,
        validation=validation,
        guards=guards,
        compiled=compiled,
        script=script,
        run_result=None,
        run_bundle_result=None,
        planning=planning,
        output_dir=facade.output_dir,
        files=files,
        steps=preserved_steps + (step, validation_step),
        reason_codes=refined_reason_codes,
    )
    agent_report = output_dir / "agent_report.json"
    files["agent_report"] = str(agent_report)
    result = AgentFacadeResult(**{**result.__dict__, "files": files})
    write_json(agent_report, result.to_dict())
    return result


def run_unified_agent(request: UnifiedAgentRequest) -> UnifiedAgentResult:
    output_dir = Path(request.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_provider = None
    effective_backend = request.backend
    effective_command = request.model_command
    effective_model = request.model_name
    effective_base_url = request.model_base_url
    effective_api_key_env = request.model_api_key_env
    effective_timeout_s = request.model_timeout_s
    effective_temperature = request.model_temperature
    effective_max_tokens = request.model_max_output_tokens
    effective_structured_output = request.model_structured_output
    experience_advisory = _experience_advisory_payload(request)
    write_json(output_dir / "experience_advisory.json", experience_advisory)
    knowledge_policy = {
        **RAG_BOUNDARY_POLICY,
        "approved_experience_advisories": experience_advisory[
            "approved_experience_advisories"
        ],
        "experience_influence": {
            "lesson_ids": experience_advisory["lesson_ids"],
            "attribution": experience_advisory.get("influence_attribution"),
        },
    }

    if request.routing_mode != "legacy" or request.provider_id:
        provider_selection = ModelProviderRegistry(config_path=request.provider_config_path).select(
            input_kind=request.input_kind,
            request_text=request.request_text,
            routing_mode="auto" if request.routing_mode == "legacy" else request.routing_mode,
            provider_id=request.provider_id,
        )
        selected_provider = provider_selection.provider
        route = provider_selection.route
        effective_backend = selected_provider.backend
        effective_command = selected_provider.command
        effective_model = selected_provider.model
        effective_base_url = selected_provider.base_url
        effective_api_key_env = selected_provider.api_key_env or request.model_api_key_env
        effective_timeout_s = selected_provider.timeout_s
        effective_temperature = selected_provider.temperature
        effective_max_tokens = selected_provider.max_output_tokens
        effective_structured_output = selected_provider.structured_output
        write_json(output_dir / "model_provider_route.json", provider_selection.to_dict())
    else:
        route_config = ModelRouteConfig(
            local_backend=request.local_backend,
            remote_backend=request.remote_backend,
            remote_model=request.model_name,
            remote_base_url=request.model_base_url,
            remote_command=request.model_command,
        )
        if request.backend != "auto":
            if request.backend == "deterministic":
                route_config = ModelRouteConfig(local_backend="deterministic", force_tier="L0")
            elif request.backend == request.local_backend:
                route_config = ModelRouteConfig(local_backend=request.backend, force_tier="L1")
            else:
                route_config = ModelRouteConfig(
                    local_backend=request.local_backend,
                    remote_backend=request.backend,
                    remote_model=request.model_name,
                    remote_base_url=request.model_base_url,
                    remote_command=request.model_command,
                    force_tier="L2",
                )
        route = route_model(input_kind=request.input_kind, request_text=request.request_text, config=route_config)
    write_json(output_dir / "model_route.json", route.to_dict())
    write_json(output_dir / "rag_boundary.json", RAG_BOUNDARY_POLICY)

    if request.input_kind == "natural_language":
        effective_request_text = request.request_text
        if request.base_task_spec is not None:
            base = canonicalize_task_spec(request.base_task_spec)
            effective_request_text = (
                "这是一次基于现有 TaskSpec 的多轮补充。请输出完整、更新后的 TaskSpec；"
                "保留用户未要求修改的能力、参数、事件和输出，不要凭空增加故障或退化。\n"
                f"现有 TaskSpec：\n{json.dumps(base, ensure_ascii=False)}\n"
                f"本轮补充指令：\n{request.request_text}"
            )
        facade = run_agent(AgentFacadeRequest(
            request=effective_request_text,
            output_dir=output_dir,
            examples_dir=request.examples_dir,
            backend=route.backend if effective_backend == "auto" else effective_backend,
            task_id=request.task_id,
            output_root=request.output_root,
            script_output=request.script_output,
            max_repair_attempts=request.max_repair_attempts,
            model_command=effective_command,
            model_name=effective_model,
            model_base_url=effective_base_url,
            model_api_key_env=effective_api_key_env,
            model_timeout_s=effective_timeout_s,
            model_temperature=effective_temperature,
            model_seed=request.model_seed,
            model_max_output_tokens=effective_max_tokens,
            model_structured_output=effective_structured_output,
            input_kind=request.input_kind,
            model_route=route.to_dict(),
            knowledge_policy=knowledge_policy,
        ))
        if request.base_task_spec is not None and facade.task_spec:
            refined_spec, changed_paths = _merge_natural_language_refinement(
                request.base_task_spec, facade.task_spec, request.request_text,
            )
            facade = _rebuild_refined_facade(facade, refined_spec, changed_paths, request, output_dir)
        files = {
            **facade.files,
            "model_route": str(output_dir / "model_route.json"),
            "rag_boundary": str(output_dir / "rag_boundary.json"),
            "experience_advisory": str(output_dir / "experience_advisory.json"),
        }
        if selected_provider is not None:
            files["model_provider_route"] = str(output_dir / "model_provider_route.json")
        codes = tuple(dict.fromkeys([
            *route.reason_codes,
            ReasonCode.RAG_ADVISORY_ONLY.value,
            *(["EXPERIENCE_ADVISORY_PRESENTED"] if experience_advisory["lesson_ids"] else []),
            *( ["NATURAL_LANGUAGE_REFINEMENT_APPLIED"] if request.base_task_spec is not None else [] ),
            *facade.reason_codes,
        ]))
        result = UnifiedAgentResult(
            facade.ok, request, route, facade.task_spec, facade.validation, facade.guards,
            facade.compiled, facade.script, facade, files, codes, facade.planning,
        )
        report = output_dir / "unified_agent_report.json"
        files["unified_agent_report"] = str(report)
        result = UnifiedAgentResult(**{**result.__dict__, "files": files})
        write_json(report, result.to_dict())
        return result

    canonical, input_reason = _normalize_direct_input(request)
    validation = validate_task_spec(canonical)
    guards = evaluate_agent_guards(canonical)
    compiled: CompiledTask | None = None
    script: ScriptExportResult | None = None
    planning: PlanningResult | None = None
    if validation.ok and guards.ok:
        planning = plan_task_spec(canonical)
    if validation.ok and guards.ok and planning is not None and planning.ok and request.compile_if_valid:
        compiled = attach_plan_metadata(compile_task_spec(canonical), planning)
        if request.script_output:
            script = export_runner_script(canonical, request.script_output, kind="capability-python", output_root=request.output_root)
    task_yaml = output_dir / "task_spec.yaml"
    task_yaml.write_text(yaml.safe_dump(canonical, sort_keys=False, allow_unicode=True), encoding="utf-8")
    files = {
        "task_spec_yaml": str(task_yaml),
        "task_spec_json": str(write_json(output_dir / "task_spec.json", canonical)),
        "validation": str(write_json(output_dir / "validation.json", validation.to_dict())),
        "guard_report": str(write_json(output_dir / "guard_report.json", guards.to_dict())),
        "model_route": str(output_dir / "model_route.json"),
        "rag_boundary": str(output_dir / "rag_boundary.json"),
        "experience_advisory": str(output_dir / "experience_advisory.json"),
    }
    if selected_provider is not None:
        files["model_provider_route"] = str(output_dir / "model_provider_route.json")
    if planning is not None:
        files["plan_validation"] = str(write_json(output_dir / "plan_validation.json", planning.validation.to_dict()))
        if planning.resolved_spec is not None:
            resolved_payload = planning.resolved_spec.model_dump(mode="json")
            files["resolved_spec_json"] = str(write_json(output_dir / "resolved_spec.json", resolved_payload))
            resolved_yaml = output_dir / "resolved_spec.yaml"
            resolved_yaml.write_text(yaml.safe_dump(resolved_payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
            files["resolved_spec_yaml"] = str(resolved_yaml)
        if planning.execution_plan is not None:
            files["execution_plan"] = str(write_json(output_dir / "execution_plan.json", planning.execution_plan.model_dump(mode="json")))
    if compiled:
        files["compiled_plan"] = str(write_json(output_dir / "compiled_plan.json", compiled.to_dict()))
    if script:
        files["script"] = str(script.path)
    planning_codes = [issue.code for issue in planning.validation.issues] if planning else []
    codes = tuple(dict.fromkeys([
        *route.reason_codes,
        input_reason,
        ReasonCode.RAG_ADVISORY_ONLY.value,
        *(issue.code for issue in guards.issues),
        *_stable_validation_reason_codes(validation),
        *planning_codes,
    ]))
    ok = validation.ok and guards.ok and planning is not None and planning.ok and (compiled is not None or not request.compile_if_valid)
    result = UnifiedAgentResult(ok, request, route, canonical, validation, guards, compiled, script, None, files, codes, planning)
    report = output_dir / "unified_agent_report.json"
    files["unified_agent_report"] = str(report)
    result = UnifiedAgentResult(**{**result.__dict__, "files": files})
    write_json(report, result.to_dict())
    return result


__all__ = [
    "UNIFIED_AGENT_VERSION",
    "UnifiedAgentRequest",
    "UnifiedAgentResult",
    "normalize_form_task_spec",
    "run_unified_agent",
]
