"""Memory-bounded Run Bundle sealing for continuous interactive telemetry.

V1 artifact contracts and semantic trace hashing remain identical to the
finite-session format.  Physical checks run incrementally with bounded issue
reporting; command-effect index memory scales with ACKED commands, not TM frames.
This is not a native Basilisk checkpoint or a streaming replay mechanism.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import deque
from pathlib import Path
from typing import Any

from sat_sim.run_bundle import verify_run_bundle
from sat_sim.task_spec import spec_sha256
from sat_sim.validation.physical_checks import PhysicalValidationAccumulator, physical_validation_options
from sat_sim.validation_outcome import ValidationResult

from .models import InteractiveSessionSpec, SessionState
from .workspace import SessionWorkspace
from . import telemetry_segments

class _StreamingPhysicalChecks(PhysicalValidationAccumulator):
    """Compatibility name for the shared memory-bounded physical validator."""


def _effect_records(commands: list[dict[str, Any]], quantum_s: float) -> list[dict[str, Any]]:
    effects = []
    for row in commands:
        ack = row.get("ack") or {}
        if ack.get("state") != "ACKED":
            continue
        start = float(ack.get("sim_time_s") or 0.0)
        effects.append({
            "command_id": row["command_id"],
            "operation": row["operation"], "target": row["target"],
            "ack_sim_time_s": start, "window": [start, start + quantum_s],
            "frame_count": 0, "streams": set(),
        })
    return effects


def _write_semantic_trace_and_effects(
    telemetry_path: Path, trace_path: Path, commands: list[dict[str, Any]], quantum_s: float,
    *, task_spec: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    from .evidence import _canonical

    checks = _StreamingPhysicalChecks(**physical_validation_options(task_spec))
    effects = _effect_records(commands, quantum_s)
    sorted_indices = sorted(range(len(effects)), key=lambda i: effects[i]["window"][0])
    next_index = 0
    active: deque[int] = deque()
    last_time = float("-inf")
    digest = hashlib.sha256()
    digest.update(b"[")
    first = True
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("wb") as handle:
        handle.write(b'{"rows":[')
        for event in SessionWorkspace.iter_telemetry(telemetry_path.parent):
            frame = event["frame"]
            at = float(frame["sim_time_s"])
            if at < last_time:
                raise ValueError("CONTINUOUS_TELEMETRY_OUT_OF_ORDER")
            last_time = at
            while next_index < len(sorted_indices):
                idx = sorted_indices[next_index]
                if effects[idx]["window"][0] > at:
                    break
                active.append(idx)
                next_index += 1
            if active:
                active = deque(idx for idx in active if effects[idx]["window"][1] >= at)
                for idx in active:
                    effects[idx]["frame_count"] += 1
                    effects[idx]["streams"].add(str(frame["stream"]))
            if frame["stream"] != "runtime.trace":
                continue
            row = dict(frame["values"])
            checks.add(row)
            encoded = _canonical(row)
            semantic = _canonical({k: value for k, value in row.items() if k not in {"wall_time", "sequence"}})
            if not first:
                handle.write(b",")
                digest.update(b",")
            first = False
            handle.write(encoded)
            digest.update(semantic)
        digest.update(b"]")
        result_hash = digest.hexdigest()
        handle.write(
            b'],"schema_version":"interactive-semantic-trace.v1","sha256":'
            + json.dumps(result_hash).encode("ascii") + b"}"
        )
    for item in effects:
        item["streams"] = sorted(item["streams"])
        found = item["frame_count"] > 0
        item["result"] = "PASS" if found else "INCONCLUSIVE"
        item["reason_code"] = (
            "COMMAND_EFFECT_WINDOW_OBSERVED" if found else "COMMAND_EFFECT_WINDOW_MISSING"
        )
    return result_hash, checks.result(), effects


def seal_continuous_workspace(path: str | Path) -> dict[str, Any]:
    """Seal a continuous session with O(stream count + ACK count) memory."""

    from .evidence import (
        INTERACTIVE_BUNDLE_VERSION, _now, _canonical, _sha256_bytes,
        _sha256_file, _write_json, _claim_report, _validation, _runtime_summary,
    )

    root = Path(path).resolve()
    inspection, states = SessionWorkspace.inspect(root, include_rows=True)
    if not inspection.valid:
        raise ValueError(f"INTERACTIVE_WORKSPACE_INTEGRITY_FAILED:{inspection.reason_code}")
    envelope = json.loads((root / "session_spec.json").read_text(encoding="utf-8"))
    spec = InteractiveSessionSpec.model_validate(envelope["session_spec"])
    if not spec.continuous:
        raise ValueError("CONTINUOUS_SEAL_REQUIRES_CONTINUOUS_SPEC")
    bundle = root / "run_bundle"
    # Never expose an unverified bundle at its published path.
    # A crashed/failed staging tree stays in place for forensic inspection;
    # do not silently overwrite it or accept it as a seal.
    staging = root / ".run_bundle.pending"
    if (bundle / "SEALED.json").exists():
        result = verify_run_bundle(bundle)
        if not result.get("ok"):
            raise ValueError("INTERACTIVE_RUN_BUNDLE_INTEGRITY_FAILED")
        return result
    if bundle.exists():
        raise ValueError("INTERACTIVE_PARTIAL_BUNDLE_EXISTS")
    if staging.exists():
        raise ValueError("INTERACTIVE_PARTIAL_STAGING_EXISTS")
    state = SessionState(states[-1]["state"]) if states else SessionState.CREATED
    if state is not SessionState.COMPLETED:
        raise ValueError("INTERACTIVE_SESSION_NOT_COMPLETED")
    commands = SessionWorkspace.inspect_hash_chain(root / "command_events.jsonl")
    trace_path = staging / "results" / "semantic_trace.json"
    trace_hash, physical, effects = _write_semantic_trace_and_effects(
        root / "telemetry.jsonl", trace_path, commands, spec.quantum_s, task_spec=spec.task_spec
    )
    validation = _validation(state, [], effects, physical_override=physical, task_spec=spec.task_spec,
        runtime_summary=_runtime_summary(root))
    _write_json(root / "validation_outcome.json", validation.model_dump(mode="json"))
    if validation.result is not ValidationResult.PASS:
        raise ValueError(f"INTERACTIVE_VALIDATION_NOT_PASS:{validation.reason_code}")
    claims = _claim_report(validation, spec, trace_hash)
    task_hash = spec_sha256(spec.task_spec)
    plan = {
        "schema_version": "interactive-execution-plan.v1",
        "session_id": spec.session_id, "capability_id": spec.capability_id,
        "quantum_s": spec.quantum_s, "rate": spec.rate, "paced": spec.paced,
        "determinism_inputs": ["task_spec", "capability_id", "quantum_s", "acked_command_log"],
    }
    plan_hash = _sha256_bytes(_canonical(plan))
    run_record = {
        "schema_version": "v25.run-record.v1", "run_id": spec.session_id,
        "task_id": str((spec.task_spec.get("task") or {}).get("id") or spec.session_id),
        "status": "SUCCEEDED",
        "created_at": spec.created_at.isoformat(),
        "updated_at": str(states[-1].get("wall_time") or _now()),
        "canonical_task_spec_sha256": task_hash,
        "resolved_spec_sha256": task_hash, "execution_plan_sha256": plan_hash,
        "attempts": [], "execution_status": "SUCCEEDED", "validation_status": "PASS",
        "overall_status": "PASS", "validation_result": "PASS",
        "terminal_reason_code": validation.reason_code, "supersedes_run_id": None,
        "sealed": True,
    }
    _write_json(staging / "input" / "task_spec.json", spec.task_spec)
    _write_json(staging / "input" / "execution_plan.json", plan)
    _write_json(staging / "input" / "interactive_session_spec.json", spec.model_dump(mode="json"))
    _write_json(staging / "run_record.json", run_record)
    _write_json(staging / "validation" / "validation_outcome.json", validation.model_dump(mode="json"))
    _write_json(staging / "validation" / "claim_report.json", claims.model_dump(mode="json"))
    _write_json(staging / "results" / "command_effect_windows.json", {
        "schema_version": "interactive-command-effects.v1", "commands": effects,
    })
    for name in (
        "session_spec.json", "state_events.jsonl", "command_events.jsonl", "runtime_result.json",
        "telemetry.jsonl", "telemetry_manifest.json",
    ):
        source = root / name
        if source.exists() or (name == "telemetry.jsonl" and telemetry_segments.files(root)):
            target = staging / "interactive" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if name == "telemetry.jsonl":
                telemetry_segments.copy_logical(root, target)
            else:
                shutil.copy2(source, target)
    files = {
        str(item.relative_to(staging)).replace(os.sep, "/"): {
            "sha256": _sha256_file(item), "size_bytes": item.stat().st_size,
        }
        for item in sorted(staging.rglob("*")) if item.is_file()
    }
    manifest = {
        "schema_version": INTERACTIVE_BUNDLE_VERSION, "run_id": spec.session_id,
        "task_id": run_record["task_id"],
        "status": "SUCCEEDED", "execution_status": "SUCCEEDED",
        "validation_status": "PASS", "overall_status": "PASS",
        "validation_result": "PASS", "execution_plan_sha256": plan_hash,
        "sealed_at": _now(), "files": files,
    }
    _write_json(staging / "bundle_manifest.json", manifest)
    _write_json(staging / "SEALED.json", {
        "schema_version": INTERACTIVE_BUNDLE_VERSION, "run_id": spec.session_id,
        "sealed_at": manifest["sealed_at"],
        "bundle_manifest_sha256": _sha256_file(staging / "bundle_manifest.json"),
        "immutability_policy": "sealed interactive evidence is immutable; recovery creates a new epoch",
    })
    result = verify_run_bundle(staging)
    if not result.get("ok"):
        raise ValueError("INTERACTIVE_RUN_BUNDLE_INTEGRITY_FAILED")
    if bundle.exists():
        raise ValueError("INTERACTIVE_PARTIAL_BUNDLE_EXISTS")
    staging.rename(bundle)
    return result
