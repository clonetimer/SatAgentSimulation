"""Reliability and security acceptance for controlled experience reuse."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .durable_queue import DurableExecutionQueue
from .execution_worker import ExecutionQueueWorker
from .run_bundle import AttemptStatus, ExecutionAttempt, RunRecord, RunStatus, prepare_run
from .task_spec import spec_sha256, write_json
from .task_spec import load_task_spec
from .unified_agent import UnifiedAgentRequest, _experience_advisory_payload, run_unified_agent
from .verification_matrix import build_verification_matrix, run_verification_item


def _load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _model_metrics(output: Path) -> dict[str, Any]:
    call = _load(output / "model_call.json")
    evidence = call.get("invocation_evidence") or {}
    metadata = call.get("metadata") or {}
    return {
        "ok": call.get("ok") is True,
        "verified": evidence.get("verified") is True,
        "seed": metadata.get("seed"),
        "structured_output": metadata.get("structured_output"),
        "raw_response_sha256": evidence.get("response_sha256"),
        "error": call.get("error"),
    }


def _agent_request(
    *,
    request_text: str,
    output_dir: Path,
    base_url: str,
    model: str,
    store_root: Path | None = None,
) -> UnifiedAgentRequest:
    enabled = store_root is not None
    return UnifiedAgentRequest(
        input_kind="natural_language",
        request_text=request_text,
        output_dir=output_dir,
        backend="vllm",
        local_backend="template",
        remote_backend="vllm",
        model_name=model,
        model_base_url=base_url,
        model_timeout_s=30.0,
        model_temperature=0.0,
        model_seed=0,
        model_max_output_tokens=2048,
        model_structured_output="json_schema",
        compile_if_valid=True,
        experience_reuse_enabled=enabled,
        experience_store_root=store_root,
        experience_tenant_id="internal" if enabled else None,
        experience_project_id="longitudinal-eval" if enabled else None,
    )


def _fixed_snapshot_repeat(
    *,
    store_root: Path,
    output: Path,
    base_url: str,
    model: str,
) -> dict[str, Any]:
    request_text = (
        "capability_id=component.battery.v1, mode=nominal. "
        "Simulate 20 seconds with a 2 second sample interval."
    )
    results = []
    for sequence in (1, 2):
        case_output = output / f"repeat_{sequence}"
        result = run_unified_agent(
            _agent_request(
                request_text=request_text,
                output_dir=case_output,
                base_url=base_url,
                model=model,
                store_root=store_root,
            )
        )
        advisory = _load(case_output / "experience_advisory.json")
        metrics = _model_metrics(case_output)
        fallback = any(
            "fallback" in step.name
            for step in (result.facade_result.steps if result.facade_result else ())
        )
        results.append(
            {
                "ok": result.ok,
                "fallback_used": fallback,
                "task_spec_sha256": spec_sha256(result.task_spec),
                "lesson_ids": advisory.get("lesson_ids") or [],
                **metrics,
            }
        )
    checks = {
        "both_runs_valid": all(item["ok"] for item in results),
        "same_snapshot_lessons": bool(
            results[0]["lesson_ids"]
            and results[0]["lesson_ids"] == results[1]["lesson_ids"]
        ),
        "same_semantic_taskspec": (
            results[0]["task_spec_sha256"] == results[1]["task_spec_sha256"]
        ),
        "same_raw_model_response": (
            results[0]["raw_response_sha256"] == results[1]["raw_response_sha256"]
        ),
        "frozen_model_controls": all(
            item["verified"]
            and item["seed"] == 0
            and item["structured_output"] == "json_schema"
            and not item["fallback_used"]
            for item in results
        ),
    }
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "runs": results}


def _scope_isolation(store_root: Path, output: Path) -> dict[str, Any]:
    requests = {
        "nominal": "Generate a nominal battery engineering simulation.",
        "fault": "Simulate a battery open circuit fault.",
        "degradation": "执行电池容量衰减退化仿真。",
        "unrelated": "Generate a nominal solar panel engineering simulation.",
    }
    evidence = {}
    for name, text in requests.items():
        request = _agent_request(
            request_text=text,
            output_dir=output / name,
            base_url="http://127.0.0.1:9/v1",
            model="scope-only",
            store_root=store_root,
        )
        payload = _experience_advisory_payload(request)
        evidence[name] = {
            "mode": payload.get("mode"),
            "capability_id": payload.get("capability_id"),
            "lesson_ids": payload.get("lesson_ids") or [],
        }
    checks = {
        "nominal_lesson_matches": bool(evidence["nominal"]["lesson_ids"]),
        "fault_not_polluted_by_nominal_lesson": not evidence["fault"]["lesson_ids"],
        "degradation_not_polluted_by_nominal_lesson": not evidence["degradation"]["lesson_ids"],
        "unrelated_capability_not_polluted": not evidence["unrelated"]["lesson_ids"],
    }
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "cases": evidence}


def _vllm_interruption_recovery(output: Path, model: str) -> dict[str, Any]:
    result = run_unified_agent(
        _agent_request(
            request_text=(
                "capability_id=component.battery.v1, mode=nominal. "
                "Generate a 10 second engineering simulation."
            ),
            output_dir=output,
            base_url="http://127.0.0.1:9/v1",
            model=model,
        )
    )
    metrics = _model_metrics(output)
    fallback = any(
        "fallback" in step.name
        for step in (result.facade_result.steps if result.facade_result else ())
    )
    checks = {
        "injected_outage_observed": not metrics["ok"] and bool(metrics["error"]),
        "deterministic_recovery_explicit": fallback,
        "recovered_taskspec_valid": result.ok and result.validation.ok and result.guards.ok,
        "outage_not_claimed_as_verified_model_call": not metrics["verified"],
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "expected_fallback_in_fault_injection": True,
        "checks": checks,
        "model_call": metrics,
    }


def _worker_interruption_recovery(project_root: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    spec = load_task_spec(
        project_root / "examples" / "subsystem_thermal_source_native_nominal.yaml"
    ).data
    prepared = prepare_run(
        spec,
        output_root=output / "runs",
        run_id="g6_worker_restart",
    )
    bundle_root = Path(prepared.bundle_root)
    queue = DurableExecutionQueue(output / "queue.sqlite3")
    job = queue.enqueue(
        run_id=prepared.run_id,
        bundle_root=bundle_root,
        execution_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=2,
        hard_timeout=True,
    )
    claimed = queue.claim_next(worker_id="worker-interrupted", lease_seconds=0.01)
    if claimed is None:
        raise RuntimeError("worker interruption probe could not claim queued job")
    queue.mark_running(
        job.job_id,
        worker_id="worker-interrupted",
        pid=999_999_999,
        lease_seconds=0.01,
    )

    record_path = bundle_root / "run_record.json"
    record = RunRecord.model_validate(_load(record_path))
    started_at = datetime.now(timezone.utc).isoformat()
    attempt = ExecutionAttempt(
        attempt_id="attempt_001",
        sequence=1,
        status=AttemptStatus.RUNNING,
        started_at=started_at,
        plan_sha256=prepared.execution_plan_sha256,
    )
    attempt_root = bundle_root / "attempts" / attempt.attempt_id
    attempt_root.mkdir(parents=True, exist_ok=True)
    write_json(attempt_root / "attempt.json", attempt.model_dump(mode="json"))
    interrupted = record.model_copy(
        update={
            "status": RunStatus.RUNNING,
            "updated_at": started_at,
            "attempts": [attempt],
        }
    )
    write_json(record_path, interrupted.model_dump(mode="json"))

    recovered_count = queue.requeue_stale(stale_before="9999-12-31T23:59:59+00:00")
    after_requeue = queue.get(job.job_id)
    recovered_record = RunRecord.model_validate(_load(record_path))
    worker = ExecutionQueueWorker(
        queue,
        worker_id="worker-restarted",
        source_root=project_root / "src",
        poll_interval_s=0.05,
        heartbeat_interval_s=0.1,
        lease_seconds=5.0,
    )
    completed = worker.run_forever(max_jobs=1, idle_timeout_s=30.0)
    final = queue.get(job.job_id)
    final_record = RunRecord.model_validate(_load(record_path))
    validation = _load(bundle_root / "validation" / "validation_outcome.json")
    events = queue.events(job.job_id)
    checks = {
        "stale_lease_requeued": (
            recovered_count == 1
            and after_requeue is not None
            and after_requeue.state == "QUEUED"
        ),
        "interrupted_attempt_preserved": (
            recovered_record.status == RunStatus.PREPARED
            and len(recovered_record.attempts) == 1
            and recovered_record.attempts[0].status == AttemptStatus.ABORTED
        ),
        "replacement_worker_completed": (
            completed == 1
            and final is not None
            and final.state == "SUCCEEDED"
            and final.validation_result == "PASS"
        ),
        "bundle_sealed_after_restart": (
            final_record.status == RunStatus.SUCCEEDED
            and validation.get("result") == "PASS"
            and (bundle_root / "SEALED.json").is_file()
        ),
        "no_execution_lock_or_active_job": (
            not (bundle_root / "runtime" / "execution.lock").exists()
            and final is not None
            and final.state not in {"CLAIMED", "RUNNING", "CANCEL_REQUESTED"}
        ),
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "job": final.to_dict() if final else None,
        "events": events,
        "bundle_root": str(bundle_root),
    }


def _determinism_matrix(project_root: Path, output: Path) -> dict[str, Any]:
    selected_types = {"determinism", "repeat_stability", "golden_repeat5"}
    items = [
        item
        for item in build_verification_matrix(project_root=project_root)
        if item.scenario_type in selected_types
    ]
    results = []
    output.mkdir(parents=True, exist_ok=True)
    for item in items:
        report = run_verification_item(item, project_root=project_root, execute_shell=False)
        write_json(output / f"{item.test_id}.json", report)
        results.append(
            {
                "test_id": item.test_id,
                "level": item.level,
                "object": item.object,
                "scenario_type": item.scenario_type,
                "status": report["status"],
                "reason": report["reason"],
            }
        )
    passed = sum(item["status"] == "PASS" for item in results)
    return {
        "status": "PASS" if passed == len(results) and results else "FAIL",
        "item_count": len(results),
        "pass_count": passed,
        "fail_count": len(results) - passed,
        "results": results,
    }


def _security_matrix(report: dict[str, Any]) -> dict[str, Any]:
    source = report.get("checks") or {}
    checks = {
        "experience_poisoning_fail_closed": all(
            source.get(name) is True
            for name in (
                "unsealed_source_rejected",
                "unknown_capability_rejected",
                "revoked_source_rejected",
            )
        ),
        "prompt_and_feedback_injection_not_copied": (
            source.get("candidate_hash_and_no_prompt_copy") is True
        ),
        "privilege_escalation_fail_closed": all(
            source.get(name) is True
            for name in (
                "compiler_self_approval_rejected",
                "evaluator_self_approval_rejected",
                "duplicate_reviewer_rejected",
                "cross_scope_isolation",
            )
        ),
        "hash_tamper_fail_closed": source.get("lesson_tamper_rejected") is True,
        "revocation_and_rollback_effective": all(
            source.get(name) is True
            for name in (
                "one_click_disable",
                "snapshot_rollback",
                "revoked_lesson_not_retrieved",
            )
        ),
    }
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}


def run_reliability_acceptance(
    *,
    project_root: str | Path,
    longitudinal_report: str | Path,
    capacity_report: str | Path,
    controlled_experience_report: str | Path,
    output_dir: str | Path,
    base_url: str = "http://127.0.0.1:8000/v1",
    model: str = "Qwen3.5-9B",
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    longitudinal_path = Path(longitudinal_report)
    capacity_path = Path(capacity_report)
    security_path = Path(controlled_experience_report)
    longitudinal = _load(longitudinal_path)
    capacity = _load(capacity_path)
    controlled = _load(security_path)
    store_root = (
        longitudinal_path.parent
        / "evidence"
        / str(longitudinal["run_id"])
        / "store"
    )
    sections = {
        "longitudinal": {
            "status": longitudinal.get("status"),
            "report_sha256": _sha256_file(longitudinal_path),
            "token_wins": longitudinal.get("token_wins"),
            "sign_test_p_value": longitudinal.get("token_sign_test_p_value"),
        },
        "capacity": {
            "status": capacity.get("status"),
            "report_sha256": _sha256_file(capacity_path),
            "summary": capacity.get("summary"),
            "tested_envelope": capacity.get("tested_envelope"),
        },
        "security": _security_matrix(controlled),
        "fixed_snapshot_repeat": _fixed_snapshot_repeat(
            store_root=store_root,
            output=output / "fixed_snapshot",
            base_url=base_url,
            model=model,
        ),
        "experience_scope_isolation": _scope_isolation(
            store_root,
            output / "scope_isolation",
        ),
        "vllm_interruption_recovery": _vllm_interruption_recovery(
            output / "vllm_interruption",
            model,
        ),
        "worker_interruption_recovery": _worker_interruption_recovery(
            root,
            output / "worker_interruption",
        ),
        "determinism_matrix": _determinism_matrix(
            root,
            output / "determinism_matrix",
        ),
    }
    checks = {
        "longitudinal_gain": sections["longitudinal"]["status"] == "PASS",
        "capacity_envelope": sections["capacity"]["status"] == "PASS",
        "security_attack_matrix": sections["security"]["status"] == "PASS",
        "fixed_snapshot_reproducible": sections["fixed_snapshot_repeat"]["status"] == "PASS",
        "irrelevant_experience_isolated": sections["experience_scope_isolation"]["status"] == "PASS",
        "vllm_interruption_recovered": sections["vllm_interruption_recovery"]["status"] == "PASS",
        "worker_interruption_recovered": (
            sections["worker_interruption_recovery"]["status"] == "PASS"
        ),
        "all_object_determinism": sections["determinism_matrix"]["status"] == "PASS",
    }
    report = {
        "schema_version": "sat-sim.g6-reliability-acceptance.v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "sections": sections,
        "engineering_simulation_only": True,
        "represents_hardware_or_flight_validation": False,
    }
    write_json(output / "report.json", report)
    return report


__all__ = ["run_reliability_acceptance"]
