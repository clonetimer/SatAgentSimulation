"""V27 formal Golden Set evaluation for the constrained simulation Agent.

The evaluator measures more than simple generation success.  It distinguishes
correct acceptance from false acceptance/rejection, checks registry-grounded
routing/effect ownership/output binding, detects silent semantic degradation and
over-claiming, and repeats cases to measure deterministic reliability.
"""
from __future__ import annotations

import json
import math
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .dag_harness import SimulationDAGHarness
from .dag_mutations import DAGMutation
from .run_bundle import execute_prepared_run, prepare_run, verify_run_bundle
from .task_spec import spec_sha256, write_json
from .unified_agent import UnifiedAgentRequest, run_unified_agent

GOLDEN_SET_VERSION = "v27.agent-golden-set.v1"
GOLDEN_EVAL_VERSION = "v27.agent-golden-eval.v1"


@dataclass(frozen=True)
class GoldenRepetitionResult:
    repetition: int
    passed: bool
    accepted: bool
    task_spec_sha256: str | None
    execution_plan_sha256: str | None
    capability_id: str | None
    level: str | None
    route_tier: str | None
    validation_result: str | None
    injection_delivery_results: tuple[str, ...] = field(default_factory=tuple)
    injection_effect_results: tuple[str, ...] = field(default_factory=tuple)
    run_bundle_integrity_ok: bool | None = None
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    effect_bindings: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    output_bindings: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    guard_allowed_claims: tuple[str, ...] = field(default_factory=tuple)
    guard_forbidden_claims: tuple[str, ...] = field(default_factory=tuple)
    allowed_claims: tuple[str, ...] = field(default_factory=tuple)
    forbidden_claims: tuple[str, ...] = field(default_factory=tuple)
    dag_state_hash: str | None = None
    dag_mutation_count: int = 0
    dag_path: str | None = None
    dag_mutation_log: str | None = None
    generated_python_policy: dict[str, Any] = field(default_factory=dict)
    checks: dict[str, bool] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GoldenCaseResult:
    case_id: str
    category: str
    expected_accept: bool
    passed: bool
    false_accept: bool
    false_reject: bool
    repetitions: tuple[GoldenRepetitionResult, ...]
    deterministic_task_spec: bool | None
    deterministic_plan: bool | None
    route_flip: bool
    claim_flip: bool
    silent_degradation: bool
    overclaim: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["repetitions"] = [item.to_dict() for item in self.repetitions]
        return payload


@dataclass(frozen=True)
class GoldenEvalReport:
    schema_version: str
    golden_set_version: str
    ok: bool
    manifest: str
    output_dir: str
    repeat_count: int
    total: int
    passed: int
    failed: int
    metrics: dict[str, Any]
    cases: tuple[GoldenCaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "golden_set_version": self.golden_set_version,
            "ok": self.ok,
            "manifest": self.manifest,
            "output_dir": self.output_dir,
            "repeat_count": self.repeat_count,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "metrics": dict(self.metrics),
            "cases": [case.to_dict() for case in self.cases],
        }


def _load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    text = manifest_path.read_text(encoding="utf-8")
    data = json.loads(text) if manifest_path.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("Golden Set manifest root must be an object")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Golden Set manifest must contain a non-empty cases list")
    ids = [str(item.get("case_id")) for item in cases if isinstance(item, Mapping)]
    if len(ids) != len(cases) or len(ids) != len(set(ids)):
        raise ValueError("Golden Set case_id values must be unique non-empty strings")
    return data


def _nested(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _request_from_case(case: Mapping[str, Any], *, output_dir: Path) -> UnifiedAgentRequest:
    input_kind = str(case.get("input_kind") or "form")
    payload = case.get("input") if isinstance(case.get("input"), Mapping) else {}
    common = {
        "input_kind": input_kind,
        "output_dir": output_dir,
        "backend": str(case.get("backend") or ("template" if input_kind == "natural_language" else "deterministic")),
        "local_backend": str(case.get("local_backend") or "template"),
        "remote_backend": case.get("remote_backend"),
        "compile_if_valid": True,
    }
    if input_kind == "natural_language":
        return UnifiedAgentRequest(request_text=str(payload.get("request_text") or case.get("request_text") or ""), **common)
    if input_kind == "form":
        return UnifiedAgentRequest(form_data=dict(payload.get("form") or payload), **common)
    if input_kind == "task_spec":
        return UnifiedAgentRequest(task_spec=dict(payload.get("task_spec") or payload), **common)
    if input_kind == "patch":
        return UnifiedAgentRequest(
            base_task_spec=dict(payload.get("base_task_spec") or {}),
            patch=dict(payload.get("patch") or {}),
            **common,
        )
    raise ValueError(f"unsupported Golden Set input_kind: {input_kind}")


def _script_policy(output_dir: Path, files: Mapping[str, Any]) -> dict[str, Any]:
    generated_script = files.get("generated_script")
    script_export = files.get("script_export")
    script_path = Path(str(generated_script)) if generated_script else None
    export_path = Path(str(script_export)) if script_export else None
    policy = {
        "arbitrary_python_generated": False,
        "deterministic_script_export": bool(script_path and export_path),
        "generated_script": str(script_path) if script_path else None,
        "script_export": str(export_path) if export_path else None,
        "reason": "no python script emitted",
    }
    if not script_path:
        return policy
    if not export_path or not export_path.is_file():
        policy.update({
            "arbitrary_python_generated": True,
            "reason": "python script exists without script_export provenance",
        })
        return policy
    try:
        export = json.loads(export_path.read_text(encoding="utf-8"))
    except Exception as exc:
        policy.update({
            "arbitrary_python_generated": True,
            "reason": f"script_export provenance is unreadable: {exc}",
        })
        return policy
    expected_path = str(script_path)
    if export.get("kind") != "capability-python" or str(export.get("output_path")) != expected_path:
        policy.update({
            "arbitrary_python_generated": True,
            "reason": "python script provenance is not a deterministic capability-python export",
        })
        return policy
    policy["reason"] = "python script is a deterministic capability-python export, not model-authored arbitrary code"
    return policy


def _write_dag_evidence(
    *,
    case: Mapping[str, Any],
    repetition: int,
    case_dir: Path,
    request: UnifiedAgentRequest | None,
    task_spec: Mapping[str, Any],
    task_hash: str | None,
    plan_hash: str | None,
    capability_id: str | None,
    level: str | None,
    route_tier: str | None,
    validation_ok: bool,
    guards_ok: bool,
    accepted: bool,
    rejection_reason: str | None = None,
) -> dict[str, Any]:
    dag_dir = case_dir / "dag_evidence"
    if dag_dir.exists():
        shutil.rmtree(dag_dir)
    harness = SimulationDAGHarness.from_root(str(dag_dir))
    if request is not None:
        source_payload = request.form_data or request.task_spec or request.patch or {}
        source_request = request.request_text or json.dumps(source_payload, ensure_ascii=False, sort_keys=True)
    else:
        source_request = json.dumps(case.get("input") or {}, ensure_ascii=False, sort_keys=True)
    dag, create = harness.create_dag(
        dag_id=f"{case.get('case_id')}_repeat_{repetition:02d}",
        actor="sat-agent-eval",
        mutation_id=f"{case.get('case_id')}.r{repetition:02d}.create",
        source_request=source_request,
        provenance={
            "case_id": str(case.get("case_id")),
            "repetition": repetition,
            "input_kind": str(case.get("input_kind") or "form"),
            "manifest_expected_accept": bool((case.get("expected") or {}).get("accept", True)) if isinstance(case.get("expected"), Mapping) else True,
        },
    )
    mutations = [create]

    def commit(operation: str, payload: dict[str, Any]) -> None:
        nonlocal dag
        mutation = DAGMutation(
            mutation_id=f"{case.get('case_id')}.r{repetition:02d}.{len(mutations):02d}.{operation}",
            base_dag_hash=dag.state_hash,
            actor="sat-agent-eval",
            operation=operation,  # type: ignore[arg-type]
            payload=payload,
        )
        dag, recorded = harness.commit(mutation)
        mutations.append(recorded)

    nodes = [
        {
            "node_id": "intent_parse",
            "node_type": "intent_parse",
            "label": "Input request",
            "payload": {"input_kind": str(case.get("input_kind") or "form")},
            "provenance": {"source": "golden_set"},
        },
        {
            "node_id": "capability_select",
            "node_type": "capability_select",
            "label": "Capability selection",
            "payload": {"capability_id": capability_id, "level": level, "route_tier": route_tier},
            "provenance": {"source": "run_unified_agent"},
        },
        {
            "node_id": "taskspec_draft",
            "node_type": "taskspec_draft",
            "label": "Canonical TaskSpec",
            "payload": {"task_spec": dict(task_spec), "task_spec_sha256": task_hash},
            "provenance": {"source": "run_unified_agent"},
        },
        {
            "node_id": "validate_taskspec",
            "node_type": "validate_taskspec",
            "label": "TaskSpec validation",
            "payload": {"ok": validation_ok},
            "provenance": {"source": "validate_task_spec"},
        },
        {
            "node_id": "agent_guard",
            "node_type": "agent_guard",
            "label": "Agent guard",
            "payload": {"ok": guards_ok, "rejection_reason": rejection_reason},
            "provenance": {"source": "evaluate_agent_guards"},
        },
        {
            "node_id": "execution_plan",
            "node_type": "execution_plan",
            "label": "Execution plan",
            "payload": {"execution_plan_sha256": plan_hash},
            "provenance": {"source": "plan_task_spec"},
        },
        {
            "node_id": "evidence_review",
            "node_type": "evidence_review",
            "label": "Golden eval verdict",
            "payload": {"accepted": accepted, "rejection_reason": rejection_reason},
            "provenance": {"source": "golden_eval"},
        },
    ]
    for node in nodes:
        commit("add_node", {"node": node})
    for edge_id, source, target, edge_type in (
        ("intent_to_capability", "intent_parse", "capability_select", "produces"),
        ("capability_to_taskspec", "capability_select", "taskspec_draft", "produces"),
        ("taskspec_to_validation", "taskspec_draft", "validate_taskspec", "validates"),
        ("taskspec_to_guard", "taskspec_draft", "agent_guard", "guards"),
        ("validation_to_plan", "validate_taskspec", "execution_plan", "produces"),
        ("guard_to_plan", "agent_guard", "execution_plan", "guards"),
        ("plan_to_review", "execution_plan", "evidence_review", "reviews"),
    ):
        commit("connect_edge", {
            "edge": {
                "edge_id": edge_id,
                "source_node_id": source,
                "target_node_id": target,
                "edge_type": edge_type,
            }
        })
    return {
        "dag_state_hash": dag.state_hash,
        "dag_path": str(dag_dir / "dag.json"),
        "dag_mutation_log": str(dag_dir / "mutations.jsonl"),
        "dag_mutation_count": len(mutations),
    }


def _expectation_checks(
    case: Mapping[str, Any],
    *,
    accepted: bool,
    task_spec: Mapping[str, Any],
    planning: Any,
    reason_codes: Sequence[str],
    allowed_claims: Sequence[str],
    forbidden_claims: Sequence[str],
    validation_result: str | None,
    injection_delivery_results: Sequence[str],
    injection_effect_results: Sequence[str],
    run_bundle_integrity_ok: bool | None,
    executed: bool,
) -> tuple[dict[str, bool], dict[str, Any]]:
    expected = case.get("expected") if isinstance(case.get("expected"), Mapping) else {}
    checks: dict[str, bool] = {"acceptance": accepted is bool(expected.get("accept", True))}
    diagnostics: dict[str, Any] = {}
    capability_id = _nested(task_spec, "model.capability_id")
    level = _nested(task_spec, "simulation.level")
    if expected.get("capability_id") is not None:
        checks["capability_id"] = capability_id == expected.get("capability_id")
    if expected.get("level") is not None:
        checks["level"] = level == expected.get("level")
    for path, value in (expected.get("fields") or {}).items() if isinstance(expected.get("fields"), Mapping) else []:
        checks[f"field:{path}"] = _nested(task_spec, str(path)) == value

    reason_set = set(reason_codes)
    required_all = {str(item) for item in expected.get("reason_codes_all", [])}
    required_any = {str(item) for item in expected.get("reason_codes_any", [])}
    forbidden_reasons = {str(item) for item in expected.get("reason_codes_forbidden", [])}
    if required_all:
        checks["reason_codes_all"] = required_all.issubset(reason_set)
    if required_any:
        checks["reason_codes_any"] = bool(required_any.intersection(reason_set))
    if forbidden_reasons:
        checks["reason_codes_forbidden"] = not bool(forbidden_reasons.intersection(reason_set))

    effect_bindings: list[Mapping[str, Any]] = []
    output_bindings: list[Mapping[str, Any]] = []
    if planning is not None and planning.resolved_spec is not None:
        effect_bindings = [item.model_dump(mode="json") for item in planning.resolved_spec.effect_bindings]
        output_bindings = [item.model_dump(mode="json") for item in planning.resolved_spec.output_bindings]
    expected_effects = expected.get("effects") if isinstance(expected.get("effects"), list) else []
    for index, item in enumerate(expected_effects):
        if not isinstance(item, Mapping):
            continue
        matching = [binding for binding in effect_bindings if binding.get("requested_effect") == item.get("effect")]
        checks[f"effect:{index}:present"] = bool(matching)
        if item.get("owner") is not None:
            checks[f"effect:{index}:owner"] = bool(matching) and all(binding.get("owner") == item.get("owner") for binding in matching)
        if item.get("capability_id") is not None:
            checks[f"effect:{index}:capability"] = bool(matching) and all(binding.get("capability_id") == item.get("capability_id") for binding in matching)
    expected_qoi = [str(item) for item in expected.get("qoi", [])]
    if expected_qoi:
        resolved_qoi = {str(item.get("requested_field")) for item in output_bindings if item.get("binding_status") == "resolved"}
        checks["required_qoi"] = set(expected_qoi).issubset(resolved_qoi)

    required_allowed = {str(item) for item in expected.get("allowed_claims", [])}
    required_forbidden = {str(item) for item in expected.get("forbidden_claims", [])}
    if required_allowed:
        checks["allowed_claims"] = required_allowed.issubset(set(allowed_claims))
    if required_forbidden:
        checks["forbidden_claims"] = required_forbidden.issubset(set(forbidden_claims))
    # Runtime expectations are evaluated only on repetitions that actually
    # execute a marked case.  Repetitions 2..N still test deterministic
    # parsing/planning without needlessly rerunning the physical simulation.
    if executed and expected.get("validation_result") is not None:
        checks["validation_result"] = validation_result == expected.get("validation_result")
    if executed and expected.get("injection_delivery_result") is not None:
        checks["injection_delivery_result"] = bool(injection_delivery_results) and all(
            item == expected.get("injection_delivery_result") for item in injection_delivery_results
        )
    if executed and expected.get("injection_effect_result") is not None:
        checks["injection_effect_result"] = bool(injection_effect_results) and all(
            item == expected.get("injection_effect_result") for item in injection_effect_results
        )
    if executed and expected.get("run_bundle_integrity_ok") is not None:
        checks["run_bundle_integrity_ok"] = run_bundle_integrity_ok is bool(expected.get("run_bundle_integrity_ok"))

    diagnostics.update({
        "expected": dict(expected),
        "actual": {
            "accepted": accepted,
            "capability_id": capability_id,
            "level": level,
            "reason_codes": list(reason_codes),
            "allowed_claims": list(allowed_claims),
            "forbidden_claims": list(forbidden_claims),
            "validation_result": validation_result,
            "injection_delivery_results": list(injection_delivery_results),
            "injection_effect_results": list(injection_effect_results),
            "run_bundle_integrity_ok": run_bundle_integrity_ok,
        },
    })
    return checks, diagnostics


def _run_one(case: Mapping[str, Any], *, repetition: int, output_dir: Path, execute: bool) -> GoldenRepetitionResult:
    case_dir = output_dir / f"repeat_{repetition:02d}"
    case_dir.mkdir(parents=True, exist_ok=True)
    accepted = False
    task_hash: str | None = None
    plan_hash: str | None = None
    capability_id: str | None = None
    level: str | None = None
    route_tier: str | None = None
    validation_result: str | None = None
    injection_delivery_results: tuple[str, ...] = ()
    injection_effect_results: tuple[str, ...] = ()
    run_bundle_integrity_ok: bool | None = None
    reason_codes: tuple[str, ...] = ()
    effect_bindings: tuple[dict[str, Any], ...] = ()
    output_bindings: tuple[dict[str, Any], ...] = ()
    guard_allowed_claims: tuple[str, ...] = ()
    guard_forbidden_claims: tuple[str, ...] = ()
    allowed_claims: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    planning = None
    task_spec: dict[str, Any] = {}
    diagnostics: dict[str, Any] = {}
    dag_state_hash: str | None = None
    dag_mutation_count = 0
    dag_path: str | None = None
    dag_mutation_log: str | None = None
    generated_python_policy: dict[str, Any] = {}
    request: UnifiedAgentRequest | None = None
    try:
        request = _request_from_case(case, output_dir=case_dir / "agent")
        result = run_unified_agent(request)
        accepted = bool(result.ok)
        task_spec = dict(result.task_spec)
        task_hash = spec_sha256(task_spec) if task_spec else None
        capability_id = _nested(task_spec, "model.capability_id")
        level = _nested(task_spec, "simulation.level")
        route_tier = result.route.tier
        reason_codes = tuple(str(item) for item in result.reason_codes)
        guard_allowed_claims = tuple(str(item) for item in result.guards.allowed_claims)
        guard_forbidden_claims = tuple(str(item) for item in result.guards.forbidden_claims)
        allowed_claims = guard_allowed_claims
        forbidden_claims = guard_forbidden_claims
        planning = result.planning
        if planning is not None and planning.execution_plan is not None:
            plan_hash = planning.execution_plan.plan_sha256
        if planning is not None and planning.resolved_spec is not None:
            effect_bindings = tuple(item.model_dump(mode="json") for item in planning.resolved_spec.effect_bindings)
            output_bindings = tuple(item.model_dump(mode="json") for item in planning.resolved_spec.output_bindings)
        generated_python_policy = _script_policy(case_dir / "agent", result.files)
        dag_evidence = _write_dag_evidence(
            case=case,
            repetition=repetition,
            case_dir=case_dir,
            request=request,
            task_spec=task_spec,
            task_hash=task_hash,
            plan_hash=plan_hash,
            capability_id=str(capability_id) if capability_id is not None else None,
            level=str(level) if level is not None else None,
            route_tier=route_tier,
            validation_ok=bool(result.validation.ok),
            guards_ok=bool(result.guards.ok),
            accepted=accepted,
        )
        dag_state_hash = str(dag_evidence["dag_state_hash"])
        dag_mutation_count = int(dag_evidence["dag_mutation_count"])
        dag_path = str(dag_evidence["dag_path"])
        dag_mutation_log = str(dag_evidence["dag_mutation_log"])
        diagnostics["dag_evidence"] = dag_evidence
        diagnostics["generated_python_policy"] = generated_python_policy

        if execute and accepted:
            runs_root = case_dir / "runs"
            prepared = prepare_run(task_spec, output_root=runs_root)
            execution = execute_prepared_run(prepared.bundle_root, expected_plan_sha256=prepared.execution_plan_sha256, max_attempts=1)
            validation_result = execution.validation.result.value
            injection_delivery_results = tuple(item.delivery_result.value for item in execution.validation.injection_evidence)
            injection_effect_results = tuple(item.effect_result.value for item in execution.validation.injection_evidence)
            injection_reason_codes = [
                code
                for item in execution.validation.injection_evidence
                for code in item.reason_codes
            ]
            reason_codes = tuple(dict.fromkeys([
                *reason_codes,
                execution.validation.reason_code,
                *injection_reason_codes,
                *execution.claim_report.reason_codes,
            ]))
            allowed_claims = tuple(execution.claim_report.allowed_claims)
            forbidden_claims = tuple(execution.claim_report.forbidden_claims)
            integrity = verify_run_bundle(execution.bundle_root)
            run_bundle_integrity_ok = bool(integrity.get("ok"))
            diagnostics["run_bundle"] = {
                "root": execution.bundle_root,
                "integrity": integrity,
                "run_status": execution.run_record.status.value,
            }
    except Exception as exc:
        diagnostics["exception"] = {"type": type(exc).__name__, "message": str(exc)}
        reason_codes = tuple(dict.fromkeys([*reason_codes, "EVAL_PIPELINE_EXCEPTION"]))
        generated_python_policy = _script_policy(case_dir / "agent", {})
        dag_evidence = _write_dag_evidence(
            case=case,
            repetition=repetition,
            case_dir=case_dir,
            request=request,
            task_spec=task_spec,
            task_hash=task_hash,
            plan_hash=plan_hash,
            capability_id=str(capability_id) if capability_id is not None else None,
            level=str(level) if level is not None else None,
            route_tier=route_tier,
            validation_ok=False,
            guards_ok=False,
            accepted=accepted,
            rejection_reason=str(exc),
        )
        dag_state_hash = str(dag_evidence["dag_state_hash"])
        dag_mutation_count = int(dag_evidence["dag_mutation_count"])
        dag_path = str(dag_evidence["dag_path"])
        dag_mutation_log = str(dag_evidence["dag_mutation_log"])
        diagnostics["dag_evidence"] = dag_evidence
        diagnostics["generated_python_policy"] = generated_python_policy

    checks, expectation_diag = _expectation_checks(
        case,
        accepted=accepted,
        task_spec=task_spec,
        planning=planning,
        reason_codes=reason_codes,
        allowed_claims=allowed_claims,
        forbidden_claims=forbidden_claims,
        validation_result=validation_result,
        injection_delivery_results=injection_delivery_results,
        injection_effect_results=injection_effect_results,
        run_bundle_integrity_ok=run_bundle_integrity_ok,
        executed=execute and accepted,
    )
    diagnostics.update(expectation_diag)
    passed = all(checks.values()) if checks else False
    return GoldenRepetitionResult(
        repetition=repetition,
        passed=passed,
        accepted=accepted,
        task_spec_sha256=task_hash,
        execution_plan_sha256=plan_hash,
        capability_id=str(capability_id) if capability_id is not None else None,
        level=str(level) if level is not None else None,
        route_tier=route_tier,
        validation_result=validation_result,
        injection_delivery_results=injection_delivery_results,
        injection_effect_results=injection_effect_results,
        run_bundle_integrity_ok=run_bundle_integrity_ok,
        reason_codes=reason_codes,
        effect_bindings=effect_bindings,
        output_bindings=output_bindings,
        guard_allowed_claims=guard_allowed_claims,
        guard_forbidden_claims=guard_forbidden_claims,
        allowed_claims=allowed_claims,
        forbidden_claims=forbidden_claims,
        dag_state_hash=dag_state_hash,
        dag_mutation_count=dag_mutation_count,
        dag_path=dag_path,
        dag_mutation_log=dag_mutation_log,
        generated_python_policy=generated_python_policy,
        checks=checks,
        diagnostics=diagnostics,
    )


def _rate(values: Sequence[bool]) -> float | None:
    return (sum(1 for value in values if value) / len(values)) if values else None


def _all_equal(values: Sequence[Any]) -> bool | None:
    present = [value for value in values if value is not None]
    return len(set(present)) <= 1 if present else None


def _case_result(case: Mapping[str, Any], repetitions: Sequence[GoldenRepetitionResult]) -> GoldenCaseResult:
    expected = case.get("expected") if isinstance(case.get("expected"), Mapping) else {}
    expected_accept = bool(expected.get("accept", True))
    first = repetitions[0]
    task_deterministic = _all_equal([item.task_spec_sha256 for item in repetitions if item.accepted])
    plan_deterministic = _all_equal([item.execution_plan_sha256 for item in repetitions if item.accepted])
    route_flip = len({item.route_tier for item in repetitions if item.route_tier is not None}) > 1
    # Compare the same pre-execution claim stage across repetitions.  Marked
    # cases execute only on repetition 1, whose final ClaimReport necessarily
    # differs from the remaining planning-only repetitions.
    claim_signatures = {(item.guard_allowed_claims, item.guard_forbidden_claims) for item in repetitions}
    claim_flip = len(claim_signatures) > 1
    expected_effects = expected.get("effects") if isinstance(expected.get("effects"), list) else []
    effect_required = bool(expected_effects)
    effect_bound_every_time = all(
        all(any(binding.get("requested_effect") == expected_effect.get("effect") for binding in item.effect_bindings) for expected_effect in expected_effects if isinstance(expected_effect, Mapping))
        for item in repetitions if item.accepted
    ) if any(item.accepted for item in repetitions) and effect_required else True
    silent_degradation = expected_accept and effect_required and any(item.accepted for item in repetitions) and not effect_bound_every_time
    required_forbidden = {str(value) for value in expected.get("forbidden_claims", [])}
    overclaim = any(not required_forbidden.issubset(set(item.forbidden_claims)) for item in repetitions) if required_forbidden else False
    passed = all(item.passed for item in repetitions) and not route_flip and not claim_flip and not silent_degradation and not overclaim
    return GoldenCaseResult(
        case_id=str(case.get("case_id")),
        category=str(case.get("category") or "uncategorized"),
        expected_accept=expected_accept,
        passed=passed,
        false_accept=(not expected_accept and first.accepted),
        false_reject=(expected_accept and not first.accepted),
        repetitions=tuple(repetitions),
        deterministic_task_spec=task_deterministic,
        deterministic_plan=plan_deterministic,
        route_flip=route_flip,
        claim_flip=claim_flip,
        silent_degradation=silent_degradation,
        overclaim=overclaim,
    )


def _compute_metrics(cases: Sequence[GoldenCaseResult], repeat_count: int) -> dict[str, Any]:
    repetitions = [item for case in cases for item in case.repetitions]
    supported = [case for case in cases if case.expected_accept]
    unsupported = [case for case in cases if not case.expected_accept]
    checks = [value for item in repetitions for value in item.checks.values()]
    field_checks = [value for item in repetitions for name, value in item.checks.items() if name.startswith("field:") or name in {"capability_id", "level"}]
    owner_checks = [value for item in repetitions for name, value in item.checks.items() if name.endswith(":owner")]
    qoi_checks = [value for item in repetitions for name, value in item.checks.items() if name == "required_qoi"]
    executed = [item for item in repetitions if item.validation_result is not None]
    injection_executions = [item for item in executed if item.injection_delivery_results]
    delivery_results = [value for item in injection_executions for value in item.injection_delivery_results]
    effect_results = [value for item in injection_executions for value in item.injection_effect_results]
    dag_hash_checks = [bool(item.dag_state_hash) for item in repetitions]
    mutation_log_checks = [item.dag_mutation_count > 0 and bool(item.dag_mutation_log) for item in repetitions]
    arbitrary_python_checks = [
        bool(item.generated_python_policy.get("arbitrary_python_generated"))
        for item in repetitions
        if item.generated_python_policy
    ]
    category_counts: dict[str, int] = {}
    category_pass: dict[str, list[bool]] = {}
    for case in cases:
        category_counts[case.category] = category_counts.get(case.category, 0) + 1
        category_pass.setdefault(case.category, []).append(case.passed)

    pass_at_1 = _rate([case.repetitions[0].passed for case in cases])
    pass_power_3 = _rate([all(item.passed for item in case.repetitions[:3]) for case in cases]) if repeat_count >= 3 else None
    pass_power_5 = _rate([all(item.passed for item in case.repetitions[:5]) for case in cases]) if repeat_count >= 5 else None
    return {
        "case_pass_rate": _rate([case.passed for case in cases]),
        "repetition_check_pass_rate": _rate(checks),
        "task_spec_field_match_rate": _rate(field_checks),
        "level_misroute_rate": 1.0 - (_rate([item.checks["level"] for item in repetitions if "level" in item.checks]) or 1.0),
        "effect_owner_error_rate": 1.0 - (_rate(owner_checks) or 1.0),
        "unsupported_rejection_accuracy": _rate([not case.repetitions[0].accepted for case in unsupported]),
        "supported_acceptance_accuracy": _rate([case.repetitions[0].accepted for case in supported]),
        "false_accept_rate": _rate([case.false_accept for case in unsupported]),
        "false_reject_rate": _rate([case.false_reject for case in supported]),
        "required_qoi_availability_rate": _rate(qoi_checks),
        "execution_validation_pass_rate": _rate([item.validation_result == "PASS" for item in executed]),
        "execution_inconclusive_rate": _rate([item.validation_result == "INCONCLUSIVE" for item in executed]),
        "runtime_injection_delivery_success_rate": _rate([value == "PASS" for value in delivery_results]),
        "runtime_effect_verified_rate": _rate([value == "PASS" for value in effect_results]),
        "runtime_effect_inconclusive_rate": _rate([value == "INCONCLUSIVE" for value in effect_results]),
        "run_bundle_integrity_rate": _rate([item.run_bundle_integrity_ok is True for item in executed]),
        "executed_case_count": len(executed),
        "executed_injection_case_count": len(injection_executions),
        "overclaim_rate": _rate([case.overclaim for case in cases]),
        "silent_degradation_rate": _rate([case.silent_degradation for case in cases]),
        "same_input_same_taskspec_rate": _rate([case.deterministic_task_spec is True for case in supported if case.deterministic_task_spec is not None]),
        "same_input_same_plan_rate": _rate([case.deterministic_plan is True for case in supported if case.deterministic_plan is not None]),
        "routing_flip_rate": _rate([case.route_flip for case in cases]),
        "claim_flip_rate": _rate([case.claim_flip for case in cases]),
        "dag_state_hash_availability_rate": _rate(dag_hash_checks),
        "typed_mutation_log_rate": _rate(mutation_log_checks),
        "arbitrary_python_generated_rate": _rate(arbitrary_python_checks),
        "pass_at_1": pass_at_1,
        "pass_power_3": pass_power_3,
        "pass_power_5": pass_power_5,
        "category_counts": category_counts,
        "category_pass_rate": {name: _rate(values) for name, values in sorted(category_pass.items())},
        "repeat_count": repeat_count,
    }


def _write_dag_summary(root: Path, cases: Sequence[GoldenCaseResult]) -> dict[str, Any]:
    aggregate_log = root / "agent_mutations.jsonl"
    records: list[dict[str, Any]] = []
    with aggregate_log.open("w", encoding="utf-8") as out:
        for case in cases:
            for repetition in case.repetitions:
                if not repetition.dag_mutation_log:
                    continue
                log_path = Path(repetition.dag_mutation_log)
                if not log_path.is_file():
                    continue
                for line in log_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    payload["case_id"] = case.case_id
                    payload["repetition"] = repetition.repetition
                    out.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                records.append({
                    "case_id": case.case_id,
                    "repetition": repetition.repetition,
                    "dag_state_hash": repetition.dag_state_hash,
                    "dag_path": repetition.dag_path,
                    "dag_mutation_log": repetition.dag_mutation_log,
                    "dag_mutation_count": repetition.dag_mutation_count,
                })
    report = {
        "schema_version": "sat-sim.agent-dag-state-report.v1",
        "ok": bool(records) and all(item["dag_state_hash"] and item["dag_mutation_count"] > 0 for item in records),
        "repetition_count": len(records),
        "agent_mutation_log": str(aggregate_log),
        "records": records,
    }
    write_json(root / "dag_state_report.json", report)
    return report


def default_golden_manifest() -> Path:
    """Return the Golden Set bundled with an installed wheel."""

    return Path(__file__).resolve().parent / "evals" / "golden_set.json"


def golden_set_schema() -> dict[str, Any]:
    """Return the packaged manifest contract for the formal V27 Golden Set."""

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://sat-sim.local/schemas/golden-set-v27.schema.json",
        "title": "V27 Agent Golden Set",
        "type": "object",
        "required": ["schema_version", "cases"],
        "properties": {
            "schema_version": {"const": GOLDEN_SET_VERSION},
            "description": {"type": "string"},
            "case_count": {"type": "integer", "minimum": 1},
            "category_counts": {
                "type": "object",
                "additionalProperties": {"type": "integer", "minimum": 0},
            },
            "cases": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["case_id", "category", "input_kind", "expected"],
                    "properties": {
                        "case_id": {"type": "string", "minLength": 1},
                        "category": {"type": "string", "minLength": 1},
                        "input_kind": {"enum": ["natural_language", "form", "task_spec", "patch"]},
                        "input": {"type": "object"},
                        "backend": {"type": "string"},
                        "execute": {"type": "boolean"},
                        "expected": {
                            "type": "object",
                            "required": ["accept"],
                            "properties": {"accept": {"type": "boolean"}},
                            "additionalProperties": True,
                        },
                    },
                    "additionalProperties": True,
                },
            },
        },
        "additionalProperties": True,
    }


def golden_eval_report_schema() -> dict[str, Any]:
    """Return the packaged report contract for V27 reliability results."""

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://sat-sim.local/schemas/golden-eval-report-v27.schema.json",
        "title": "V27 Agent Golden Evaluation Report",
        "type": "object",
        "required": [
            "schema_version", "golden_set_version", "ok", "manifest",
            "output_dir", "repeat_count", "total", "passed", "failed",
            "metrics", "cases",
        ],
        "properties": {
            "schema_version": {"const": GOLDEN_EVAL_VERSION},
            "golden_set_version": {"type": "string"},
            "ok": {"type": "boolean"},
            "manifest": {"type": "string"},
            "output_dir": {"type": "string"},
            "repeat_count": {"enum": [1, 3, 5]},
            "total": {"type": "integer", "minimum": 0},
            "passed": {"type": "integer", "minimum": 0},
            "failed": {"type": "integer", "minimum": 0},
            "metrics": {"type": "object", "additionalProperties": True},
            "cases": {"type": "array", "items": {"type": "object"}},
        },
        "additionalProperties": False,
    }


def run_golden_evals(
    manifest: str | Path,
    *,
    output_dir: str | Path = "reports/agent_golden_v27",
    repeat_count: int = 3,
    execute_marked_cases: bool = False,
    max_cases: int | None = None,
) -> GoldenEvalReport:
    """Run the formal V27 Golden Set.

    ``execute_marked_cases`` only executes cases whose manifest entry contains
    ``execute: true``.  All other cases stop after deterministic planning.
    """

    if repeat_count not in {1, 3, 5}:
        raise ValueError("repeat_count must be one of 1, 3 or 5")
    data = _load_manifest(manifest)
    raw_cases = [dict(item) for item in data["cases"] if isinstance(item, Mapping)]
    if max_cases is not None:
        raw_cases = raw_cases[: max(0, int(max_cases))]
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    results: list[GoldenCaseResult] = []
    for case in raw_cases:
        case_dir = root / "cases" / str(case["case_id"])
        reps = [
            _run_one(
                case,
                repetition=index,
                output_dir=case_dir,
                execute=bool(execute_marked_cases and case.get("execute", False) and index == 1),
            )
            for index in range(1, repeat_count + 1)
        ]
        result = _case_result(case, reps)
        results.append(result)
        write_json(case_dir / "case_result.json", result.to_dict())

    metrics = _compute_metrics(results, repeat_count)
    dag_summary = _write_dag_summary(root, results)
    metrics["dag_state_report_ok"] = bool(dag_summary.get("ok"))
    passed = sum(1 for case in results if case.passed)
    dag_evidence_ok = (
        bool(dag_summary.get("ok"))
        and float(metrics.get("dag_state_hash_availability_rate") or 0.0) == 1.0
        and float(metrics.get("typed_mutation_log_rate") or 0.0) == 1.0
    )
    arbitrary_python_ok = float(metrics.get("arbitrary_python_generated_rate") or 0.0) == 0.0
    report = GoldenEvalReport(
        schema_version=GOLDEN_EVAL_VERSION,
        golden_set_version=str(data.get("schema_version") or GOLDEN_SET_VERSION),
        ok=passed == len(results) and bool(results) and dag_evidence_ok and arbitrary_python_ok,
        manifest=str(Path(manifest)),
        output_dir=str(root),
        repeat_count=repeat_count,
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        metrics=metrics,
        cases=tuple(results),
    )
    write_json(root / "golden_eval_report.json", report.to_dict())
    write_json(root / "metrics.json", metrics)
    summary = {
        "schema_version": GOLDEN_EVAL_VERSION,
        "ok": report.ok,
        "total": report.total,
        "passed": report.passed,
        "failed": report.failed,
        "metrics": metrics,
        "failed_cases": [case.case_id for case in results if not case.passed],
    }
    write_json(root / "summary.json", summary)
    return report


__all__ = [
    "GOLDEN_SET_VERSION",
    "GOLDEN_EVAL_VERSION",
    "GoldenRepetitionResult",
    "GoldenCaseResult",
    "GoldenEvalReport",
    "default_golden_manifest",
    "golden_set_schema",
    "golden_eval_report_schema",
    "run_golden_evals",
]
