"""LM Studio target-device acceptance workflow.

The workflow distinguishes a real LM Studio execution from a protocol fixture.
It never upgrades an unreachable endpoint or a mock server to an actual-device
PASS.  OpenAI-compatible endpoints are used for the constrained Agent, while
LM Studio's native v1 REST endpoint is queried for server/model metadata when
available.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse
import ipaddress

from .model_providers import ModelProviderProfile, ModelProviderRegistry
from .release_closure import RELEASE_VERSION
from .run_bundle import execute_prepared_run, prepare_run
from .unified_agent import UnifiedAgentRequest, run_unified_agent

REPORT_VERSION = "sat-sim.lmstudio-acceptance.v2"


def _root_url(base_url: str) -> str:
    value = str(base_url).rstrip("/")
    return value[:-3] if value.endswith("/v1") else value


def _headers(api_key_env: str | None) -> dict[str, str]:
    result = {"Accept": "application/json", "Content-Type": "application/json"}
    key = os.environ.get(api_key_env or "") if api_key_env else None
    if key:
        result["Authorization"] = f"Bearer {key}"
    return result


def _request_json(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    body: Mapping[str, Any] | None = None,
    timeout_s: float = 10.0,
    max_bytes: int = 2_097_152,
) -> tuple[int, Any, float]:
    data = json.dumps(dict(body), ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, method=method, headers=dict(headers or {}), data=data)
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # nosec B310 - operator configured localhost/LAN endpoint
        raw = response.read(max_bytes).decode("utf-8", errors="replace")
        status = int(response.status)
    return status, json.loads(raw) if raw else {}, round((time.perf_counter() - started) * 1000.0, 2)


def _error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            excerpt = exc.read(65536).decode("utf-8", errors="replace")[:1000]
        except Exception:
            excerpt = ""
        return {"ok": False, "status": "HTTP_ERROR", "http_status": int(exc.code), "reason": str(exc), "response_excerpt": excerpt}
    if isinstance(exc, urllib.error.URLError):
        return {"ok": False, "status": "UNREACHABLE", "reason": str(getattr(exc, "reason", exc))}
    if isinstance(exc, TimeoutError):
        return {"ok": False, "status": "TIMEOUT", "reason": str(exc)}
    return {"ok": False, "status": "FAILED", "reason": str(exc)}


@dataclass(frozen=True)
class AcceptanceCase:
    case_id: str
    request_text: str
    expected_capability_ids: tuple[str, ...]
    required_outputs: tuple[str, ...] = ()
    base_case_id: str | None = None
    expected_mode: str | None = None
    expected_effects: tuple[str, ...] = ()


def _legacy_degradation_effects(value: Any) -> set[str]:
    if not isinstance(value, Mapping):
        return set()
    effects: set[str] = set()
    for key, nested in value.items():
        if isinstance(nested, Mapping):
            effects.update(_legacy_degradation_effects(nested))
        elif nested is not None:
            effects.add(str(key))
    return effects


DEFAULT_CASES: tuple[AcceptanceCase, ...] = (
    AcceptanceCase(
        "adcs_create",
        "创建一个ADCS统一运行图仿真，运行20秒，输出姿态误差和三个反作用轮轮速。",
        ("subsystem.adcs_unified_native.v1",),
        ("pointing_error", "rw.speed"),
    ),
    AcceptanceCase(
        "adcs_refine",
        "把仿真时长改成30秒，并追加输出陀螺角速度，保留原有能力和轮速输出。",
        ("subsystem.adcs_unified_native.v1",),
        ("rw.speed", "gyro"),
        base_case_id="adcs_create",
    ),
    AcceptanceCase(
        "whole_create",
        "创建整星统一运行图仿真，运行40秒，输出电池SOC、载荷数据率、下行数据率和温度。",
        ("whole_spacecraft.unified_native.v1",),
        ("battery_soc", "payload.generated", "comm.downlink", "thermal.payload_temp"),
    ),
)


@dataclass
class LMStudioAcceptanceReport:
    schema_version: str = REPORT_VERSION
    release_version: str = RELEASE_VERSION
    execution_kind: str = "actual_lmstudio"
    overall_status: str = "NOT_EXECUTED"
    ok: bool = False
    base_url: str = ""
    endpoint_scope: str = "unknown"
    model: str | None = None
    platform: dict[str, Any] = field(default_factory=dict)
    openai_models: dict[str, Any] = field(default_factory=dict)
    native_models: dict[str, Any] = field(default_factory=dict)
    structured_probe: dict[str, Any] = field(default_factory=dict)
    taskspec_cases: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    invocation_evidence_summary: dict[str, Any] = field(default_factory=dict)
    fallback_detected: bool = False
    operator_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _extract_native_models(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        rows = payload.get("models") or payload.get("data")
    else:
        rows = payload
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return []
    out: list[dict[str, Any]] = []
    for item in rows:
        if isinstance(item, Mapping):
            out.append({
                "id": item.get("id") or item.get("key") or item.get("model") or item.get("name"),
                "loaded": bool(
                    item.get("loaded") is True
                    or item.get("state") == "loaded"
                    or (isinstance(item.get("loaded_instances"), Sequence) and not isinstance(item.get("loaded_instances"), (str, bytes)) and bool(item.get("loaded_instances")))
                ),
                "type": item.get("type"),
                "quantization": item.get("quantization"),
                "max_context_length": item.get("max_context_length") or item.get("context_length"),
                "raw": dict(item),
            })
    return out


def run_lmstudio_acceptance(
    *,
    base_url: str = "http://127.0.0.1:1234/v1",
    model: str | None = None,
    api_key_env: str = "SAT_SIM_LMSTUDIO_API_KEY",
    timeout_s: float = 300.0,
    output_dir: str | Path = "reports/lmstudio_acceptance",
    execution_kind: str = "actual_lmstudio",
    cases: Sequence[AcceptanceCase] = DEFAULT_CASES,
    require_loopback: bool = False,
    max_output_tokens: int = 2048,
    execute_generated: bool = False,
    execution_timeout_s: float = 300.0,
    max_repair_attempts: int = 2,
) -> LMStudioAcceptanceReport:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        endpoint_host = urlparse(base_url).hostname or ""
        endpoint_loopback = endpoint_host.lower() == "localhost" or ipaddress.ip_address(endpoint_host).is_loopback
    except Exception:
        endpoint_loopback = False
    report = LMStudioAcceptanceReport(
        execution_kind=execution_kind,
        base_url=base_url,
        endpoint_scope="loopback" if endpoint_loopback else "remote_or_unknown",
        model=model,
        platform={"system": platform.system(), "release": platform.release(), "python": platform.python_version()},
    )
    headers = _headers(api_key_env)
    endpoint_root = _root_url(base_url)

    try:
        status, payload, latency = _request_json(endpoint_root + "/v1/models", headers=headers, timeout_s=min(timeout_s, 30.0))
        model_ids = [str(item.get("id")) for item in payload.get("data", []) if isinstance(item, Mapping) and item.get("id")] if isinstance(payload, Mapping) else []
        report.openai_models = {"ok": 200 <= status < 300, "http_status": status, "latency_ms": latency, "models": model_ids}
        if not model:
            model = model_ids[0] if model_ids else None
            report.model = model
    except Exception as exc:
        report.openai_models = _error_payload(exc)

    try:
        status, payload, latency = _request_json(endpoint_root + "/api/v1/models", headers=headers, timeout_s=min(timeout_s, 30.0))
        report.native_models = {
            "ok": 200 <= status < 300,
            "http_status": status,
            "latency_ms": latency,
            "models": _extract_native_models(payload),
        }
    except Exception as exc:
        report.native_models = _error_payload(exc)

    if not report.openai_models.get("ok"):
        report.overall_status = "NOT_EXECUTED_LMSTUDIO_UNREACHABLE"
        report.operator_actions = ["启动 LM Studio Local Server。", "确认端口、认证 Token 和防火墙设置。", "重新运行验收脚本。"]
        (root / "lmstudio_acceptance_report.json").write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report
    if not model:
        report.overall_status = "NOT_EXECUTED_NO_MODEL_VISIBLE"
        report.operator_actions = ["在 LM Studio 中下载并加载一个指令模型，或启用 JIT loading。"]
        (root / "lmstudio_acceptance_report.json").write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report

    profile = ModelProviderProfile(
        provider_id="local-lmstudio",
        label="目标机 LM Studio",
        backend="openai_compatible",
        location="local",
        tier="L2",
        model=model,
        base_url=base_url,
        api_key_env=api_key_env,
        structured_output="json_schema",
        timeout_s=timeout_s,
        temperature=0.0,
        max_output_tokens=max_output_tokens,
        source="target_acceptance",
    )
    registry = ModelProviderRegistry(profiles=[profile])
    report.structured_probe = registry.probe_generation("local-lmstudio", timeout_s=timeout_s)

    provider_file = root / "model_providers.acceptance.json"
    provider_file.write_text(json.dumps({"providers": [profile.to_dict(include_runtime=True)]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    created_specs: dict[str, dict[str, Any]] = {}
    for case in cases:
        case_dir = root / "cases" / case.case_id
        base_spec = created_specs.get(case.base_case_id or "")
        if case.base_case_id and base_spec is None:
            report.taskspec_cases.append({
                "case_id": case.case_id,
                "passed": False,
                "status": "BLOCKED_BASE_CASE_UNAVAILABLE",
                "base_case_id": case.base_case_id,
                "error": f"Base case {case.base_case_id!r} did not produce a valid TaskSpec.",
                "evidence_dir": str(case_dir),
            })
            continue
        try:
            result = run_unified_agent(UnifiedAgentRequest(
                input_kind="natural_language",
                request_text=case.request_text,
                base_task_spec=base_spec,
                output_dir=case_dir,
                backend="auto",
                provider_id="local-lmstudio",
                provider_config_path=provider_file,
                routing_mode="local",
                compile_if_valid=True,
                model_timeout_s=timeout_s,
                model_temperature=0.0,
                model_max_output_tokens=max_output_tokens,
                model_structured_output="json_schema",
                max_repair_attempts=max_repair_attempts,
            ))
            spec = result.task_spec if isinstance(result.task_spec, dict) else {}
            capability_id = (spec.get("model") or {}).get("capability_id") if isinstance(spec.get("model"), Mapping) else None
            outputs = json.dumps(spec.get("outputs") or {}, ensure_ascii=False).lower()
            target = (spec.get("model") or {}).get("target") if isinstance(spec.get("model"), Mapping) else {}
            actual_mode = target.get("mode") if isinstance(target, Mapping) else None
            event_payload = spec.get("events") if isinstance(spec.get("events"), Mapping) else {}
            actual_effects = {
                str(item.get("effect"))
                for plural in ("faults", "degradations", "constraints")
                for item in (
                    event_payload.get(plural)
                    if isinstance(event_payload.get(plural), Sequence)
                    and not isinstance(event_payload.get(plural), (str, bytes))
                    else ()
                )
                if isinstance(item, Mapping) and item.get("effect")
            }
            model_payload = spec.get("model") if isinstance(spec.get("model"), Mapping) else {}
            actual_effects.update(
                _legacy_degradation_effects(model_payload.get("legacy_degradations"))
            )
            facade_payload = result.facade_result.to_dict() if result.facade_result is not None else {}
            backend_identity = facade_payload.get("backend_identity") if isinstance(facade_payload.get("backend_identity"), Mapping) else {}
            invocation = backend_identity.get("invocation_evidence") if isinstance(backend_identity.get("invocation_evidence"), Mapping) else {}
            invocation_verified = bool(
                backend_identity.get("actual_model_execution_verified") is True
                and invocation.get("verified") is True
                and invocation.get("model_id")
                and invocation.get("request_id")
                and invocation.get("prompt_sha256")
                and invocation.get("response_sha256")
                and invocation.get("structured_output_sha256")
            )
            spec_metadata = spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}
            agent_safety = spec_metadata.get("agent_safety") if isinstance(spec_metadata.get("agent_safety"), Mapping) else {}
            no_fallback = bool(
                result.route.provider_id == "local-lmstudio"
                and backend_identity.get("backend") == "openai_compatible"
                and backend_identity.get("actual_model_used") is True
                and agent_safety.get("model_fallback_used") is not True
            )
            checks = {
                "provider_selected": result.route.provider_id == "local-lmstudio",
                "valid": bool(result.validation.ok and result.guards.ok),
                "compiled": result.compiled is not None,
                "capability": capability_id in case.expected_capability_ids,
                "outputs": all(token.lower() in outputs for token in case.required_outputs),
                "actual_model_invocation_verified": invocation_verified,
                "no_fallback": no_fallback,
            }
            if case.expected_mode is not None:
                checks["mode"] = actual_mode == case.expected_mode
            if case.expected_effects:
                checks["effects"] = set(case.expected_effects).issubset(actual_effects)
            execution_evidence: dict[str, Any] = {}
            if execute_generated and checks["valid"] and checks["compiled"]:
                script_path = Path(result.files.get("generated_script") or "")
                script_stdout = case_dir / "generated_script.stdout.log"
                script_stderr = case_dir / "generated_script.stderr.log"
                if script_path.is_file():
                    started = time.perf_counter()
                    try:
                        with script_stdout.open("w", encoding="utf-8") as stdout, script_stderr.open(
                            "w", encoding="utf-8"
                        ) as stderr:
                            completed = subprocess.run(
                                [sys.executable, str(script_path)],
                                cwd=case_dir,
                                stdout=stdout,
                                stderr=stderr,
                                text=True,
                                check=False,
                                timeout=execution_timeout_s,
                            )
                        execution_evidence["script"] = {
                            "path": str(script_path),
                            "returncode": completed.returncode,
                            "elapsed_s": round(time.perf_counter() - started, 6),
                            "stdout": str(script_stdout),
                            "stderr": str(script_stderr),
                        }
                        checks["generated_script_executed"] = completed.returncode == 0
                    except subprocess.TimeoutExpired:
                        execution_evidence["script"] = {
                            "path": str(script_path),
                            "timed_out": True,
                            "timeout_s": execution_timeout_s,
                        }
                        checks["generated_script_executed"] = False
                else:
                    execution_evidence["script"] = {"path": str(script_path), "missing": True}
                    checks["generated_script_executed"] = False

                try:
                    prepared = prepare_run(
                        spec,
                        output_root=case_dir / "runs",
                        run_id=f"{case.case_id}_e2e",
                    )
                    run = execute_prepared_run(
                        prepared.bundle_root,
                        expected_plan_sha256=prepared.execution_plan_sha256,
                    )
                    execution_evidence["run_bundle"] = {
                        "bundle_root": run.bundle_root,
                        "run_status": run.run_record.status.value,
                        "sealed": run.run_record.sealed,
                        "validation_result": run.validation.result.value,
                        "validation_reason_code": run.validation.reason_code,
                        "requested_claim_level": run.claim_report.requested_claim_level,
                    }
                    checks["run_bundle_succeeded"] = (
                        run.run_record.status.value == "SUCCEEDED"
                        and run.run_record.sealed is True
                    )
                    checks["physical_validation_passed"] = run.validation.result.value == "PASS"
                except Exception as exc:
                    execution_evidence["run_bundle"] = {
                        "exception_type": type(exc).__name__,
                        "message": str(exc),
                    }
                    checks["run_bundle_succeeded"] = False
                    checks["physical_validation_passed"] = False
            passed = all(checks.values())
            report.taskspec_cases.append({
                "case_id": case.case_id,
                "passed": passed,
                "checks": checks,
                "capability_id": capability_id,
                "mode": actual_mode,
                "effects": sorted(actual_effects),
                "reason_codes": list(result.reason_codes),
                "backend_identity": backend_identity,
                "invocation_evidence": invocation,
                "execution_evidence": execution_evidence,
                "evidence_dir": str(case_dir),
            })
            # Preserve a semantically valid base TaskSpec even when its output
            # compilation check fails.  This keeps the refinement case
            # diagnostically independent instead of turning a first-turn QoI
            # issue into a misleading missing-subsystem exception.
            if checks["valid"] and checks["capability"]:
                created_specs[case.case_id] = spec
        except Exception as exc:
            report.taskspec_cases.append({"case_id": case.case_id, "passed": False, "error": str(exc), "evidence_dir": str(case_dir)})

    passed = sum(1 for item in report.taskspec_cases if item.get("passed"))
    invocation_verified = sum(
        1 for item in report.taskspec_cases
        if isinstance(item.get("checks"), Mapping) and item["checks"].get("actual_model_invocation_verified") is True
    )
    fallback_cases = [
        str(item.get("case_id")) for item in report.taskspec_cases
        if not isinstance(item.get("checks"), Mapping) or item["checks"].get("no_fallback") is not True
    ]
    report.fallback_detected = bool(fallback_cases)
    report.invocation_evidence_summary = {
        "verified_count": invocation_verified,
        "required_count": len(report.taskspec_cases),
        "all_verified": bool(report.taskspec_cases and invocation_verified == len(report.taskspec_cases)),
        "fallback_cases": fallback_cases,
    }
    report.summary = {
        "structured_output_ok": bool(report.structured_probe.get("ok")),
        "taskspec_case_count": len(report.taskspec_cases),
        "taskspec_passed": passed,
        "taskspec_pass_rate": passed / len(report.taskspec_cases) if report.taskspec_cases else 0.0,
        "native_api_available": bool(report.native_models.get("ok")),
        "actual_model_invocation_verified_count": invocation_verified,
        "fallback_detected": report.fallback_detected,
    }
    reasoning_exhausted_cases = [
        str(item.get("case_id")) for item in report.taskspec_cases
        if "exhausted the output budget in reasoning" in str((item.get("invocation_evidence") or {}).get("error") or "")
    ]
    report.summary["reasoning_budget_exhausted_cases"] = reasoning_exhausted_cases
    semantic_ok = bool(report.structured_probe.get("ok") and passed == len(report.taskspec_cases) and report.taskspec_cases)
    lmstudio_identity_ok = bool(report.native_models.get("ok"))
    actual_ok = bool(semantic_ok and lmstudio_identity_ok and not report.fallback_detected)
    if require_loopback and not endpoint_loopback:
        actual_ok = False
        report.operator_actions.append("本次验收要求本机 LM Studio，但 base_url 不是 loopback 地址。")
    report.ok = bool(actual_ok and execution_kind == "actual_lmstudio")
    report.overall_status = (
        "PASS" if report.ok else
        "PROTOCOL_FIXTURE_PASS_NOT_ACTUAL_LMSTUDIO" if actual_ok else
        "OPENAI_COMPATIBLE_PASS_LMSTUDIO_IDENTITY_UNVERIFIED" if semantic_ok and not lmstudio_identity_ok else
        "BLOCKED_LOCAL_MODEL_REASONING_EXHAUSTED" if reasoning_exhausted_cases and lmstudio_identity_ok else
        "FAIL"
    )
    if not report.native_models.get("ok"):
        report.operator_actions.append("无法通过 /api/v1/models 证明服务端为真实 LM Studio；OpenAI-compatible 语义结果不能关闭 LM Studio 身份门禁。")
    if passed != len(report.taskspec_cases):
        report.operator_actions.append("查看 cases 目录中的模型原始响应、验证错误和回退记录，调整模型或上下文长度。")
    (root / "lmstudio_acceptance_report.json").write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


__all__ = ["AcceptanceCase", "DEFAULT_CASES", "LMStudioAcceptanceReport", "run_lmstudio_acceptance"]
