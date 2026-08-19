"""V26 closed-loop validation and claim computation.

The validator deliberately separates four questions:

* did the deterministic runner execute successfully?
* were the requested outputs actually observed?
* were runtime fault/degradation events delivered and, where evidence permits,
  was their effect verified?
* did explicit mission/QoI assertions pass?

The result is never collapsed into a misleading boolean.  It is one of
PASS, FAIL, INCONCLUSIVE or NOT_EVALUATED.
"""
from __future__ import annotations

import fnmatch
import math
from enum import StrEnum
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .agent_guards import PROFILE_RANK, evaluate_agent_guards
from .effect_evidence import (
    EffectEvidenceClassification,
    evaluate_runtime_effect,
    get_effect_evidence_profile,
)
from .execution_planner import ResolvedSpec
from .validation.physical_checks import evaluate_physical_validation

VALIDATION_OUTCOME_VERSION = "v26.validation-outcome.v1"
CLAIM_REPORT_VERSION = "v26.claim-report.v1"


class ValidationResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_EVALUATED = "NOT_EVALUATED"


class ExecutionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValidationCheck(_Strict):
    check_id: str
    category: str
    result: ValidationResult
    reason_code: str
    message: str
    path: str | None = None
    expected: Any = None
    observed: Any = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class InjectionEvidence(_Strict):
    event_id: str
    event_kind: str
    requested_effect: str
    delivery_result: ValidationResult
    effect_result: ValidationResult
    registered: bool
    active_sample_count: int = 0
    evidence_fields_present: list[str] = Field(default_factory=list)
    evidence_fields_missing: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class ValidationOutcome(_Strict):
    schema_version: str = VALIDATION_OUTCOME_VERSION
    execution_status: ExecutionStatus
    result: ValidationResult
    reason_code: str
    checks: list[ValidationCheck] = Field(default_factory=list)
    injection_evidence: list[InjectionEvidence] = Field(default_factory=list)
    physical_validation: dict[str, Any] = Field(default_factory=dict)
    evidence_present: list[str] = Field(default_factory=list)
    evidence_missing: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ClaimReport(_Strict):
    schema_version: str = CLAIM_REPORT_VERSION
    validation_result: ValidationResult
    requested_claim_level: str
    parameter_profile: str
    allowed_claims: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


def _nested_get(payload: Mapping[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _row_values(rows: Sequence[Mapping[str, Any]], field: str, time_range: Sequence[float] | None = None) -> list[Any]:
    values: list[Any] = []
    start = float(time_range[0]) if time_range and len(time_range) >= 1 else None
    end = float(time_range[1]) if time_range and len(time_range) >= 2 else None
    for row in rows:
        time_value = row.get("time_s")
        if start is not None and isinstance(time_value, (int, float)) and float(time_value) < start:
            continue
        if end is not None and isinstance(time_value, (int, float)) and float(time_value) > end:
            continue
        if field in row and row.get(field) is not None:
            values.append(row.get(field))
    return values


def _summary_value(summary: Mapping[str, Any], field: str) -> Any:
    if field in summary:
        return summary[field]
    nested = _nested_get(summary, field)
    if nested is not None:
        return nested
    qoi = summary.get("qoi") if isinstance(summary.get("qoi"), Mapping) else {}
    if field in qoi:
        return qoi[field]
    if field.startswith("qoi.") and field[4:] in qoi:
        return qoi[field[4:]]
    return None


def _flatten_keys(payload: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        out[name] = value
        if isinstance(value, Mapping):
            out.update(_flatten_keys(value, name))
    return out


def _matching_fields(field: str, summary: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> list[str]:
    if not any(char in field for char in "*?["):
        return [field] if (_summary_value(summary, field) is not None or any(field in row and row.get(field) is not None for row in rows)) else []
    candidates: dict[str, Any] = _flatten_keys(summary)
    qoi = summary.get("qoi") if isinstance(summary.get("qoi"), Mapping) else {}
    for key, value in qoi.items():
        candidates.setdefault(str(key), value)
        candidates.setdefault(f"qoi.{key}", value)
    for row in rows:
        for key, value in row.items():
            if value is not None:
                candidates.setdefault(str(key), value)
    return sorted(key for key, value in candidates.items() if value is not None and fnmatch.fnmatchcase(key, field))


def _field_present(field: str, summary: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> bool:
    return bool(_matching_fields(field, summary, rows))


def _aggregate(values: Sequence[Any], aggregation: str) -> Any:
    if aggregation == "exists":
        return bool(values)
    if not values:
        return None
    if aggregation == "first":
        return values[0]
    if aggregation == "last":
        return values[-1]
    numeric = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))]
    if aggregation == "min":
        return min(numeric) if numeric else None
    if aggregation == "max":
        return max(numeric) if numeric else None
    if aggregation == "mean":
        return fmean(numeric) if numeric else None
    if aggregation == "any":
        return any(bool(value) for value in values)
    if aggregation == "all":
        return all(bool(value) for value in values)
    return values[-1]


def _compare(observed: Any, operator: str, expected: Any) -> bool:
    if operator == "exists":
        return bool(observed)
    if operator in {"==", "eq"}:
        return observed == expected
    if operator in {"!=", "ne"}:
        return observed != expected
    if operator in {"<", "lt"}:
        return float(observed) < float(expected)
    if operator in {"<=", "le"}:
        return float(observed) <= float(expected)
    if operator in {">", "gt"}:
        return float(observed) > float(expected)
    if operator in {">=", "ge"}:
        return float(observed) >= float(expected)
    if operator == "within":
        return isinstance(expected, Sequence) and len(expected) == 2 and float(expected[0]) <= float(observed) <= float(expected[1])
    if operator == "in":
        return observed in expected
    raise ValueError(f"unsupported assertion operator: {operator}")


def _assertion_checks(spec: Mapping[str, Any], summary: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> list[ValidationCheck]:
    model = spec.get("model") if isinstance(spec.get("model"), Mapping) else {}
    validation = model.get("validation") if isinstance(model.get("validation"), Mapping) else {}
    assertions = validation.get("assertions") if isinstance(validation.get("assertions"), Sequence) and not isinstance(validation.get("assertions"), (str, bytes)) else []
    checks: list[ValidationCheck] = []
    for index, raw in enumerate(assertions):
        if not isinstance(raw, Mapping):
            checks.append(ValidationCheck(
                check_id=f"assertion_{index:03d}", category="mission_assertion",
                result=ValidationResult.INCONCLUSIVE, reason_code="ASSERTION_FORMAT_INVALID",
                message="Assertion is not a mapping.", path=f"$.model.validation.assertions[{index}]",
            ))
            continue
        field = str(raw.get("field") or raw.get("metric") or "")
        operator = str(raw.get("operator") or "<=")
        aggregation = str(raw.get("aggregation") or "last")
        expected = raw.get("value")
        time_range = raw.get("time_range") if isinstance(raw.get("time_range"), Sequence) and not isinstance(raw.get("time_range"), (str, bytes)) else None
        values = _row_values(rows, field, time_range)
        if not values:
            summary_value = _summary_value(summary, field)
            if summary_value is not None:
                values = [summary_value]
        observed = _aggregate(values, aggregation)
        if observed is None:
            checks.append(ValidationCheck(
                check_id=str(raw.get("id") or f"assertion_{index:03d}"), category="mission_assertion",
                result=ValidationResult.INCONCLUSIVE, reason_code="ASSERTION_EVIDENCE_MISSING",
                message=f"No evidence was available for assertion field {field!r}.",
                path=f"$.model.validation.assertions[{index}]", expected=expected,
                evidence={"field": field, "aggregation": aggregation, "time_range": list(time_range) if time_range else None},
            ))
            continue
        try:
            passed = _compare(observed, operator, expected)
        except Exception as exc:
            checks.append(ValidationCheck(
                check_id=str(raw.get("id") or f"assertion_{index:03d}"), category="mission_assertion",
                result=ValidationResult.INCONCLUSIVE, reason_code="ASSERTION_EVALUATION_ERROR",
                message=str(exc), path=f"$.model.validation.assertions[{index}]",
                expected=expected, observed=observed,
            ))
            continue
        checks.append(ValidationCheck(
            check_id=str(raw.get("id") or f"assertion_{index:03d}"), category="mission_assertion",
            result=ValidationResult.PASS if passed else ValidationResult.FAIL,
            reason_code="ASSERTION_PASSED" if passed else "MISSION_REQUIREMENT_FAILED",
            message=f"Assertion {field} {operator} {expected!r} {'passed' if passed else 'failed'}.",
            path=f"$.model.validation.assertions[{index}]", expected=expected, observed=observed,
            evidence={"field": field, "aggregation": aggregation, "sample_count": len(values), "time_range": list(time_range) if time_range else None},
        ))
    return checks


def _modifier_payloads(summary: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidates: list[Any] = []
    events = summary.get("events") if isinstance(summary.get("events"), Mapping) else {}
    candidates.append(events.get("applied_modifiers"))
    candidates.append(summary.get("applied_modifiers"))
    candidates.append(summary.get("applied_effects"))
    runtime_injection = summary.get("runtime_injection") if isinstance(summary.get("runtime_injection"), Mapping) else {}
    candidates.append(runtime_injection.get("requested_events"))
    adapter_metadata = summary.get("adapter_metadata") if isinstance(summary.get("adapter_metadata"), Mapping) else {}
    candidates.append(adapter_metadata.get("applied_modifiers"))
    candidates.append(adapter_metadata.get("applied_effects"))
    adapter_runtime = adapter_metadata.get("runtime_injection") if isinstance(adapter_metadata.get("runtime_injection"), Mapping) else {}
    candidates.append(adapter_runtime.get("requested_events"))
    adapter_labels = summary.get("adapter_labels") if isinstance(summary.get("adapter_labels"), Mapping) else {}
    candidates.append(adapter_labels.get("applied_modifiers"))
    candidates.append(adapter_labels.get("applied_effects"))
    for candidate in candidates:
        if isinstance(candidate, Mapping) and isinstance(candidate.get("modifiers"), Sequence):
            return [item for item in candidate.get("modifiers", []) if isinstance(item, Mapping)]
        if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes)):
            payloads = [item for item in candidate if isinstance(item, Mapping)]
            if payloads:
                return payloads
    return []


def _active_count(
    event_id: str,
    event_kind: str,
    rows: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    effect_id: str | None = None,
) -> int:
    count = 0
    for row in rows:
        ids = str(row.get("modifier.active_ids") or "")
        if event_id and event_id in {item.strip() for item in ids.split(",") if item.strip()}:
            count += 1
            continue
        active_effects = {item.strip() for item in str(row.get("event.active_effects") or "").split(",") if item.strip()}
        if effect_id and active_effects:
            if effect_id in active_effects:
                count += 1
            continue
        if event_kind == "fault" and row.get("label.fault_active") is True:
            count += 1
        elif event_kind == "degradation" and row.get("label.degradation_active") is True:
            count += 1
        elif event_kind == "constraint" and row.get("label.constraint_active") is True:
            count += 1
    if count:
        return count
    if event_kind == "fault":
        triggered = summary.get("runtime_fault_triggered_count")
        if isinstance(triggered, int) and triggered > 0:
            return triggered
    modifiers = _modifier_payloads(summary)
    for item in modifiers:
        if str(item.get("modifier_id") or item.get("fault_id") or item.get("degradation_id") or item.get("effect_id")) == event_id:
            applied = summary.get("events") if isinstance(summary.get("events"), Mapping) else {}
            active = applied.get("applied_modifiers") if isinstance(applied.get("applied_modifiers"), Mapping) else {}
            value = active.get("active_sample_count")
            if isinstance(value, int):
                return value
    return 0


def _event_payload(spec: Mapping[str, Any], event_id: str) -> Mapping[str, Any] | None:
    events = spec.get("events") if isinstance(spec.get("events"), Mapping) else {}
    for key in ("faults", "degradations", "constraints"):
        values = events.get(key) if isinstance(events, Mapping) else None
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
            continue
        for item in values:
            if isinstance(item, Mapping) and str(item.get("id") or item.get("event_id") or "") == event_id:
                return item
    return None


def _injection_evidence(
    spec: Mapping[str, Any],
    resolved: ResolvedSpec,
    summary: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> list[InjectionEvidence]:
    modifiers = _modifier_payloads(summary)
    registered_ids = {
        str(item.get("modifier_id") or item.get("fault_id") or item.get("degradation_id") or item.get("effect_id"))
        for item in modifiers
    }
    out: list[InjectionEvidence] = []
    for binding in resolved.effect_bindings:
        active_count = _active_count(
            binding.event_id, binding.event_kind, rows, summary, binding.requested_effect
        )
        runtime_registered = any(
            binding.requested_effect in {part.strip() for part in str(row.get("event.active_effects") or "").split(",") if part.strip()}
            for row in rows
        )
        registered = binding.event_id in registered_ids or runtime_registered
        present = [field for field in binding.evidence_fields if _field_present(field, summary, rows)]
        missing = [field for field in binding.evidence_fields if field not in present]
        delivery_ok = registered and active_count > 0
        delivery_result = ValidationResult.PASS if delivery_ok else ValidationResult.FAIL
        reason_codes: list[str] = []
        if not registered:
            reason_codes.append("RUNTIME_EVENT_NOT_REGISTERED")
        if active_count <= 0:
            reason_codes.append("RUNTIME_EVENT_NOT_ACTIVE")
        runtime_effect = evaluate_runtime_effect(
            capability_id=binding.capability_id,
            effect_id=binding.requested_effect,
            summary=summary,
            rows=rows,
            delivery_ok=delivery_ok,
            event=_event_payload(spec, binding.event_id),
        )
        if runtime_effect is not None:
            present = list(runtime_effect.evidence_fields_present)
            missing = list(runtime_effect.evidence_fields_missing)
            effect_result = ValidationResult(runtime_effect.result)
            reason_codes.append(runtime_effect.reason_code)
        elif binding.verification == "declared" and present and delivery_ok:
            effect_result = ValidationResult.PASS
            reason_codes.append("RUNTIME_EFFECT_VERIFIED")
        elif binding.verification == "not_observable":
            effect_result = ValidationResult.INCONCLUSIVE
            reason_codes.append("EFFECT_NOT_OBSERVABLE")
        elif binding.verification == "candidate":
            effect_result = ValidationResult.INCONCLUSIVE
            reason_codes.append("EFFECT_EVIDENCE_CANDIDATE")
        elif not present:
            effect_result = ValidationResult.INCONCLUSIVE
            reason_codes.append("EFFECT_EVIDENCE_MISSING")
        else:
            effect_result = ValidationResult.INCONCLUSIVE
            reason_codes.append("RUNTIME_INJECTION_UNVERIFIED")
        out.append(InjectionEvidence(
            event_id=binding.event_id,
            event_kind=binding.event_kind,
            requested_effect=binding.requested_effect,
            delivery_result=delivery_result,
            effect_result=effect_result,
            registered=registered,
            active_sample_count=active_count,
            evidence_fields_present=present,
            evidence_fields_missing=missing,
            reason_codes=reason_codes,
        ))
    return out


def evaluate_validation_outcome(
    *,
    spec: Mapping[str, Any],
    resolved: ResolvedSpec,
    summary: Mapping[str, Any] | None,
    trace_rows: Iterable[Mapping[str, Any]] | None,
    execution_status: ExecutionStatus = ExecutionStatus.SUCCEEDED,
    execution_reason_code: str | None = None,
) -> ValidationOutcome:
    """Evaluate outputs, physical gates, runtime injection and assertions."""

    summary_map = dict(summary or {})
    rows = tuple(dict(row) for row in (trace_rows or ()))
    if execution_status != ExecutionStatus.SUCCEEDED:
        return ValidationOutcome(
            execution_status=execution_status,
            result=ValidationResult.NOT_EVALUATED,
            reason_code=execution_reason_code or ("EXECUTION_CANCELLED" if execution_status == ExecutionStatus.CANCELLED else "EXECUTION_FAILED"),
            limitations=["Mission and QoI validation were not evaluated because deterministic execution did not succeed."],
        )

    checks: list[ValidationCheck] = []
    present: list[str] = []
    missing: list[str] = []

    # A runner can execute numerically while failing its mission contract.  Do
    # not allow output-presence and generic physical checks to turn an explicit
    # summary FAIL back into a ValidationOutcome PASS.
    summary_status = str(summary_map.get("status") or "").strip().upper()
    mission_status = str(summary_map.get("mission_status") or "").strip().upper()
    execution_summary_status = str(summary_map.get("execution_status") or "").strip().upper()
    overall_summary_status = str(summary_map.get("overall_status") or "").strip().upper()
    if execution_summary_status in {"FAIL", "FAILED", "ERROR"}:
        checks.append(ValidationCheck(
            check_id="runner_execution_status", category="runtime_health",
            result=ValidationResult.FAIL, reason_code="RUNNER_RUNTIME_HEALTH_FAILED",
            message="The runner completed its call but reported runtime message or model health errors.",
            observed=execution_summary_status,
            evidence={"runtime_message_read_errors": list(summary_map.get("runtime_message_read_errors") or [])},
        ))
    if overall_summary_status in {"FAIL", "FAILED", "ERROR"}:
        checks.append(ValidationCheck(
            check_id="overall_status", category="mission_result",
            result=ValidationResult.FAIL, reason_code="OVERALL_STATUS_FAILED",
            message="The runner's explicit combined execution/mission status failed.",
            observed={
                "overall_status": overall_summary_status,
                "execution_status": execution_summary_status or None,
                "mission_status": mission_status or None,
            },
        ))
    elif overall_summary_status == "INCONCLUSIVE":
        checks.append(ValidationCheck(
            check_id="overall_status", category="mission_result",
            result=ValidationResult.INCONCLUSIVE, reason_code="OVERALL_STATUS_INCONCLUSIVE",
            message="Execution completed, but one or more requested causal requirements were not exercised.",
            observed=overall_summary_status,
        ))
    if summary_status in {"FAIL", "FAILED", "ERROR"}:
        checks.append(ValidationCheck(
            check_id="summary_status", category="mission_result",
            result=ValidationResult.FAIL, reason_code="SUMMARY_STATUS_FAILED",
            message="The simulation runner reported an overall failure.",
            observed={
                "status": summary_status,
                "execution_status": execution_summary_status or None,
                "mission_status": mission_status or None,
            },
        ))
    elif mission_status in {"FAIL", "FAILED", "ERROR"}:
        checks.append(ValidationCheck(
            check_id="mission_status", category="mission_result",
            result=ValidationResult.FAIL, reason_code="MISSION_REQUIREMENT_FAILED",
            message="Deterministic execution completed, but the mission contract failed.",
            observed=mission_status,
        ))
    resource_feedback_status = str(summary_map.get("resource_feedback_closure_status") or "").strip().upper()
    if resource_feedback_status in {"FAIL", "FAILED", "ERROR"}:
        checks.append(ValidationCheck(
            check_id="resource_feedback_runtime_health", category="runtime_health",
            result=ValidationResult.FAIL, reason_code="RESOURCE_FEEDBACK_RUNTIME_FAILED",
            message="A required runtime resource bridge reported a message-read or directional-response error.",
            observed=resource_feedback_status,
            evidence={"bridges": dict(summary_map.get("resource_feedback_bridge_health") or {})},
        ))
    causal_status = str(summary_map.get("causal_requirement_evidence_status") or "").strip().upper()
    required_couplings = list((spec.get("mission") or {}).get("required_couplings") or []) if isinstance(spec.get("mission"), Mapping) else []
    if causal_status in {"FAIL", "FAILED", "ERROR"}:
        checks.append(ValidationCheck(
            check_id="causal_requirement_runtime_evidence", category="causal_requirement",
            result=ValidationResult.FAIL, reason_code="CAUSAL_REQUIREMENT_RUNTIME_FAILED",
            message="At least one declared causal requirement failed its runtime evidence check.",
            expected=required_couplings,
            observed=causal_status,
            evidence=dict(summary_map.get("causal_requirement_evidence") or {}),
        ))
    elif required_couplings and causal_status == "INCONCLUSIVE":
        checks.append(ValidationCheck(
            check_id="causal_requirement_runtime_evidence", category="causal_requirement",
            result=ValidationResult.INCONCLUSIVE, reason_code="CAUSAL_REQUIREMENT_NOT_EXERCISED",
            message="The selected capability provides the declared causal path, but this run did not excite all required links.",
            expected=required_couplings,
            observed=causal_status,
            evidence=dict(summary_map.get("causal_requirement_evidence") or {}),
        ))

    for binding in resolved.output_bindings:
        field = binding.bound_field or binding.requested_field
        if _field_present(field, summary_map, rows):
            present.append(field)
            checks.append(ValidationCheck(
                check_id=f"output:{binding.requested_field}", category="required_output",
                result=ValidationResult.PASS, reason_code="REQUIRED_QOI_PRESENT",
                message=f"Required output {binding.requested_field!r} was observed.", observed=field,
            ))
        else:
            missing.append(field)
            checks.append(ValidationCheck(
                check_id=f"output:{binding.requested_field}", category="required_output",
                result=ValidationResult.INCONCLUSIVE, reason_code="REQUIRED_QOI_MISSING",
                message=f"Required output {binding.requested_field!r} was not found in summary or telemetry.", expected=field,
            ))

    model = spec.get("model") if isinstance(spec.get("model"), Mapping) else {}
    validation_cfg = model.get("validation") if isinstance(model.get("validation"), Mapping) else {}
    selected_checks = validation_cfg.get("physical_checks") or validation_cfg.get("checks")
    physical = evaluate_physical_validation(
        rows,
        summary=summary_map,
        checks=selected_checks if isinstance(selected_checks, Sequence) and not isinstance(selected_checks, (str, bytes)) else None,
        strict=bool(validation_cfg.get("strict_physical", validation_cfg.get("strict", False))),
    )
    if physical.get("status") == "fail":
        checks.append(ValidationCheck(
            check_id="physical_validation", category="physical_validation",
            result=ValidationResult.FAIL, reason_code="PHYSICAL_VALIDATION_FAILED",
            message="One or more physical validation gates failed.", observed=physical.get("fail_count"),
            evidence={"issues": physical.get("issues", [])},
        ))
    elif physical.get("status") == "warning":
        checks.append(ValidationCheck(
            check_id="physical_validation", category="physical_validation",
            result=ValidationResult.PASS, reason_code="PHYSICAL_VALIDATION_WARNING",
            message="Physical validation passed with warnings.", observed=physical.get("warning_count"),
            evidence={"issues": physical.get("issues", [])},
        ))
    else:
        checks.append(ValidationCheck(
            check_id="physical_validation", category="physical_validation",
            result=ValidationResult.PASS, reason_code="PHYSICAL_VALIDATION_PASSED",
            message="Physical validation gates passed.",
        ))

    assertion_checks = _assertion_checks(spec, summary_map, rows)
    checks.extend(assertion_checks)
    injections = _injection_evidence(spec, resolved, summary_map, rows)
    for item in injections:
        checks.append(ValidationCheck(
            check_id=f"injection_delivery:{item.event_id}", category="runtime_injection",
            result=item.delivery_result,
            reason_code="RUNTIME_INJECTION_DELIVERY_VERIFIED" if item.delivery_result == ValidationResult.PASS else "RUNTIME_INJECTION_UNVERIFIED",
            message=f"Runtime event {item.event_id!r} delivery {'was verified' if item.delivery_result == ValidationResult.PASS else 'could not be verified'}.",
            evidence={"active_sample_count": item.active_sample_count, "registered": item.registered},
        ))
        checks.append(ValidationCheck(
            check_id=f"injection_effect:{item.event_id}", category="runtime_effect",
            result=item.effect_result,
            reason_code=item.reason_codes[-1] if item.reason_codes else ("RUNTIME_EFFECT_VERIFIED" if item.effect_result == ValidationResult.PASS else "RUNTIME_INJECTION_UNVERIFIED"),
            message=f"Runtime event {item.event_id!r} effect {'was verified' if item.effect_result == ValidationResult.PASS else ('failed its direct assertion' if item.effect_result == ValidationResult.FAIL else 'remains unverified')}.",
            evidence={"present": item.evidence_fields_present, "missing": item.evidence_fields_missing},
        ))

    results = [check.result for check in checks]
    if ValidationResult.FAIL in results:
        result = ValidationResult.FAIL
        reason = next((check.reason_code for check in checks if check.result == ValidationResult.FAIL), "MISSION_REQUIREMENT_FAILED")
    elif ValidationResult.INCONCLUSIVE in results:
        result = ValidationResult.INCONCLUSIVE
        reason = next((check.reason_code for check in checks if check.result == ValidationResult.INCONCLUSIVE), "VALIDATION_INCONCLUSIVE")
    else:
        result = ValidationResult.PASS
        reason = "VALIDATION_PASSED"
    limitations: list[str] = []
    if missing:
        limitations.append("One or more required QoI fields were not observed.")
    if any(item.effect_result == ValidationResult.INCONCLUSIVE for item in injections):
        limitations.append("Runtime event delivery may be verified while effect causality remains unobservable or outside the current model boundary.")
    for binding in resolved.effect_bindings:
        profile = get_effect_evidence_profile(binding.capability_id, binding.requested_effect)
        if profile is None:
            continue
        limitations.extend(profile.claim_limitations)
        if profile.classification == EffectEvidenceClassification.PROJECT_PROXY:
            limitations.append("At least one verified event is a project proxy and must not be reported as native physical-equivalence evidence.")
    return ValidationOutcome(
        execution_status=execution_status,
        result=result,
        reason_code=reason,
        checks=checks,
        injection_evidence=injections,
        physical_validation=physical,
        evidence_present=sorted(set(present)),
        evidence_missing=sorted(set(missing)),
        limitations=limitations,
    )


def build_claim_report(
    *,
    spec: Mapping[str, Any],
    resolved: ResolvedSpec,
    validation: ValidationOutcome,
) -> ClaimReport:
    """Compute allowed/forbidden claims from evidence instead of model prose."""

    guards = evaluate_agent_guards(spec)
    assurance = spec.get("assurance") if isinstance(spec.get("assurance"), Mapping) else {}
    parameters = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
    requested_claim = str(assurance.get("claim_level") or "analysis_only")
    parameter_profile = str(parameters.get("profile") or assurance.get("parameter_profile") or "demo")
    allowed = set(guards.allowed_claims)
    forbidden = set(guards.forbidden_claims)
    reasons: list[str] = []
    limitations = list(validation.limitations)

    if validation.execution_status == ExecutionStatus.SUCCEEDED:
        allowed.add("simulation_execution_completed")
    else:
        forbidden.update({"simulation_execution_completed", "task_requirements_satisfied", "runtime_effect_verified"})
        reasons.append(validation.reason_code)
    if validation.result == ValidationResult.PASS:
        allowed.add("task_requirements_satisfied")
    else:
        forbidden.add("task_requirements_satisfied")
        reasons.append(validation.reason_code)
    injections = validation.injection_evidence
    if injections and all(item.delivery_result == ValidationResult.PASS for item in injections):
        allowed.add("runtime_injection_delivery_verified")
    elif injections:
        forbidden.add("runtime_injection_delivery_verified")
    effect_profiles = [
        found_profile
        for binding in resolved.effect_bindings
        if (found_profile := get_effect_evidence_profile(binding.capability_id, binding.requested_effect)) is not None
    ]
    has_proxy_effect = any(
        profile.classification == EffectEvidenceClassification.PROJECT_PROXY
        for profile in effect_profiles
    )
    if injections and all(item.effect_result == ValidationResult.PASS for item in injections):
        if has_proxy_effect:
            allowed.add("runtime_proxy_effect_verified")
            forbidden.update({"runtime_effect_verified", "runtime_native_effect_verified"})
            reasons.append("PROJECT_PROXY_EFFECT_VERIFIED")
        else:
            allowed.update({"runtime_effect_verified", "runtime_registered_effect_verified"})
    elif injections:
        forbidden.update({"runtime_effect_verified", "runtime_registered_effect_verified"})
        reasons.append("RUNTIME_INJECTION_UNVERIFIED")
    for effect_profile in effect_profiles:
        limitations.extend(effect_profile.claim_limitations)

    requested_rank = PROFILE_RANK.get(requested_claim, 99)
    profile_rank = PROFILE_RANK.get(parameter_profile, -1)
    if requested_rank > profile_rank:
        forbidden.add(f"claim_level:{requested_claim}")
        reasons.append("CLAIM_EXCEEDS_PARAMETER_PROFILE")
    elif validation.result == ValidationResult.PASS:
        allowed.add(f"claim_level:{requested_claim}")
    else:
        forbidden.add(f"claim_level:{requested_claim}")

    if not resolved.assurance.capability_can_claim_high_fidelity:
        forbidden.add("certified_high_fidelity")
    if parameter_profile != "ground_calibrated":
        forbidden.add("ground_calibrated")
    elif validation.result == ValidationResult.PASS:
        allowed.add("ground_calibrated_parameter_profile")
    if parameter_profile != "flight_correlated":
        forbidden.add("flight_correlated")
    elif validation.result == ValidationResult.PASS:
        allowed.add("flight_correlated_parameter_profile")

    limitations.extend(resolved.assurance.limitations)
    return ClaimReport(
        validation_result=validation.result,
        requested_claim_level=requested_claim,
        parameter_profile=parameter_profile,
        allowed_claims=sorted(allowed - forbidden),
        forbidden_claims=sorted(forbidden),
        limitations=list(dict.fromkeys(limitations)),
        reason_codes=sorted(set(reasons)),
        evidence={
            "canonical_task_spec_sha256": resolved.canonical_task_spec_sha256,
            "resolved_spec_sha256": resolved.resolved_spec_sha256,
            "execution_status": validation.execution_status,
            "validation_result": validation.result,
            "runtime_event_count": len(injections),
        },
    )


def validation_outcome_schema() -> dict[str, Any]:
    schema = ValidationOutcome.model_json_schema(mode="validation")
    schema["$id"] = "https://example.local/sat-sim/validation-outcome-v26.schema.json"
    schema["title"] = "Satellite Simulation ValidationOutcome V26"
    return schema


def claim_report_schema() -> dict[str, Any]:
    schema = ClaimReport.model_json_schema(mode="validation")
    schema["$id"] = "https://example.local/sat-sim/claim-report-v26.schema.json"
    schema["title"] = "Satellite Simulation ClaimReport V26"
    return schema


__all__ = [
    "VALIDATION_OUTCOME_VERSION",
    "CLAIM_REPORT_VERSION",
    "ValidationResult",
    "ExecutionStatus",
    "ValidationCheck",
    "InjectionEvidence",
    "ValidationOutcome",
    "ClaimReport",
    "evaluate_validation_outcome",
    "build_claim_report",
    "validation_outcome_schema",
    "claim_report_schema",
]
