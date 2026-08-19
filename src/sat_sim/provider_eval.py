"""Provider-aware composite whole-spacecraft Agent evaluation.

The evaluator distinguishes deterministic baselines, protocol-conformance
fixtures and real model providers.  A missing API key or model endpoint yields
an explicit NOT_EXECUTED status rather than a fabricated model score.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .model_providers import ModelProviderRegistry
from .unified_agent import UnifiedAgentRequest, run_unified_agent

PROVIDER_EVAL_VERSION = "provider-eval.v1"


@dataclass(frozen=True)
class ProviderEvalCaseResult:
    case_id: str
    request: str
    passed: bool
    expected_supported: bool
    actual_supported: bool
    checks: dict[str, bool]
    errors: tuple[str, ...] = field(default_factory=tuple)
    route: dict[str, Any] = field(default_factory=dict)
    task_spec: dict[str, Any] = field(default_factory=dict)
    reason_codes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["errors"] = list(self.errors)
        payload["reason_codes"] = list(self.reason_codes)
        return payload


@dataclass(frozen=True)
class ProviderEvalReport:
    schema_version: str
    evaluation_id: str
    started_at: str
    completed_at: str
    provider_id: str
    provider_kind: str
    provider_execution_status: str
    readiness: dict[str, Any]
    cases_sha256: str
    provider_config_fingerprint: str
    provider_identity_verified: bool
    fallback_case_count: int
    case_count: int
    passed_count: int
    failed_count: int
    pass_rate: float | None
    cases: tuple[ProviderEvalCaseResult, ...]
    attestation: dict[str, Any]
    claim_guardrail: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["cases"] = [item.to_dict() for item in self.cases]
        return payload


def load_provider_eval_cases(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("provider eval cases must be a JSON array")
    return [dict(item) for item in data if isinstance(item, Mapping)]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _stable_sha256(value: Any) -> str:
    return _sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _provider_fingerprint(profile: Any) -> str:
    public = profile.to_dict()
    public.pop("api_key_configured", None)
    public.pop("secret_values_exposed", None)
    return _stable_sha256(public)


def _attestation_payload(report_payload: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(report_payload)
    body.pop("attestation", None)
    return {
        "schema_version": "provider-eval-attestation.v1",
        "report_payload_sha256": _stable_sha256(body),
        "secret_values_exposed": False,
        "provider_identity_rule": "Every executed case must route to the requested provider_id without deterministic fallback.",
        "score_rule": "pass_rate is null unless provider_execution_status is EXECUTED and provider identity is verified.",
    }


def _events(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    payload = spec.get("events") if isinstance(spec.get("events"), Mapping) else {}
    out: list[dict[str, Any]] = []
    for key in ("faults", "degradations", "constraints"):
        raw = payload.get(key) if isinstance(payload.get(key), list) else []
        for item in raw:
            if isinstance(item, Mapping):
                out.append(dict(item))
    return out


def _reason_codes(result: Any) -> set[str]:
    codes = set(str(item) for item in (result.reason_codes or ()))
    if result.planning is not None:
        codes.update(str(item.code) for item in result.planning.validation.issues)
    codes.update(str(item.code) for item in result.validation.errors)
    codes.update(str(item.code) for item in result.guards.issues)
    task_spec = result.task_spec or {}
    metadata = task_spec.get("metadata") if isinstance(task_spec, Mapping) else {}
    planner = metadata.get("planner") if isinstance(metadata, Mapping) else {}
    unsupported = planner.get("unsupported_requirements") if isinstance(planner, Mapping) else []
    if isinstance(unsupported, list):
        for item in unsupported:
            if isinstance(item, Mapping) and item.get("reason_code"):
                codes.add(str(item["reason_code"]))
    return codes

def _qoi(spec: Mapping[str, Any]) -> set[str]:
    outputs = spec.get("outputs") if isinstance(spec.get("outputs"), Mapping) else {}
    values: list[Any] = []
    for key in ("summary", "qoi", "traces", "plots"):
        item = outputs.get(key)
        if isinstance(item, list):
            values.extend(item)
    return {str(item) for item in values}


def _compare(case: Mapping[str, Any], result: Any, *, expected_provider_id: str) -> ProviderEvalCaseResult:
    expected_supported = bool(case.get("expected_supported", True))
    actual_supported = bool(result.ok)
    checks: dict[str, bool] = {
        "supported": actual_supported == expected_supported,
        "provider_identity": str(result.route.provider_id or "") == expected_provider_id,
    }
    errors: list[str] = []
    spec = dict(result.task_spec or {})
    if expected_supported:
        expected_mode = case.get("expected_mode")
        actual_mode = spec.get("mode") or (spec.get("model") or {}).get("target", {}).get("mode")
        if expected_mode:
            checks["mode"] = str(actual_mode) == str(expected_mode)
        capability_id = (spec.get("model") or {}).get("capability_id") or spec.get("capability_id")
        checks["composite_capability"] = capability_id == "whole_spacecraft.composite_digital_twin.v1"
        requested_qoi = set(str(item) for item in case.get("required_qoi", []))
        checks["qoi"] = requested_qoi.issubset(_qoi(spec))
        actual_events = _events(spec)
        for index, expected in enumerate(case.get("expected_events", [])):
            effect = str(expected.get("effect"))
            matches = [item for item in actual_events if str(item.get("effect")) == effect]
            checks[f"event:{index}:{effect}"] = bool(matches)
            if matches:
                match = matches[0]
                if expected.get("start_s") is not None:
                    checks[f"event:{index}:start_s"] = float(match.get("start_s", 0.0)) == float(expected["start_s"])
                if expected.get("end_s") is not None:
                    checks[f"event:{index}:end_s"] = float(match.get("end_s", 0.0)) == float(expected["end_s"])
    else:
        required_codes = set(str(item) for item in case.get("expected_reason_codes", []))
        actual_codes = _reason_codes(result)
        checks["reason_codes"] = bool(required_codes & actual_codes) if required_codes else not actual_supported
    for name, passed in checks.items():
        if not passed:
            errors.append(name)
    return ProviderEvalCaseResult(
        case_id=str(case.get("case_id") or "unnamed"),
        request=str(case.get("request") or ""),
        passed=all(checks.values()),
        expected_supported=expected_supported,
        actual_supported=actual_supported,
        checks=checks,
        errors=tuple(errors),
        route=result.route.to_dict(),
        task_spec=spec,
        reason_codes=tuple(result.reason_codes or ()),
    )


def run_provider_eval(
    *,
    provider_id: str,
    cases_path: str | Path,
    output_dir: str | Path,
    provider_kind: str = "real_llm_provider",
    registry: ModelProviderRegistry | None = None,
) -> ProviderEvalReport:
    registry = registry or ModelProviderRegistry()
    started_at = _utc_now()
    evaluation_id = f"provider-eval-{provider_id}-{started_at.replace(':', '').replace('+', '-')}"
    readiness = registry.readiness(provider_id)
    profile = registry.get(provider_id)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    cases_source = Path(cases_path)
    cases_sha256 = _sha256_bytes(cases_source.read_bytes())
    provider_fingerprint = _provider_fingerprint(profile)
    if not readiness.ready:
        completed_at = _utc_now()
        preliminary = {
            "schema_version": PROVIDER_EVAL_VERSION,
            "evaluation_id": evaluation_id,
            "started_at": started_at,
            "completed_at": completed_at,
            "provider_id": provider_id,
            "provider_kind": provider_kind,
            "provider_execution_status": "NOT_EXECUTED_CONFIGURATION_MISSING",
            "readiness": readiness.to_dict(),
            "cases_sha256": cases_sha256,
            "provider_config_fingerprint": provider_fingerprint,
            "provider_identity_verified": False,
            "fallback_case_count": 0,
            "case_count": 0,
            "passed_count": 0,
            "failed_count": 0,
            "pass_rate": None,
            "cases": [],
            "claim_guardrail": "No real-provider score is claimed because the selected provider is not configured and ready.",
        }
        attestation = _attestation_payload(preliminary)
        report = ProviderEvalReport(
            **{key: value for key, value in preliminary.items() if key != "cases"},
            cases=(),
            attestation=attestation,
        )
        (output / "report.json").write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return report

    cases = load_provider_eval_cases(cases_source)
    results: list[ProviderEvalCaseResult] = []
    fallback_case_count = 0
    for case in cases:
        case_id = str(case.get("case_id") or f"case_{len(results)+1}")
        result = run_unified_agent(UnifiedAgentRequest(
            input_kind="natural_language",
            request_text=str(case.get("request") or ""),
            output_dir=output / "cases" / case_id,
            provider_id=provider_id,
            routing_mode="local" if profile.location == "local" else "remote",
            compile_if_valid=True,
        ))
        compared = _compare(case, result, expected_provider_id=provider_id)
        results.append(compared)
        if str(result.route.provider_id or "") != provider_id:
            fallback_case_count += 1
    provider_identity_verified = fallback_case_count == 0 and len(results) == len(cases)
    passed = sum(1 for item in results if item.passed)
    completed_at = _utc_now()
    execution_status = "EXECUTED" if provider_identity_verified else "EXECUTED_PROVIDER_IDENTITY_MISMATCH"
    pass_rate = passed / len(results) if results and provider_identity_verified else None
    preliminary = {
        "schema_version": PROVIDER_EVAL_VERSION,
        "evaluation_id": evaluation_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "provider_id": provider_id,
        "provider_kind": provider_kind,
        "provider_execution_status": execution_status,
        "readiness": readiness.to_dict(),
        "cases_sha256": cases_sha256,
        "provider_config_fingerprint": provider_fingerprint,
        "provider_identity_verified": provider_identity_verified,
        "fallback_case_count": fallback_case_count,
        "case_count": len(results),
        "passed_count": passed,
        "failed_count": len(results) - passed,
        "pass_rate": pass_rate,
        "cases": [item.to_dict() for item in results],
        "claim_guardrail": (
            "This score belongs only to the named provider, model configuration and current cases. "
            "Capability Registry, validation and execution evidence remain authoritative."
        ),
    }
    attestation = _attestation_payload(preliminary)
    report = ProviderEvalReport(
        **{key: value for key, value in preliminary.items() if key != "cases"},
        cases=tuple(results),
        attestation=attestation,
    )
    (output / "report.json").write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def build_provider_readiness_matrix(*, output_path: str | Path, registry: ModelProviderRegistry | None = None) -> dict[str, Any]:
    registry = registry or ModelProviderRegistry()
    payload = {
        "schema_version": PROVIDER_EVAL_VERSION,
        "providers": [
            {
                "provider": profile.to_dict(),
                "readiness": registry.readiness(profile.provider_id).to_dict(),
                "evaluation_status": "READY_TO_EXECUTE" if registry.readiness(profile.provider_id).ready else "NOT_EXECUTED_CONFIGURATION_MISSING",
            }
            for profile in registry.list()
        ],
        "claim_guardrail": "Missing providers have no score. Protocol tests are not reported as real-model evaluations.",
        "attestation_rule": "Executed scores require requested-provider identity for every case and a non-null input-case SHA-256.",
    }
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


__all__ = [
    "PROVIDER_EVAL_VERSION",
    "ProviderEvalCaseResult",
    "ProviderEvalReport",
    "build_provider_readiness_matrix",
    "load_provider_eval_cases",
    "run_provider_eval",
]
