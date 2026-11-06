"""Paired real-vLLM A/B evaluation for approved advisory experience reuse."""
from __future__ import annotations

import json
import math
import subprocess
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from .experience import ExperienceScope, ExperienceStore, _canonical, _sha256
from .experience_lessons import ExperienceLessonService
from .agent_eval_attribution import analyze_case, aggregate_cases, normalize_token_usage
from .task_spec import write_json
from .unified_agent import UnifiedAgentRequest, run_unified_agent

DEFAULT_HELD_OUT_REQUESTS = (
    "capability_id=component.battery.v1, mode=nominal. Simulate 8 seconds and report battery voltage and state of charge.",
    "为 capability_id=component.battery.v1 生成正常工况工程仿真，持续 10 秒，输出电池电压和荷电状态。",
    "Create a nominal battery engineering simulation with duration 12 seconds and a 1 second sample interval.",
    "电池正常运行仿真：capability_id=component.battery.v1，时长 14 秒，采样间隔 1 秒。",
    "Use component.battery.v1 in nominal mode for 16 seconds; produce a deterministic simulation script.",
    "对 component.battery.v1 执行 18 秒正常模式仿真，并验证运行结果满足物理约束。",
)


def _model_metrics(output_dir: Path) -> dict[str, Any]:
    """Return missing/invalid model evidence as unknown, never zero cost.

    A deterministic pre-model rejection is a valid failed test outcome and
    must not crash the entire paired benchmark.
    """
    path = output_dir / "model_call.json"
    payload: Any = None
    error_code: str | None = None
    if not path.is_file():
        error_code = "MODEL_CALL_EVIDENCE_MISSING"
    else:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            error_code = "MODEL_CALL_EVIDENCE_INVALID"
    evidence = (
        payload.get("invocation_evidence")
        if isinstance(payload, dict)
        else None
    )
    if not isinstance(evidence, dict):
        evidence = {}
        if error_code is None:
            error_code = "MODEL_CALL_EVIDENCE_INVALID"
    usage = normalize_token_usage(evidence.get("usage"))
    latency = evidence.get("latency_ms")
    latency_ms = (
        float(latency)
        if isinstance(latency, (int, float))
        and not isinstance(latency, bool)
        and math.isfinite(float(latency))
        and float(latency) >= 0
        else None
    )
    return {
        "model_call_present": path.is_file(),
        "model_call_evidence_error": error_code,
        "verified": error_code is None and evidence.get("verified") is True,
        "latency_ms": latency_ms,
        **usage,
        "request_id": evidence.get("request_id"),
    }


def _sign_test_p_value(wins: int, losses: int) -> float:
    trials = wins + losses
    if trials == 0:
        return 1.0
    extreme = max(wins, losses)
    tail = sum(math.comb(trials, index) for index in range(extreme, trials + 1))
    return min(1.0, 2.0 * tail / (2**trials))


def _paired_cost_statistics(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Summarize only complete, attributable token pairs.

    An empty or entirely unknown cost comparison is a failed/unknown cost
    result, never zero-token savings or a statistics.median exception.
    """
    token_deltas = [
        item["delta"]["attributed_total_tokens_saved"]
        for item in cases
        if item["delta"].get("attributed_total_tokens_saved") is not None
    ]
    latency_deltas = [
        item["delta"]["attributed_latency_ms_saved"]
        for item in cases
        if item["delta"].get("attributed_latency_ms_saved") is not None
    ]
    wins = sum(value > 0 for value in token_deltas)
    losses = sum(value < 0 for value in token_deltas)
    ties = sum(value == 0 for value in token_deltas)
    token_median = statistics.median(token_deltas) if token_deltas else None
    latency_median = statistics.median(latency_deltas) if latency_deltas else None
    return {
        "token_usage_complete": bool(cases) and len(token_deltas) == len(cases),
        "token_pair_count": len(token_deltas),
        "token_unknown_pair_count": len(cases) - len(token_deltas),
        "token_wins": wins,
        "token_losses": losses,
        "token_ties": ties,
        "token_sign_test_p_value": _sign_test_p_value(wins, losses),
        "median_total_tokens_saved": token_median,
        "median_latency_ms_saved": latency_median,
        "median_total_tokens_saved_positive": (
            token_median is not None and token_median > 0
        ),
    }


def _execute_supervised(task_spec_path: Path, runs_root: Path) -> dict[str, Any]:
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "sat_sim.agent_cli",
            "run",
            str(task_spec_path),
            "--output-root",
            str(runs_root),
            "--max-attempts",
            "1",
            "--isolated-process-exit",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 120.0
    bundle = None
    while time.monotonic() < deadline:
        candidates = [
            path.parent
            for path in runs_root.glob("*/SEALED.json")
            if path.is_file()
        ]
        if candidates:
            bundle = max(candidates, key=lambda path: path.stat().st_mtime_ns)
            break
        if process.poll() is not None:
            break
        time.sleep(0.2)
    if bundle is not None:
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3.0)
        record = json.loads((bundle / "run_record.json").read_text(encoding="utf-8"))
        validation = json.loads(
            (bundle / "validation" / "validation_outcome.json").read_text(
                encoding="utf-8"
            )
        )
        return {
            "sealed": True,
            "run_record_status": record.get("status"),
            "validation_result": validation.get("result"),
            "bundle_root": str(bundle),
            "supervised_termination": process.returncode not in {0, None},
        }
    if process.poll() is None:
        process.kill()
    stdout, stderr = process.communicate(timeout=5.0)
    return {
        "sealed": False,
        "run_record_status": None,
        "validation_result": None,
        "return_code": process.returncode,
        "stdout": stdout[-2_000:],
        "stderr": stderr[-2_000:],
    }


def run_experience_longitudinal_eval(
    *,
    source_bundle: str | Path,
    output_dir: str | Path,
    base_url: str = "http://127.0.0.1:8000/v1",
    model: str = "Qwen3.5-9B",
    requests: Sequence[str] = DEFAULT_HELD_OUT_REQUESTS,
    max_repair_attempts: int = 2,
) -> dict[str, Any]:
    if len(requests) < 6:
        raise ValueError("longitudinal evaluation requires at least six paired cases")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "_"
        + uuid4().hex[:8]
    )
    output = root / "evidence" / run_id
    store = ExperienceStore(output / "store")
    service = ExperienceLessonService(store)
    scope = ExperienceScope(tenant_id="internal", project_id="longitudinal-eval")
    source, _ = store.capture_run_bundle(
        source_bundle,
        request_text="verified battery baseline source",
        scope=scope,
        actor="capture-service",
    )
    lesson, _ = service.compile(
        [source.experience_id],
        scope=scope,
        actor="compiler-a",
    )
    preliminary = {
        "runner": "preliminary-held-out-gate",
        "case_ids": ["preliminary-battery-nominal"],
        "result": "PASS",
    }
    preliminary_hash = service.store_evaluation_artifact(preliminary)
    preliminary_metrics = {
        "baseline": {
            "primary_score": 1.0,
            "safety_regressions": 0,
            "physical_regressions": 0,
            "backward_regressions": 0,
        },
        "candidate": {
            "primary_score": 1.0,
            "safety_regressions": 0,
            "physical_regressions": 0,
            "backward_regressions": 0,
        },
    }
    preliminary_metrics_hash = service.store_evaluation_artifact(preliminary_metrics)
    service.evaluate(
        lesson.lesson_id,
        scope=scope,
        evaluator="evaluator-a",
        held_out_case_ids=("preliminary-battery-nominal",),
        baseline_metrics={
            **preliminary_metrics["baseline"],
        },
        candidate_metrics={
            **preliminary_metrics["candidate"],
        },
        evaluation_evidence={
            "runner": preliminary["runner"],
            "case_result_sha256": preliminary_hash,
            "metrics_source_sha256": preliminary_metrics_hash,
        },
        require_evidence_objects=True,
    )
    for reviewer in ("reviewer-a", "reviewer-b"):
        service.review(
            lesson.lesson_id,
            scope=scope,
            reviewer=reviewer,
            decision="APPROVE",
            reason="preliminary held-out gate passed",
        )
    snapshot = service.create_snapshot(scope=scope, actor="release-manager")

    cases = []
    for index, request_text in enumerate(requests):
        pair: dict[str, Any] = {"case_id": f"held-out-{index + 1}", "request": request_text}
        for arm, enabled in (("baseline", False), ("experience", True)):
            case_output = output / "cases" / f"{index + 1:02d}" / arm
            result = run_unified_agent(
                UnifiedAgentRequest(
                    input_kind="natural_language",
                    request_text=request_text,
                    output_dir=case_output,
                    backend="vllm",
                    local_backend="template",
                    remote_backend="vllm",
                    model_name=model,
                    model_base_url=base_url,
                    model_timeout_s=180.0,
                    model_temperature=0.0,
                    model_seed=0,
                    model_max_output_tokens=2048,
                    model_structured_output="json_schema",
                    max_repair_attempts=max_repair_attempts,
                    compile_if_valid=True,
                    experience_reuse_enabled=enabled,
                    experience_store_root=store.root if enabled else None,
                    experience_tenant_id=scope.tenant_id if enabled else None,
                    experience_project_id=scope.project_id if enabled else None,
                )
            )
            run_pass = False
            validation_result = None
            run_status = None
            execution = {}
            if result.ok:
                execution = _execute_supervised(
                    case_output / "task_spec.json",
                    case_output / "runs",
                )
                validation_result = execution.get("validation_result")
                run_status = execution.get("run_record_status")
                run_pass = bool(
                    execution.get("sealed") is True
                    and validation_result == "PASS"
                    and run_status == "SUCCEEDED"
                )
            attribution = analyze_case(
                case_id=f"held-out-{index + 1}:{arm}",
                output_dir=case_output,
                bundle_root=execution.get("bundle_root"),
            )
            attributed_calls = (
                ((attribution.get("generation") or {}).get("model_calls") or {})
            )
            pair[arm] = {
                "agent_ok": result.ok,
                "validation_ok": result.validation.ok,
                "guards_ok": result.guards.ok,
                "planning_ok": bool(result.planning and result.planning.ok),
                "run_pass": run_pass,
                "validation_result": validation_result,
                "run_status": run_status,
                "fallback_used": any(
                    "fallback" in step.name
                    for step in (result.facade_result.steps if result.facade_result else ())
                ),
                "experience_presented": "EXPERIENCE_ADVISORY_PRESENTED"
                in result.reason_codes,
                "attribution": attribution,
                "attributed_prompt_tokens": attributed_calls.get("prompt_tokens"),
                "attributed_completion_tokens": attributed_calls.get("completion_tokens"),
                "attributed_total_tokens": attributed_calls.get("total_tokens"),
                "attributed_total_tokens_unknown_call_count": int(
                    attributed_calls.get("total_tokens_unknown_call_count") or 0
                ),
                "attributed_token_usage_complete": attributed_calls.get("token_usage_complete") is True,
                "attributed_latency_ms": float(attributed_calls.get("latency_ms") or 0.0),
                "all_present_model_calls_verified": bool(
                    attributed_calls.get("all_present_verified")
                ),
                **_model_metrics(case_output),
            }
        def optional_delta(left: Any, right: Any) -> int | float | None:
            if (
                isinstance(left, (int, float))
                and not isinstance(left, bool)
                and isinstance(right, (int, float))
                and not isinstance(right, bool)
            ):
                return left - right
            return None

        pair["delta"] = {
            "prompt_tokens_saved": optional_delta(
                pair["baseline"]["prompt_tokens"],
                pair["experience"]["prompt_tokens"],
            ),
            "total_tokens_saved": optional_delta(
                pair["baseline"]["total_tokens"],
                pair["experience"]["total_tokens"],
            ),
            "latency_ms_saved": optional_delta(
                pair["baseline"]["latency_ms"],
                pair["experience"]["latency_ms"],
            ),
            "attributed_prompt_tokens_saved": optional_delta(
                pair["baseline"]["attributed_prompt_tokens"],
                pair["experience"]["attributed_prompt_tokens"],
            ),
            "attributed_total_tokens_saved": optional_delta(
                pair["baseline"]["attributed_total_tokens"],
                pair["experience"]["attributed_total_tokens"],
            ),
            "attributed_latency_ms_saved": optional_delta(
                pair["baseline"]["attributed_latency_ms"],
                pair["experience"]["attributed_latency_ms"],
            ),
        }
        cases.append(pair)

    cost_stats = _paired_cost_statistics(cases)
    token_usage_complete = cost_stats["token_usage_complete"]
    wins = cost_stats["token_wins"]
    losses = cost_stats["token_losses"]
    ties = cost_stats["token_ties"]
    sign_p = cost_stats["token_sign_test_p_value"]
    baseline_pass = sum(item["baseline"]["run_pass"] for item in cases) / len(cases)
    experience_pass = sum(item["experience"]["run_pass"] for item in cases) / len(cases)
    safety_ok = all(
        item["baseline"]["guards_ok"]
        and item["experience"]["guards_ok"]
        and not item["baseline"]["fallback_used"]
        and not item["experience"]["fallback_used"]
        for item in cases
    )
    checks = {
        "all_model_invocations_verified": all(
            item[arm]["all_present_model_calls_verified"]
            for item in cases
            for arm in ("baseline", "experience")
        ),
        "all_token_usage_complete": token_usage_complete,
        "baseline_run_pass_rate_100": baseline_pass == 1.0,
        "experience_run_pass_rate_100": experience_pass == 1.0,
        "no_safety_or_fallback_regression": safety_ok,
        "experience_presented_in_all_b_cases": all(
            item["experience"]["experience_presented"] for item in cases
        ),
        "paired_token_wins_at_least_5": wins >= 5,
        "paired_sign_test_p_le_0_05": sign_p <= 0.05,
        "median_total_tokens_saved_positive": cost_stats["median_total_tokens_saved_positive"],
    }
    attribution_report = {
        arm: aggregate_cases([item[arm]["attribution"] for item in cases])
        for arm in ("baseline", "experience")
    }
    report = {
        "schema_version": "sat-sim.experience-longitudinal-ab.v3",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "run_id": run_id,
        "model": model,
        "base_url": base_url,
        "max_repair_attempts": int(max_repair_attempts),
        "cost_gate_basis": "all_attributed_model_calls",
        "legacy_single_call_cost_fields_retained": True,
        "attribution": attribution_report,
        "lesson_id": lesson.lesson_id,
        "snapshot_id": snapshot["snapshot_id"],
        "pair_count": len(cases),
        "baseline_run_pass_rate": baseline_pass,
        "experience_run_pass_rate": experience_pass,
        "token_wins": wins,
        "token_losses": losses,
        "token_ties": ties,
        "token_sign_test_p_value": sign_p,
        "token_usage_complete": token_usage_complete,
        "token_pair_count": cost_stats["token_pair_count"],
        "token_unknown_pair_count": cost_stats["token_unknown_pair_count"],
        "median_total_tokens_saved": cost_stats["median_total_tokens_saved"],
        "median_latency_ms_saved": cost_stats["median_latency_ms_saved"],
        "cases": cases,
        "engineering_simulation_only": True,
        "represents_hardware_or_flight_validation": False,
    }
    write_json(output / "report.json", report)
    write_json(root / "report.json", report)
    return report


__all__ = ["DEFAULT_HELD_OUT_REQUESTS", "run_experience_longitudinal_eval"]
