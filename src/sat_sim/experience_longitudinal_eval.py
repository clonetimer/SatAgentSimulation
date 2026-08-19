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
    payload = json.loads((output_dir / "model_call.json").read_text(encoding="utf-8"))
    evidence = payload["invocation_evidence"]
    usage = evidence.get("usage") or {}
    return {
        "verified": evidence.get("verified") is True,
        "latency_ms": float(evidence.get("latency_ms") or 0.0),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "request_id": evidence.get("request_id"),
    }


def _sign_test_p_value(wins: int, losses: int) -> float:
    trials = wins + losses
    if trials == 0:
        return 1.0
    extreme = max(wins, losses)
    tail = sum(math.comb(trials, index) for index in range(extreme, trials + 1))
    return min(1.0, 2.0 * tail / (2**trials))


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
    preliminary_hash = _sha256(_canonical(preliminary))
    service.evaluate(
        lesson.lesson_id,
        scope=scope,
        evaluator="evaluator-a",
        held_out_case_ids=("preliminary-battery-nominal",),
        baseline_metrics={
            "primary_score": 1.0,
            "safety_regressions": 0,
            "physical_regressions": 0,
            "backward_regressions": 0,
        },
        candidate_metrics={
            "primary_score": 1.0,
            "safety_regressions": 0,
            "physical_regressions": 0,
            "backward_regressions": 0,
        },
        evaluation_evidence={
            "runner": preliminary["runner"],
            "case_result_sha256": preliminary_hash,
            "metrics_source_sha256": preliminary_hash,
        },
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
                **_model_metrics(case_output),
            }
        pair["delta"] = {
            "prompt_tokens_saved": (
                pair["baseline"]["prompt_tokens"] - pair["experience"]["prompt_tokens"]
            ),
            "total_tokens_saved": (
                pair["baseline"]["total_tokens"] - pair["experience"]["total_tokens"]
            ),
            "latency_ms_saved": (
                pair["baseline"]["latency_ms"] - pair["experience"]["latency_ms"]
            ),
        }
        cases.append(pair)

    token_deltas = [item["delta"]["total_tokens_saved"] for item in cases]
    latency_deltas = [item["delta"]["latency_ms_saved"] for item in cases]
    wins = sum(value > 0 for value in token_deltas)
    losses = sum(value < 0 for value in token_deltas)
    ties = sum(value == 0 for value in token_deltas)
    sign_p = _sign_test_p_value(wins, losses)
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
            item[arm]["verified"] for item in cases for arm in ("baseline", "experience")
        ),
        "baseline_run_pass_rate_100": baseline_pass == 1.0,
        "experience_run_pass_rate_100": experience_pass == 1.0,
        "no_safety_or_fallback_regression": safety_ok,
        "experience_presented_in_all_b_cases": all(
            item["experience"]["experience_presented"] for item in cases
        ),
        "paired_token_wins_at_least_5": wins >= 5,
        "paired_sign_test_p_le_0_05": sign_p <= 0.05,
        "median_total_tokens_saved_positive": statistics.median(token_deltas) > 0,
    }
    report = {
        "schema_version": "sat-sim.experience-longitudinal-ab.v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "run_id": run_id,
        "model": model,
        "base_url": base_url,
        "lesson_id": lesson.lesson_id,
        "snapshot_id": snapshot["snapshot_id"],
        "pair_count": len(cases),
        "baseline_run_pass_rate": baseline_pass,
        "experience_run_pass_rate": experience_pass,
        "token_wins": wins,
        "token_losses": losses,
        "token_ties": ties,
        "token_sign_test_p_value": sign_p,
        "median_total_tokens_saved": statistics.median(token_deltas),
        "median_latency_ms_saved": statistics.median(latency_deltas),
        "cases": cases,
        "engineering_simulation_only": True,
        "represents_hardware_or_flight_validation": False,
    }
    write_json(output / "report.json", report)
    write_json(root / "report.json", report)
    return report


__all__ = ["DEFAULT_HELD_OUT_REQUESTS", "run_experience_longitudinal_eval"]
