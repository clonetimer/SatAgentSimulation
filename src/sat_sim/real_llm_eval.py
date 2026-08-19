"""V37-D provider evaluation for composite whole-spacecraft TaskSpec generation.

The deterministic template backend is a baseline, not a real-LLM result.  Real
provider reports are emitted only when the selected backend is configured; the
harness never silently converts missing credentials into a claimed model run.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from .task_spec import write_json
from .parameter_consumption import audit_parameter_consumption
from .unified_agent import UnifiedAgentRequest, run_unified_agent

EVAL_SCHEMA_VERSION = "v0566.composite-provider-eval.v2"
COMPOSITE_CAPABILITY_ID = "whole_spacecraft.composite_digital_twin.v1"

DEFAULT_CASES: tuple[dict[str, Any], ...] = (
    {
        "case_id": "nominal_full_six_subsystems",
        "request": "创建完整六分系统整星仿真，包含轨道、姿态、电源、热控、通信和载荷，运行60秒。",
        "expected_supported": True,
        "expected_mode": "nominal",
        "required_qoi": [],
        "expected_events": [],
    },
    {
        "case_id": "two_faults_independent_times",
        "request": "创建完整六分系统整星仿真，运行60秒，20秒载荷关机，30秒通信中断，输出姿态误差、电池SOC、温度、载荷数据率和下行数据率。",
        "expected_supported": True,
        "expected_mode": "fault",
        "required_qoi": ["attitude_error_deg", "battery_soc", "thermal_temp_c", "payload_generated_bps", "downlink_delivered_bps"],
        "expected_events": [
            {"effect": "payload_instrument_off", "start_s": 20.0},
            {"effect": "comm_data_downlink_link_loss", "start_s": 30.0},
        ],
    },
    {
        "case_id": "adcs_fault",
        "request": "建立全分系统整星模型，在10秒时注入反作用轮卡滞故障，运行50秒，输出姿态误差。",
        "expected_supported": True,
        "expected_mode": "fault",
        "required_qoi": ["attitude_error_deg"],
        "expected_events": [{"effect": "adcs_rw_jamming", "start_s": 10.0}],
    },
    {
        "case_id": "thermal_fault_recovery",
        "request": "完整六系统整星仿真，15秒加热器常开，持续20秒后恢复，运行60秒，输出温度。",
        "expected_supported": True,
        "expected_mode": "fault",
        "required_qoi": ["thermal_temp_c"],
        "expected_events": [{"effect": "thermal_heater_stuck_on", "start_s": 15.0, "end_s": 35.0}],
    },
    {
        "case_id": "build_time_degradation",
        "request": "创建完整整星寿命末期退化仿真，采用多分系统寿命末期退化，运行60秒。",
        "expected_supported": True,
        "expected_mode": "degradation",
        "required_qoi": [],
        "expected_events": [{"effect": "multi_subsystem_end_of_life", "start_s": 0.0}],
    },
    {
        "case_id": "mixed_fault_and_degradation",
        "request": "创建完整六分系统整星混合仿真，初始采用太阳阵列效率下降20%的退化，30秒时载荷关机，运行60秒。",
        "expected_supported": True,
        "expected_mode": "mixed",
        "required_qoi": [],
        "expected_events": [
            {"effect": "solar_panel_efficiency_loss_20pct", "start_s": 0.0},
            {"effect": "payload_instrument_off", "start_s": 30.0},
        ],
    },
    {
        "case_id": "requested_conservation_qoi",
        "request": "建立全系统整星仿真，运行120秒，输出任务成功评分、能量守恒、数据守恒和耦合状态。",
        "expected_supported": True,
        "expected_mode": "nominal",
        "required_qoi": ["mission_success_score", "energy_conservation_status", "data_conservation_status", "proxy_coupling_runtime_status"],
        "expected_events": [],
    },
    {
        "case_id": "unsupported_flight_grade_claim",
        "request": "生成经过飞行数据校准、可用于认证的高保真完整六分系统整星数字孪生。",
        "expected_supported": False,
        "expected_reason_codes": ["HIGH_FIDELITY_CLAIM_BLOCKED"],
    },
    {
        "case_id": "unsupported_timed_degradation",
        "request": "创建完整整星仿真，在20秒开始多分系统寿命末期退化，40秒恢复。",
        "expected_supported": False,
        "expected_reason_codes": ["WHOLE_SPACECRAFT_DEGRADATION_BUILD_TIME_ONLY", "UNSUPPORTED_EFFECT"],
    },
)


def provider_configuration_status(
    backend: str,
    *,
    model_name: str | None = None,
    model_base_url: str | None = None,
    model_api_key_env: str = "OPENAI_API_KEY",
    model_command: str | None = None,
) -> dict[str, Any]:
    normalized = backend.strip().lower()
    if normalized == "template":
        return {"ready": True, "status": "DETERMINISTIC_BASELINE", "provider_kind": "deterministic_baseline", "missing": []}
    missing: list[str] = []
    if normalized == "command":
        if not model_command:
            missing.append("model_command")
    elif normalized in {"openai", "deepseek"}:
        if not model_name:
            missing.append("model_name")
        env_name = "DEEPSEEK_API_KEY" if normalized == "deepseek" and model_api_key_env == "OPENAI_API_KEY" else model_api_key_env
        if not os.environ.get(env_name):
            missing.append(env_name)
    elif normalized in {"openai_compatible", "qwen", "vllm"}:
        if not model_name:
            missing.append("model_name")
        if not model_base_url:
            missing.append("model_base_url")
    else:
        missing.append("supported_backend")
    return {
        "ready": not missing,
        "status": "READY" if not missing else "NOT_EXECUTED_CONFIGURATION_MISSING",
        "provider_kind": "real_llm_provider",
        "missing": missing,
    }


def _events(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    payload = spec.get("events") if isinstance(spec.get("events"), Mapping) else {}
    out: list[dict[str, Any]] = []
    for key in ("faults", "degradations", "constraints"):
        raw = payload.get(key) if isinstance(payload.get(key), Sequence) else []
        for item in raw:
            if isinstance(item, Mapping):
                out.append(dict(item))
    return out


def _reason_codes(result: Any) -> set[str]:
    codes = set(str(item) for item in result.reason_codes)
    if result.planning is not None:
        codes.update(item.code for item in result.planning.validation.issues)
    codes.update(item.code for item in result.validation.errors)
    codes.update(item.code for item in result.guards.issues)
    task_spec = result.task_spec or {}
    metadata = task_spec.get("metadata") if isinstance(task_spec, Mapping) else {}
    planner = metadata.get("planner") if isinstance(metadata, Mapping) else {}
    unsupported = planner.get("unsupported_requirements") if isinstance(planner, Mapping) else []
    if isinstance(unsupported, Sequence):
        for item in unsupported:
            if isinstance(item, Mapping) and item.get("reason_code"):
                codes.add(str(item["reason_code"]))
    return codes


def _evaluate_case(case: Mapping[str, Any], result: Any, *, require_real_model_evidence: bool = False) -> dict[str, Any]:
    spec = result.task_spec or {}
    capability_id = (spec.get("model") or {}).get("capability_id") if isinstance(spec.get("model"), Mapping) else None
    target = (spec.get("model") or {}).get("target") if isinstance(spec.get("model"), Mapping) else {}
    mode = target.get("mode") if isinstance(target, Mapping) else None
    qoi = set(str(item) for item in ((spec.get("outputs") or {}).get("qoi") or [])) if isinstance(spec.get("outputs"), Mapping) else set()
    actual_events = _events(spec)
    reasons = _reason_codes(result)
    facade_payload = result.facade_result.to_dict() if getattr(result, "facade_result", None) is not None else {}
    backend_identity = facade_payload.get("backend_identity") if isinstance(facade_payload.get("backend_identity"), Mapping) else {}
    invocation_evidence = backend_identity.get("invocation_evidence") if isinstance(backend_identity, Mapping) else None
    parameter_audit = audit_parameter_consumption(spec) if spec else None
    required_couplings = ((spec.get("mission") or {}).get("required_couplings") or []) if isinstance(spec.get("mission"), Mapping) else []
    planning_resolved = getattr(result.planning, "resolved_spec", None) if getattr(result, "planning", None) is not None else None
    resolved_couplings = list(getattr(planning_resolved, "required_couplings", []) or [])
    checks: dict[str, bool] = {}
    checks["parameter_consumption"] = bool(parameter_audit and parameter_audit.ok)
    checks["causal_requirements_preserved"] = sorted(map(str, required_couplings)) == sorted(map(str, resolved_couplings))
    if require_real_model_evidence:
        checks["model_invocation_evidence"] = bool(
            isinstance(invocation_evidence, Mapping)
            and invocation_evidence.get("verified") is True
            and invocation_evidence.get("prompt_sha256")
            and invocation_evidence.get("response_sha256")
            and invocation_evidence.get("structured_output_sha256")
        )
        checks["no_fallback"] = bool(backend_identity.get("backend") not in {None, "", "template"})
    if bool(case.get("expected_supported")):
        checks["supported"] = bool(result.ok)
        checks["capability"] = capability_id == COMPOSITE_CAPABILITY_ID
        checks["mode"] = mode == case.get("expected_mode")
        checks["qoi"] = set(case.get("required_qoi") or []).issubset(qoi)
        for index, expected in enumerate(case.get("expected_events") or []):
            matching = [item for item in actual_events if item.get("effect") == expected.get("effect")]
            checks[f"event_{index}_present"] = bool(matching)
            if matching:
                checks[f"event_{index}_start"] = abs(float(matching[0].get("start_s", -1)) - float(expected.get("start_s", -2))) < 1e-9
                if "end_s" in expected:
                    checks[f"event_{index}_end"] = abs(float(matching[0].get("end_s", -1)) - float(expected["end_s"])) < 1e-9
    else:
        checks["rejected"] = not bool(result.ok)
        expected_codes = set(case.get("expected_reason_codes") or [])
        checks["reason_code"] = bool(expected_codes & reasons)
    return {
        "case_id": case.get("case_id"),
        "request": case.get("request"),
        "expected_supported": bool(case.get("expected_supported")),
        "passed": all(checks.values()) if checks else False,
        "checks": checks,
        "actual": {
            "ok": result.ok,
            "capability_id": capability_id,
            "mode": mode,
            "qoi": sorted(qoi),
            "events": actual_events,
            "reason_codes": sorted(reasons),
            "route": result.route.to_dict(),
            "backend_identity": backend_identity,
            "parameter_consumption": parameter_audit.to_dict() if parameter_audit else None,
            "required_couplings": list(required_couplings),
            "resolved_required_couplings": resolved_couplings,
        },
    }


def run_composite_provider_eval(
    *,
    backend: str = "template",
    output_dir: str | Path = "reports/v37d_composite_provider_eval",
    model_name: str | None = None,
    model_base_url: str | None = None,
    model_api_key_env: str = "OPENAI_API_KEY",
    model_command: str | None = None,
    cases: Sequence[Mapping[str, Any]] = DEFAULT_CASES,
) -> dict[str, Any]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    configuration = provider_configuration_status(
        backend,
        model_name=model_name,
        model_base_url=model_base_url,
        model_api_key_env=model_api_key_env,
        model_command=model_command,
    )
    if not configuration["ready"]:
        report = {
            "schema_version": EVAL_SCHEMA_VERSION,
            "backend": backend,
            "model_name": model_name,
            "provider_execution_status": configuration["status"],
            "provider_kind": configuration["provider_kind"],
            "configuration": configuration,
            "case_count": 0,
            "passed_count": 0,
            "failed_count": 0,
            "cases": [],
            "claim_guardrail": "No real-provider result is claimed because configuration is incomplete.",
        }
        write_json(root / "report.json", report)
        return report

    results: list[dict[str, Any]] = []
    for case in cases:
        case_id = str(case.get("case_id") or f"case_{len(results) + 1}")
        try:
            result = run_unified_agent(UnifiedAgentRequest(
                input_kind="natural_language",
                request_text=str(case.get("request") or ""),
                output_dir=root / "cases" / case_id,
                backend=backend,
                local_backend="template",
                remote_backend=backend if backend != "template" else None,
                model_command=model_command,
                model_name=model_name,
                model_base_url=model_base_url,
                model_api_key_env=model_api_key_env,
                model_temperature=0.0,
                model_structured_output="json_object",
                compile_if_valid=True,
            ))
            results.append(_evaluate_case(case, result, require_real_model_evidence=backend != "template"))
        except Exception as exc:
            results.append({
                "case_id": case_id,
                "request": case.get("request"),
                "expected_supported": bool(case.get("expected_supported")),
                "passed": False,
                "checks": {"pipeline_exception": False},
                "actual": {"exception_type": type(exc).__name__, "message": str(exc)},
            })
    passed = sum(1 for item in results if item["passed"])
    provider_status = "DETERMINISTIC_BASELINE_COMPLETED" if backend == "template" else "REAL_PROVIDER_COMPLETED"
    invocation_verified = sum(
        1 for item in results
        if bool(((item.get("actual") or {}).get("backend_identity") or {}).get("actual_model_execution_verified"))
    )
    parameter_audit_passed = sum(
        1 for item in results
        if bool(((item.get("actual") or {}).get("parameter_consumption") or {}).get("ok"))
    )
    report = {
        "schema_version": EVAL_SCHEMA_VERSION,
        "backend": backend,
        "model_name": model_name,
        "provider_execution_status": provider_status,
        "provider_kind": configuration["provider_kind"],
        "configuration": configuration,
        "case_count": len(results),
        "passed_count": passed,
        "failed_count": len(results) - passed,
        "pass_rate": passed / len(results) if results else 0.0,
        "model_invocation_evidence_verified_count": invocation_verified,
        "model_invocation_evidence_rate": invocation_verified / len(results) if results else 0.0,
        "parameter_consumption_passed_count": parameter_audit_passed,
        "parameter_consumption_pass_rate": parameter_audit_passed / len(results) if results else 0.0,
        "cases": results,
        "claim_guardrail": (
            "This is a deterministic template baseline and is not a real-LLM score."
            if backend == "template"
            else "Provider score covers TaskSpec generation and deterministic validation only."
        ),
    }
    write_json(root / "cases.json", list(cases))
    write_json(root / "report.json", report)
    return report


__all__ = [
    "EVAL_SCHEMA_VERSION",
    "COMPOSITE_CAPABILITY_ID",
    "DEFAULT_CASES",
    "provider_configuration_status",
    "run_composite_provider_eval",
]
