"""Fail-closed sealing and deterministic replay for interactive sessions."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sat_sim.run_bundle import verify_run_bundle
from sat_sim.task_spec import spec_sha256
from sat_sim.validation.physical_checks import evaluate_physical_validation
from sat_sim.validation_outcome import (
    ClaimReport,
    ExecutionStatus,
    ValidationCheck,
    ValidationOutcome,
    ValidationResult,
)

from .models import InteractiveSessionSpec, SessionState, Telecommand
from .runtime import PersistentSimulationRuntime
from .workspace import SessionWorkspace

INTERACTIVE_BUNDLE_VERSION = "interactive-run-bundle.v1"
RuntimeBuilder = Callable[[InteractiveSessionSpec], tuple[PersistentSimulationRuntime, float]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_workspace(path: str | Path) -> tuple[Path, InteractiveSessionSpec, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    root = Path(path).resolve()
    inspection, states = SessionWorkspace.inspect(root, include_rows=True)
    if not inspection.valid:
        raise ValueError(f"INTERACTIVE_WORKSPACE_INTEGRITY_FAILED:{inspection.reason_code}")
    envelope = json.loads((root / "session_spec.json").read_text(encoding="utf-8"))
    spec = InteractiveSessionSpec.model_validate(envelope["session_spec"])
    commands = SessionWorkspace.inspect_hash_chain(root / "command_events.jsonl")
    telemetry = SessionWorkspace.inspect_hash_chain(root / "telemetry.jsonl")
    return root, spec, states, commands, telemetry


def _trace_rows(telemetry: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row["frame"]["values"]) for row in telemetry if row["frame"]["stream"] == "runtime.trace"]


def semantic_trace_hash(rows: list[Mapping[str, Any]]) -> str:
    """Hash physical trace content while excluding transport metadata."""

    normalized = [
        {key: value for key, value in row.items() if key not in {"wall_time", "sequence"}}
        for row in rows
    ]
    return _sha256_bytes(_canonical(normalized))


def _acked_effect_windows(
    commands: list[dict[str, Any]], telemetry: list[dict[str, Any]], quantum_s: float,
) -> list[dict[str, Any]]:
    frames = [row["frame"] for row in telemetry]
    results: list[dict[str, Any]] = []
    for row in commands:
        ack = row.get("ack") or {}
        if ack.get("state") != "ACKED":
            continue
        applied_at = float(ack.get("sim_time_s") or 0.0)
        window_end = applied_at + quantum_s
        window = [
            frame for frame in frames
            if applied_at <= float(frame.get("sim_time_s", -1.0)) <= window_end
        ]
        results.append({
            "command_id": row["command_id"],
            "operation": row["operation"],
            "target": row["target"],
            "ack_sim_time_s": applied_at,
            "window": [applied_at, window_end],
            "frame_count": len(window),
            "streams": sorted({str(frame["stream"]) for frame in window}),
            "result": "PASS" if window else "INCONCLUSIVE",
            "reason_code": "COMMAND_EFFECT_WINDOW_OBSERVED" if window else "COMMAND_EFFECT_WINDOW_MISSING",
        })
    return results


def _validation(
    state: SessionState, trace_rows: list[dict[str, Any]], effect_windows: list[dict[str, Any]],
) -> ValidationOutcome:
    physical = evaluate_physical_validation(trace_rows, summary={}, strict=False)
    checks = [ValidationCheck(
        check_id="workspace_terminal_state", category="runtime_health",
        result=ValidationResult.PASS if state is SessionState.COMPLETED else ValidationResult.FAIL,
        reason_code="INTERACTIVE_SESSION_COMPLETED" if state is SessionState.COMPLETED else "INTERACTIVE_SESSION_NOT_COMPLETED",
        message="Interactive session reached a sealable terminal state." if state is SessionState.COMPLETED else "Only completed interactive sessions may be sealed.",
        observed=state.value,
    )]
    physical_failed = str(physical.get("status", "")).lower() == "fail"
    checks.append(ValidationCheck(
        check_id="physical_validation", category="physical_validation",
        result=ValidationResult.FAIL if physical_failed else ValidationResult.PASS,
        reason_code="PHYSICAL_VALIDATION_FAILED" if physical_failed else "PHYSICAL_VALIDATION_PASSED",
        message="Interactive trace physical constraints were evaluated.", evidence={"physical_validation": physical},
    ))
    for item in effect_windows:
        result = ValidationResult.PASS if item["result"] == "PASS" else ValidationResult.INCONCLUSIVE
        checks.append(ValidationCheck(
            check_id=f"command_effect:{item['command_id']}", category="interactive_command_effect",
            result=result, reason_code=item["reason_code"],
            message="The acknowledged command is linked to its post-application telemetry window.", evidence=item,
        ))
    results = {item.result for item in checks}
    if ValidationResult.FAIL in results:
        result, reason = ValidationResult.FAIL, next(item.reason_code for item in checks if item.result is ValidationResult.FAIL)
    elif ValidationResult.INCONCLUSIVE in results:
        result, reason = ValidationResult.INCONCLUSIVE, next(item.reason_code for item in checks if item.result is ValidationResult.INCONCLUSIVE)
    else:
        result, reason = ValidationResult.PASS, "VALIDATION_PASSED"
    return ValidationOutcome(
        execution_status=ExecutionStatus.SUCCEEDED if state is SessionState.COMPLETED else ExecutionStatus.FAILED,
        result=result, reason_code=reason, checks=checks, physical_validation=physical,
        evidence_present=["state_hash_chain", "command_hash_chain", "telemetry_hash_chain", "interactive_trace"],
        limitations=[
            "Soft-realtime internal engineering simulation only.",
            "Command effect windows establish temporal association, not hardware or flight causality.",
        ],
    )


def _claim_report(validation: ValidationOutcome, spec: InteractiveSessionSpec, trace_hash: str) -> ClaimReport:
    allowed = ["simulation_execution_completed", "interactive_evidence_hash_verified"]
    if validation.result is ValidationResult.PASS:
        allowed.append("task_requirements_satisfied")
    return ClaimReport(
        validation_result=validation.result,
        requested_claim_level="analysis_only",
        parameter_profile="engineering_simulation",
        allowed_claims=allowed,
        forbidden_claims=[
            "certified_high_fidelity", "flight_correlated", "flight_validated",
            "hardware_validated", "hard_realtime",
        ],
        limitations=list(validation.limitations),
        reason_codes=[] if validation.result is ValidationResult.PASS else [validation.reason_code],
        evidence={
            "session_id": spec.session_id,
            "capability_id": spec.capability_id,
            "task_spec_sha256": spec_sha256(spec.task_spec),
            "semantic_trace_sha256": trace_hash,
        },
    )


def seal_interactive_workspace(path: str | Path) -> dict[str, Any]:
    """Seal one completed workspace into a Run Bundle compatible artifact tree."""

    root, spec, states, commands, telemetry = _load_workspace(path)
    bundle = root / "run_bundle"
    if (bundle / "SEALED.json").exists():
        integrity = verify_run_bundle(bundle)
        if not integrity.get("ok"):
            raise ValueError("INTERACTIVE_RUN_BUNDLE_INTEGRITY_FAILED")
        return integrity
    if bundle.exists():
        raise ValueError("INTERACTIVE_PARTIAL_BUNDLE_EXISTS")
    state = SessionState(states[-1]["state"]) if states else SessionState.CREATED
    if state is not SessionState.COMPLETED:
        raise ValueError("INTERACTIVE_SESSION_NOT_COMPLETED")

    trace = _trace_rows(telemetry)
    effects = _acked_effect_windows(commands, telemetry, spec.quantum_s)
    validation = _validation(state, trace, effects)
    if validation.result is not ValidationResult.PASS:
        raise ValueError(f"INTERACTIVE_VALIDATION_NOT_PASS:{validation.reason_code}")
    trace_hash = semantic_trace_hash(trace)
    claims = _claim_report(validation, spec, trace_hash)
    created_at = spec.created_at.isoformat()
    updated_at = str(states[-1].get("wall_time") or _now())
    task_hash = spec_sha256(spec.task_spec)
    plan = {
        "schema_version": "interactive-execution-plan.v1",
        "session_id": spec.session_id,
        "capability_id": spec.capability_id,
        "quantum_s": spec.quantum_s,
        "rate": spec.rate,
        "paced": spec.paced,
        "determinism_inputs": ["task_spec", "capability_id", "quantum_s", "acked_command_log"],
    }
    plan_hash = _sha256_bytes(_canonical(plan))
    run_record = {
        "schema_version": "v25.run-record.v1",
        "run_id": spec.session_id,
        "task_id": str((spec.task_spec.get("task") or {}).get("id") or spec.session_id),
        "status": "SUCCEEDED", "created_at": created_at, "updated_at": updated_at,
        "canonical_task_spec_sha256": task_hash,
        "resolved_spec_sha256": task_hash,
        "execution_plan_sha256": plan_hash,
        "attempts": [], "execution_status": "SUCCEEDED", "validation_status": "PASS",
        "overall_status": "PASS", "validation_result": "PASS",
        "terminal_reason_code": validation.reason_code, "supersedes_run_id": None, "sealed": True,
    }
    _write_json(bundle / "input" / "task_spec.json", spec.task_spec)
    _write_json(bundle / "input" / "execution_plan.json", plan)
    _write_json(bundle / "input" / "interactive_session_spec.json", spec.model_dump(mode="json"))
    _write_json(bundle / "run_record.json", run_record)
    _write_json(bundle / "validation" / "validation_outcome.json", validation.model_dump(mode="json"))
    _write_json(bundle / "validation" / "claim_report.json", claims.model_dump(mode="json"))
    _write_json(bundle / "results" / "command_effect_windows.json", {"schema_version": "interactive-command-effects.v1", "commands": effects})
    _write_json(bundle / "results" / "semantic_trace.json", {"schema_version": "interactive-semantic-trace.v1", "sha256": trace_hash, "rows": trace})
    for name in ("session_spec.json", "state_events.jsonl", "command_events.jsonl", "telemetry.jsonl", "telemetry_manifest.json"):
        source = root / name
        if source.exists():
            target = bundle / "interactive" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    files = {
        str(item.relative_to(bundle)).replace(os.sep, "/"): {"sha256": _sha256_file(item), "size_bytes": item.stat().st_size}
        for item in sorted(bundle.rglob("*")) if item.is_file()
    }
    manifest = {
        "schema_version": INTERACTIVE_BUNDLE_VERSION, "run_id": spec.session_id,
        "task_id": run_record["task_id"], "status": "SUCCEEDED", "execution_status": "SUCCEEDED",
        "validation_status": "PASS", "overall_status": "PASS", "validation_result": "PASS",
        "execution_plan_sha256": plan_hash, "sealed_at": _now(), "files": files,
    }
    _write_json(bundle / "bundle_manifest.json", manifest)
    _write_json(bundle / "SEALED.json", {
        "schema_version": INTERACTIVE_BUNDLE_VERSION, "run_id": spec.session_id,
        "sealed_at": manifest["sealed_at"], "bundle_manifest_sha256": _sha256_file(bundle / "bundle_manifest.json"),
        "immutability_policy": "sealed interactive evidence is immutable; recovery creates a new epoch",
    })
    integrity = verify_run_bundle(bundle)
    if not integrity.get("ok"):
        raise ValueError("INTERACTIVE_RUN_BUNDLE_INTEGRITY_FAILED")
    return integrity


def replay_interactive_workspace(path: str | Path, *, runtime_builder: RuntimeBuilder | None = None) -> dict[str, Any]:
    """Replay an interrupted or completed workspace and compare semantic traces."""

    root, spec, states, commands, telemetry = _load_workspace(path)
    state = SessionState(states[-1]["state"]) if states else SessionState.CREATED
    if state not in {SessionState.INTERRUPTED, SessionState.COMPLETED}:
        raise ValueError("INTERACTIVE_SESSION_NOT_REPLAYABLE")
    for row in commands:
        if "command" not in row:
            raise ValueError("INTERACTIVE_COMMAND_PAYLOAD_MISSING")
    if runtime_builder is None:
        from .runtime_factory import build_persistent_runtime
        runtime_builder = build_persistent_runtime
    runtime, _ = runtime_builder(spec)
    original_rows = _trace_rows(telemetry)
    final_sim_time = float(states[-1].get("sim_time_s") or 0.0)
    executing: dict[float, list[Telecommand]] = {}
    seen: set[str] = set()
    for row in commands:
        ack = row.get("ack") or {}
        if ack.get("state") != "EXECUTING" or row["command_id"] in seen:
            continue
        seen.add(row["command_id"])
        at = float(ack.get("sim_time_s") or 0.0)
        executing.setdefault(at, []).append(Telecommand.model_validate(row["command"]))
    replay_rows: list[dict[str, Any]] = []
    runtime.prepare()
    try:
        while runtime.current_time_s < final_sim_time - 1e-12:
            current = runtime.current_time_s
            for command in executing.get(current, ()): 
                runtime.apply_command(command)
            target = min(current + spec.quantum_s, final_sim_time)
            replay_rows.extend(dict(item) for item in runtime.advance_to(target))
        runtime.finalize()
    except BaseException:
        runtime.abort()
        raise
    original_hash = semantic_trace_hash(original_rows)
    replay_hash = semantic_trace_hash(replay_rows)
    if original_hash != replay_hash:
        raise ValueError("INTERACTIVE_REPLAY_MISMATCH")
    existing = sorted(root.glob("recovery_epoch-*.json"))
    epoch = len(existing) + 1
    payload = {
        "schema_version": "interactive-recovery-epoch.v1", "session_id": spec.session_id,
        "epoch": epoch, "source_state": state.value, "verified_at": _now(),
        "semantic_trace_sha256": replay_hash, "command_count": len(seen), "result": "PASS",
    }
    _write_json(root / f"recovery_epoch-{epoch}.json", payload)
    return payload


__all__ = ["replay_interactive_workspace", "seal_interactive_workspace", "semantic_trace_hash"]
