"""Deterministic attribution of Agent generation and execution evidence.

This module never calls a model and never changes TaskSpec or Run Bundle state.
It reads already-produced artifacts and reports separate denominators for:
- eligible real-model first answers,
- eventual Agent task success,
- repair/fallback-assisted success,
- sealed native execution,
- model invocation cost.

Keeping these tracks separate prevents repaired/fallback success from being
reported as first-answer model quality.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

ATTRIBUTION_SCHEMA_VERSION = "sat-sim.agent-eval-attribution.v1"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _steps(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    facade = report.get("facade_result")
    if isinstance(facade, Mapping) and isinstance(facade.get("steps"), list):
        return [dict(x) for x in facade["steps"] if isinstance(x, Mapping)]
    if isinstance(report.get("steps"), list):
        return [dict(x) for x in report["steps"] if isinstance(x, Mapping)]
    return []


def _call_cost(path: Path) -> dict[str, Any]:
    payload = _read_json(path) or {}
    evidence = payload.get("invocation_evidence")
    if not isinstance(evidence, Mapping):
        evidence = {}
    usage = evidence.get("usage")
    if not isinstance(usage, Mapping):
        usage = {}
    return {
        "present": path.is_file(),
        "verified": evidence.get("verified") is True,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "latency_ms": float(evidence.get("latency_ms") or 0.0),
        "request_id": evidence.get("request_id"),
    }


def analyze_generation_artifacts(output_dir: str | Path) -> dict[str, Any]:
    root = Path(output_dir)
    first = _read_json(root / "first_attempt.json")
    report = _read_json(root / "unified_agent_report.json")
    if report is None:
        report = _read_json(root / "capability_agent_report.json") or {}

    steps = _steps(report)
    step_names = [str(item.get("name") or "") for item in steps]
    first_available = isinstance(first, Mapping)
    first_eligible = bool(
        first
        and (
            first.get("first_answer_eligible") is True
            or (
                "first_answer_eligible" not in first
                and first.get("eligible_unrepaired_first_answer") is True
            )
        )
    )
    first_validation_ok = bool(first and first.get("validation_ok") is True)
    pre_validation_corrections = (
        list(first.get("pre_validation_corrections") or [])
        if isinstance(first, Mapping)
        else []
    )
    if isinstance(first, Mapping) and "first_answer_pass" in first:
        first_answer_pass = bool(first_eligible and first.get("first_answer_pass") is True)
    else:
        first_answer_pass = bool(
            first_eligible
            and first_validation_ok
            and not pre_validation_corrections
        )

    retry_used = (root / "model_call_retry.json").is_file() or "model_invocation_retry" in step_names
    schema_rejected_first_response = (
        (root / "model_call_rejected.json").is_file()
        or "model_invocation_rejected" in step_names
        or bool(first and first.get("schema_rejected_first_response") is True)
    )
    local_repair_used = "repair" in step_names
    route_repair_used = "planner_route_repair" in step_names
    template_fallback_used = "model_fallback" in step_names
    final_ok = report.get("ok") is True

    calls = [
        _call_cost(root / "model_call_rejected.json"),
        _call_cost(root / "model_call.json"),
        _call_cost(root / "model_call_retry.json"),
    ]
    present_calls: list[dict[str, Any]] = []
    seen_request_ids: set[str] = set()
    for item in calls:
        if not item["present"]:
            continue
        request_id = str(item.get("request_id") or "")
        if request_id and request_id in seen_request_ids:
            continue
        if request_id:
            seen_request_ids.add(request_id)
        present_calls.append(item)
    verified_calls = [item for item in present_calls if item["verified"]]

    return {
        "schema_version": ATTRIBUTION_SCHEMA_VERSION,
        "output_dir": str(root),
        "first_attempt_available": first_available,
        "first_answer_eligible": first_eligible,
        "first_answer_validation_ok": first_validation_ok,
        "first_answer_pass": first_answer_pass,
        "final_agent_ok": final_ok,
        "repair_assisted_success": bool(
            final_ok
            and not first_answer_pass
            and (
                schema_rejected_first_response
                or retry_used
                or local_repair_used
                or route_repair_used
                or template_fallback_used
            )
        ),
        "schema_rejected_first_response": schema_rejected_first_response,
        "model_retry_used": retry_used,
        "local_repair_used": local_repair_used,
        "route_repair_used": route_repair_used,
        "template_fallback_used": template_fallback_used,
        "correction_used": bool(
            schema_rejected_first_response
            or retry_used
            or local_repair_used
            or route_repair_used
            or template_fallback_used
        ),
        "model_calls": {
            "present_count": len(present_calls),
            "verified_count": len(verified_calls),
            "all_present_verified": bool(present_calls) and len(verified_calls) == len(present_calls),
            "prompt_tokens": sum(int(item["prompt_tokens"]) for item in present_calls),
            "completion_tokens": sum(int(item["completion_tokens"]) for item in present_calls),
            "total_tokens": sum(int(item["total_tokens"]) for item in present_calls),
            "latency_ms": sum(float(item["latency_ms"]) for item in present_calls),
        },
        "first_attempt": dict(first) if first else None,
    }


def analyze_run_bundle(bundle_root: str | Path | None) -> dict[str, Any]:
    if bundle_root is None:
        return {
            "present": False,
            "integrity_ok": False,
            "sealed": False,
            "implementation_class": None,
            "native_eligible": False,
            "run_status": None,
            "validation_result": None,
            "run_bundle_execution_pass": False,
            "native_execution_pass": False,
        }
    root = Path(bundle_root)
    if not root.exists():
        return {
            "present": False,
            "integrity_ok": False,
            "sealed": False,
            "implementation_class": None,
            "native_eligible": False,
            "run_status": None,
            "validation_result": None,
            "run_bundle_execution_pass": False,
            "native_execution_pass": False,
        }
    from .run_bundle import verify_run_bundle

    try:
        integrity = verify_run_bundle(root)
    except Exception as exc:
        integrity = {
            "ok": False,
            "reason_code": "RUN_BUNDLE_INTEGRITY_CHECK_ERROR",
            "error_type": exc.__class__.__name__,
        }
    record = _read_json(root / "run_record.json") or {}
    validation = _read_json(root / "validation" / "validation_outcome.json") or {}
    resolved = _read_json(root / "input" / "resolved_spec.json") or {}
    implementation_class = str(resolved.get("implementation_class") or "") or None
    native_eligible = implementation_class in {
        "official_basilisk_native",
        "mixed_native_project",
    }
    integrity_ok = integrity.get("ok") is True
    run_status = record.get("status")
    validation_result = validation.get("result")
    run_bundle_execution_pass = bool(
        integrity_ok and run_status == "SUCCEEDED" and validation_result == "PASS"
    )
    return {
        "present": True,
        "integrity_ok": integrity_ok,
        "integrity_reason_code": integrity.get("reason_code"),
        "sealed": integrity_ok,
        "implementation_class": implementation_class,
        "native_eligible": native_eligible,
        "run_status": run_status,
        "validation_result": validation_result,
        "run_bundle_execution_pass": run_bundle_execution_pass,
        "native_execution_pass": bool(native_eligible and run_bundle_execution_pass),
    }


def analyze_case(
    *,
    case_id: str,
    output_dir: str | Path,
    bundle_root: str | Path | None = None,
    evidence_checks: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    generation = analyze_generation_artifacts(output_dir)
    execution = analyze_run_bundle(bundle_root)
    checks = dict(evidence_checks or {})
    evidence_accuracy = None if not checks else all(bool(v) for v in checks.values())
    return {
        "schema_version": ATTRIBUTION_SCHEMA_VERSION,
        "case_id": case_id,
        "generation": generation,
        "execution": execution,
        "evidence_checks": checks,
        "evidence_accuracy_pass": evidence_accuracy,
    }


def aggregate_cases(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(item) for item in cases]
    first_eligible = [
        item for item in rows
        if bool(((item.get("generation") or {}).get("first_answer_eligible")))
    ]
    first_passed = [
        item for item in first_eligible
        if bool(((item.get("generation") or {}).get("first_answer_pass")))
    ]
    final_passed = [
        item for item in rows
        if bool(((item.get("generation") or {}).get("final_agent_ok")))
    ]
    run_bundle_eligible = [
        item for item in rows
        if bool(((item.get("execution") or {}).get("present")))
    ]
    run_bundle_passed = [
        item for item in run_bundle_eligible
        if bool(((item.get("execution") or {}).get("run_bundle_execution_pass")))
    ]
    native_eligible = [
        item for item in rows
        if bool(((item.get("execution") or {}).get("native_eligible")))
    ]
    native_passed = [
        item for item in native_eligible
        if bool(((item.get("execution") or {}).get("native_execution_pass")))
    ]
    evidence_eligible = [
        item for item in rows if item.get("evidence_accuracy_pass") is not None
    ]
    evidence_passed = [
        item for item in evidence_eligible if item.get("evidence_accuracy_pass") is True
    ]
    correction_success = [
        item for item in rows
        if bool(((item.get("generation") or {}).get("repair_assisted_success")))
    ]
    total_tokens = sum(
        int((((item.get("generation") or {}).get("model_calls") or {}).get("total_tokens") or 0))
        for item in rows
    )
    total_latency_ms = sum(
        float((((item.get("generation") or {}).get("model_calls") or {}).get("latency_ms") or 0.0))
        for item in rows
    )

    def rate(n: int, d: int) -> float | None:
        return (n / d) if d else None

    return {
        "schema_version": ATTRIBUTION_SCHEMA_VERSION,
        "case_count": len(rows),
        "metrics": {
            "first_answer": {
                "eligible": len(first_eligible),
                "passed": len(first_passed),
                "rate": rate(len(first_passed), len(first_eligible)),
            },
            "final_agent_task": {
                "eligible": len(rows),
                "passed": len(final_passed),
                "rate": rate(len(final_passed), len(rows)),
            },
            "repair_assisted_success_count": len(correction_success),
            "run_bundle_execution": {
                "eligible": len(run_bundle_eligible),
                "passed": len(run_bundle_passed),
                "rate": rate(len(run_bundle_passed), len(run_bundle_eligible)),
            },
            "native_execution": {
                "eligible": len(native_eligible),
                "passed": len(native_passed),
                "rate": rate(len(native_passed), len(native_eligible)),
            },
            "evidence_accuracy": {
                "eligible": len(evidence_eligible),
                "passed": len(evidence_passed),
                "rate": rate(len(evidence_passed), len(evidence_eligible)),
            },
            "resource_use": {
                "model_total_tokens": total_tokens,
                "model_latency_ms": total_latency_ms,
                "tokens_per_case": rate(total_tokens, len(rows)),
                "latency_ms_per_case": rate(total_latency_ms, len(rows)),
            },
        },
        "cases": rows,
        "denominator_policy": (
            "First-answer, final-task, native-execution and evidence-accuracy "
            "metrics retain separate eligibility denominators."
        ),
    }


__all__ = [
    "ATTRIBUTION_SCHEMA_VERSION",
    "analyze_generation_artifacts",
    "analyze_run_bundle",
    "analyze_case",
    "aggregate_cases",
]
