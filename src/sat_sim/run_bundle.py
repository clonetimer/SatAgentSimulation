"""V25 deterministic Plan/Execute runtime and immutable Run Bundle.

A run is prepared from a validated V24 plan and executed only when the caller
commits the exact plan SHA-256.  Execution attempts are append-only records;
infrastructure retries never rewrite TaskSpec semantics or mission criteria.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic

import hashlib
from fnmatch import fnmatchcase
import json
import multiprocessing as mp
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import tempfile
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Mapping, Sequence

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .capability_registry import get_capability
from .assertions import evaluate_assertions
from .reporting import generate_run_report
from .dataset_writer import write_jsonl, write_task_dataset, write_trace_csv
from .form_schema import output_hierarchy, output_label
from .execution_planner import (
    ExecutionPlan,
    ResolvedSpec,
    attach_plan_metadata,
    plan_task_spec,
    validate_execution_plan,
)
from .task_compiler import CompiledTask, compile_task_spec
from .task_models import canonicalize_task_spec
from .task_runner import TaskRunResult
from .unified_execution import adapter_key_for_compiled, execute_compiled_task
from .task_spec import write_json
from utils.runtime_diagnostics import clear_runtime_diagnostics, runtime_diagnostics_report

from .validation_outcome import (
    ClaimReport,
    ExecutionStatus,
    ValidationOutcome,
    ValidationResult,
    build_claim_report,
    evaluate_validation_outcome,
)

RUN_BUNDLE_VERSION = "v25.run-bundle.v1"
RUN_RECORD_VERSION = "v25.run-record.v1"
ATTEMPT_RECORD_VERSION = "v25.execution-attempt.v1"


class RunStatus(StrEnum):
    PREPARED = "PREPARED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AttemptStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    ABORTED = "ABORTED"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PreparedRun(_Strict):
    schema_version: str = RUN_BUNDLE_VERSION
    run_id: str
    task_id: str
    bundle_root: str
    canonical_task_spec_sha256: str
    resolved_spec_sha256: str
    execution_plan_sha256: str
    primary_capability_id: str
    prepared_at: str
    status: RunStatus = RunStatus.PREPARED
    commit_required: bool = True


class ExecutionFailure(_Strict):
    reason_code: str
    failure_class: str
    message: str
    retryable: bool
    exception_type: str | None = None
    traceback_file: str | None = None


class ExecutionAttempt(_Strict):
    schema_version: str = ATTEMPT_RECORD_VERSION
    attempt_id: str
    sequence: int = Field(ge=1)
    status: AttemptStatus
    started_at: str
    finished_at: str | None = None
    elapsed_seconds: float | None = Field(default=None, ge=0)
    plan_sha256: str
    failure: ExecutionFailure | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)


class PlanExecutionEvent(_Strict):
    sequence: int = Field(ge=1)
    node_id: str
    status: str
    condition: str | None = None
    next_node: str | None = None
    timestamp: str
    details: dict[str, Any] = Field(default_factory=dict)


class RunRecord(_Strict):
    schema_version: str = RUN_RECORD_VERSION
    run_id: str
    task_id: str
    status: RunStatus
    created_at: str
    updated_at: str
    canonical_task_spec_sha256: str
    resolved_spec_sha256: str
    execution_plan_sha256: str
    attempts: list[ExecutionAttempt] = Field(default_factory=list)
    # ``status`` remains the execution lifecycle state for backward
    # compatibility.  These explicit fields prevent callers from conflating a
    # completed process with a validated mission result.
    execution_status: ExecutionStatus | None = None
    validation_status: ValidationResult | None = None
    overall_status: ValidationResult | None = None
    validation_result: ValidationResult | None = None  # legacy alias
    terminal_reason_code: str | None = None
    supersedes_run_id: str | None = None
    sealed: bool = False


class DatasetArtifact(_Strict):
    output_root: str
    files: dict[str, str] = Field(default_factory=dict)
    manifest: dict[str, Any] = Field(default_factory=dict)


class RunExecutionResult(_Strict):
    run_record: RunRecord
    validation: ValidationOutcome
    claim_report: ClaimReport
    bundle_root: str
    bundle_manifest: dict[str, Any]
    summary: dict[str, Any] = Field(default_factory=dict)
    trace_rows: list[dict[str, Any]] = Field(default_factory=list)
    dataset: DatasetArtifact | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_id(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-")
    return text or "run"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_yaml(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(dict(payload), sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def _write_record(path: Path, record: BaseModel) -> None:
    write_json(path, record.model_dump(mode="json"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_files(root: Path) -> list[Path]:
    excluded = {"bundle_manifest.json", "SEALED.json", "execution.lock"}
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.name not in excluded
    )


def _dependency_versions() -> dict[str, Any]:
    versions: dict[str, Any] = {"python": platform.python_version()}
    for distribution in ("satellite-simulation-platform", "pydantic", "PyYAML", "numpy", "bsk", "fastapi", "uvicorn"):
        try:
            from importlib.metadata import version

            versions[distribution] = version(distribution)
        except Exception:
            versions[distribution] = None
    try:
        import Basilisk

        versions["Basilisk"] = getattr(Basilisk, "__version__", None) or versions.get("bsk")
    except Exception:
        versions["Basilisk"] = versions.get("bsk")
    if not versions.get("satellite-simulation-platform"):
        try:
            from sat_sim import __version__
            versions["satellite-simulation-platform"] = __version__
        except Exception:
            versions["satellite-simulation-platform"] = None
    return versions


def _runtime_environment(resolved: ResolvedSpec) -> dict[str, Any]:
    return {
        "schema_version": "v37d.runtime-environment.v1",
        "captured_at": _now(),
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "platform": platform.platform(),
        },
        "process": {"pid": os.getpid(), "cwd": str(Path.cwd())},
        "primary_capability_id": resolved.primary_capability_id,
        "requested_level": resolved.requested_level,
        "resolved_level": resolved.resolved_level,
        "assurance": resolved.assurance.model_dump(mode="json"),
    }


def _scalar_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "item"):
        try:
            item = value.item()
            if item is None or isinstance(item, (str, int, float, bool)):
                return item
        except Exception:
            return None
    return None


def _metrics_payload(summary: Mapping[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}

    def visit(value: Any, prefix: str) -> None:
        scalar = _scalar_value(value)
        if scalar is not None:
            if prefix:
                metrics[prefix] = scalar
            return
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_key = f"{prefix}.{key}" if prefix else str(key)
                visit(child, child_key)

    visit(summary, "")
    return {"schema_version": "sat-sim.metrics.v2", "metric_count": len(metrics), "metrics": metrics}


def _events_payload(spec: Mapping[str, Any], summary: Mapping[str, Any]) -> dict[str, Any]:
    events = spec.get("events") if isinstance(spec.get("events"), Mapping) else {}
    declared: list[dict[str, Any]] = []
    for kind, key in (("fault", "faults"), ("degradation", "degradations"), ("constraint", "constraints")):
        raw = events.get(key) if isinstance(events.get(key), Sequence) else []
        for item in raw:
            if isinstance(item, Mapping):
                declared.append({"kind": kind, **dict(item)})
    observed_keys = (
        "runtime_fault_registered_count",
        "runtime_fault_triggered_count",
        "runtime_fault_recovery_triggered_count",
        "runtime_fault_event_status",
        "degradation_execution",
        "constraint_count",
        "constraint_active_sample_count",
        "speed_limit_hit_sample_count",
        "speed_limit_active_sample_count",  # legacy field
    )
    observed = {key: _scalar_value(summary.get(key)) for key in observed_keys if key in summary}
    adapter_metadata = summary.get("adapter_metadata") if isinstance(summary.get("adapter_metadata"), Mapping) else {}
    fault_environment = (
        adapter_metadata.get("fault_environment")
        if isinstance(adapter_metadata.get("fault_environment"), Mapping)
        else {}
    )
    return {
        "schema_version": "v37e.events.v2",
        "declared_count": len(declared),
        "declared": declared,
        "observed": observed,
        "fault_environment": dict(fault_environment),
        "episode_count": int(fault_environment.get("episode_count", 0) or 0),
        "episodes": list(fault_environment.get("episodes") or []),
    }


def _plot_manifest_payload(spec: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    outputs = spec.get("outputs") if isinstance(spec.get("outputs"), Mapping) else {}
    requested = [str(item) for item in list(outputs.get("plots") or []) if str(item).strip()]
    available = list(rows[0].keys()) if rows else []
    time_field = "time_s" if "time_s" in available else "t_s" if "t_s" in available else None
    excluded = {"time_s", "t_s", "times", "sample_index", "task_id", "case_id", "utc"}

    selected: list[str] = []
    for pattern in requested:
        if pattern in {"time_s", "t_s", "times"}:
            continue
        matches = [name for name in available if fnmatchcase(name, pattern)] if any(ch in pattern for ch in "*?[") else [pattern]
        for name in matches:
            if name in excluded or name not in available or name in selected:
                continue
            if rows and not any(isinstance(row.get(name), (int, float)) and not isinstance(row.get(name), bool) for row in rows):
                continue
            selected.append(name)

    return {
        "schema_version": "sat-sim.plot-manifest.v2",
        "source": "results/telemetry.csv",
        "selection_policy": "strict_requested_plots",
        "requested_plots": requested,
        "time_field": time_field,
        "time_axis": {"field": time_field, "label": "仿真时间", "unit": "s"} if time_field else None,
        "series": [
            {
                "field": name,
                "label": output_label(name),
                "render": "line",
                **output_hierarchy(name),
            }
            for name in selected
        ],
        "excluded_metadata_fields": sorted(excluded),
        "static_images_generated": False,
    }


def _artifact_index_payload(root: Path) -> dict[str, Any]:
    artifacts = []
    for path in _relative_files(root):
        relative = str(path.relative_to(root)).replace(os.sep, "/")
        if relative == "results/artifact_index.json":
            continue
        artifacts.append({
            "path": relative,
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        })
    return {"schema_version": "v37d.artifact-index.v1", "artifact_count": len(artifacts), "artifacts": artifacts}


def _bundle_manifest(root: Path, record: RunRecord) -> dict[str, Any]:
    files = {
        str(path.relative_to(root)).replace(os.sep, "/"): {
            "sha256": _sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in _relative_files(root)
    }
    return {
        "schema_version": RUN_BUNDLE_VERSION,
        "run_id": record.run_id,
        "task_id": record.task_id,
        "status": record.status,
        "execution_status": record.execution_status,
        "validation_status": record.validation_status,
        "overall_status": record.overall_status,
        "validation_result": record.validation_result,
        "execution_plan_sha256": record.execution_plan_sha256,
        "sealed_at": _now(),
        "files": files,
    }


def verify_run_bundle(bundle_root: str | Path) -> dict[str, Any]:
    root = Path(bundle_root)
    manifest_path = root / "bundle_manifest.json"
    if not manifest_path.exists():
        return {"ok": False, "reason_code": "RUN_BUNDLE_MANIFEST_MISSING", "issues": []}
    manifest = _read_json(manifest_path)
    issues: list[dict[str, Any]] = []
    for relative, expected in (manifest.get("files") or {}).items():
        path = root / relative
        if not path.exists():
            issues.append({"code": "RUN_BUNDLE_ARTIFACT_MISSING", "path": relative})
            continue
        actual = _sha256_file(path)
        if actual != expected.get("sha256"):
            issues.append({"code": "RUN_BUNDLE_ARTIFACT_HASH_MISMATCH", "path": relative, "expected": expected.get("sha256"), "actual": actual})
    return {"ok": not issues, "reason_code": "RUN_BUNDLE_VERIFIED" if not issues else "RUN_BUNDLE_INTEGRITY_FAILED", "issues": issues, "manifest": manifest}


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _lock_payload(lock_path: Path) -> dict[str, str]:
    payload: dict[str, str] = {}
    try:
        for line in lock_path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep:
                payload[key.strip()] = value.strip()
    except OSError:
        return {}
    return payload


def _clear_stale_execution_lock(root: Path) -> bool:
    lock_path = root / "runtime" / "execution.lock"
    if not lock_path.exists():
        return False
    payload = _lock_payload(lock_path)
    lock_host = payload.get("host")
    try:
        lock_pid = int(payload.get("pid") or 0)
    except ValueError:
        lock_pid = 0
    same_host = not lock_host or lock_host == socket.gethostname()
    if same_host and _pid_alive(lock_pid):
        return False
    stale_path = root / "runtime" / f"execution.lock.stale.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
    try:
        lock_path.replace(stale_path)
    except FileNotFoundError:
        return False
    write_json(root / "runtime" / "stale_lock_recovery.json", {
        "schema_version": "v0571.stale-lock-recovery.v1",
        "recovered_at": _now(),
        "stale_lock": stale_path.name,
        "payload": payload,
    })
    return True


@contextmanager
def _execution_lock(root: Path):
    lock_path = root / "runtime" / "execution.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError as exc:
            if _clear_stale_execution_lock(root):
                continue
            raise RuntimeError("RUN_ALREADY_EXECUTING: active execution lock already exists") from exc
    else:  # pragma: no cover - defensive
        raise RuntimeError("RUN_ALREADY_EXECUTING: unable to acquire execution lock")
    try:
        os.write(fd, f"pid={os.getpid()}\nhost={socket.gethostname()}\nstarted_at={_now()}\n".encode("utf-8"))
        os.close(fd)
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError as exc:
            record_runtime_diagnostic(
                code='RUN_LOCK_ALREADY_REMOVED',
                category=DiagnosticCategory.BEST_EFFORT_CLEANUP,
                location='src/sat_sim/run_bundle.py:_execution_lock:01',
                exception=exc,
                strict=False,
            )


def prepare_run(
    task_spec: Mapping[str, Any],
    *,
    output_root: str | Path,
    run_id: str | None = None,
    supersedes_run_id: str | None = None,
) -> PreparedRun:
    """Create a new immutable-intent Run Bundle without executing it."""

    canonical = canonicalize_task_spec(task_spec)
    planning = plan_task_spec(canonical)
    if not planning.ok or planning.resolved_spec is None or planning.execution_plan is None:
        codes = [issue.code for issue in planning.validation.errors]
        raise ValueError(f"EXECUTION_PLAN_INVALID: {codes}")
    resolved = planning.resolved_spec
    plan = planning.execution_plan
    generated_id = run_id or f"{_safe_id(resolved.task_id)}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}_{plan.plan_sha256[:8]}"
    root = Path(output_root) / generated_id
    if root.exists():
        raise FileExistsError(f"RUN_BUNDLE_ALREADY_EXISTS: {root}")
    for directory in ("input", "runtime", "attempts", "results", "validation", "logs", "control"):
        (root / directory).mkdir(parents=True, exist_ok=False)

    canonical_payload = dict(canonical)
    resolved_payload = resolved.model_dump(mode="json")
    plan_payload = plan.model_dump(mode="json")
    write_json(root / "input" / "task_spec.json", canonical_payload)
    _write_yaml(root / "input" / "task_spec.yaml", canonical_payload)
    write_json(root / "input" / "resolved_spec.json", resolved_payload)
    _write_yaml(root / "input" / "resolved_spec.yaml", resolved_payload)
    write_json(root / "input" / "parameter_consumption.json", resolved.parameter_consumption)
    write_json(root / "input" / "execution_plan.json", plan_payload)
    compiled = attach_plan_metadata(compile_task_spec(canonical, validate=False), planning)
    write_json(root / "runtime" / "compiled_task.json", compiled.to_dict())
    contract = get_capability(resolved.primary_capability_id)
    write_json(root / "runtime" / "environment.json", _runtime_environment(resolved))
    write_json(root / "runtime" / "dependency_versions.json", {
        "schema_version": "v37d.dependency-versions.v1",
        "dependencies": _dependency_versions(),
    })
    write_json(root / "runtime" / "capability_contract.json", {
        "schema_version": "v37d.capability-contract-snapshot.v1",
        "capability_id": contract.capability_id,
        "source_path": str(contract.path) if contract.path else None,
        "contract": contract.data,
    })
    write_json(root / "input" / "provenance.json", {
        "schema_version": "v37d.input-provenance.v1",
        "task_spec_sha256": resolved.canonical_task_spec_sha256,
        "resolved_spec_sha256": resolved.resolved_spec_sha256,
        "execution_plan_sha256": plan.plan_sha256,
        "capability_id": resolved.primary_capability_id,
        "captured_at": _now(),
        "agent": (canonical_payload.get("metadata") or {}).get("agent") if isinstance(canonical_payload.get("metadata"), Mapping) else None,
    })
    write_json(root / "runtime" / "reproducibility.json", {
        "schema_version": "v37d.reproducibility.v1",
        "execution_plan_sha256": plan.plan_sha256,
        "commit_required": True,
        "execute_api": f"POST /runs/{generated_id}/execute",
        "required_plan_hash": plan.plan_sha256,
        "deterministic_runner": resolved.primary_adapter,
    })
    prepared = PreparedRun(
        run_id=generated_id,
        task_id=resolved.task_id,
        bundle_root=str(root),
        canonical_task_spec_sha256=resolved.canonical_task_spec_sha256,
        resolved_spec_sha256=resolved.resolved_spec_sha256,
        execution_plan_sha256=plan.plan_sha256,
        primary_capability_id=resolved.primary_capability_id,
        prepared_at=_now(),
    )
    _write_record(root / "runtime" / "prepared_run.json", prepared)
    record = RunRecord(
        run_id=generated_id,
        task_id=resolved.task_id,
        status=RunStatus.PREPARED,
        created_at=prepared.prepared_at,
        updated_at=prepared.prepared_at,
        canonical_task_spec_sha256=resolved.canonical_task_spec_sha256,
        resolved_spec_sha256=resolved.resolved_spec_sha256,
        execution_plan_sha256=plan.plan_sha256,
        supersedes_run_id=supersedes_run_id,
    )
    _write_record(root / "run_record.json", record)
    return prepared


def request_cancel(bundle_root: str | Path, *, reason: str = "user_requested") -> Path:
    root = Path(bundle_root)
    if (root / "SEALED.json").exists():
        raise RuntimeError("RUN_ALREADY_SEALED")
    return write_json(root / "control" / "cancel.requested.json", {"requested_at": _now(), "reason": reason})


def _cancel_requested(root: Path) -> bool:
    return (root / "control" / "cancel.requested.json").exists()


def _load_attempt_records(root: Path) -> list[ExecutionAttempt]:
    attempts: list[ExecutionAttempt] = []
    for path in sorted((root / "attempts").glob("attempt_*/attempt.json")):
        try:
            attempts.append(ExecutionAttempt.model_validate(_read_json(path)))
        except Exception:
            continue
    return sorted(attempts, key=lambda item: item.sequence)


def recover_interrupted_run(bundle_root: str | Path, *, reason: str = "stale_worker_recovery") -> RunRecord:
    """Reset an unsealed interrupted run to PREPARED and preserve an aborted attempt."""
    root = Path(bundle_root)
    if (root / "SEALED.json").exists():
        raise RuntimeError("RUN_ALREADY_SEALED")
    record = RunRecord.model_validate(_read_json(root / "run_record.json"))
    attempts = _load_attempt_records(root)
    recovered: list[ExecutionAttempt] = []
    for attempt in attempts:
        if attempt.status == AttemptStatus.RUNNING:
            failure = ExecutionFailure(
                reason_code="EXECUTION_ATTEMPT_INTERRUPTED",
                failure_class="infrastructure",
                message=reason,
                retryable=True,
                exception_type=None,
            )
            attempt = attempt.model_copy(update={
                "status": AttemptStatus.ABORTED,
                "finished_at": _now(),
                "failure": failure,
            })
            _write_attempt(root, attempt)
        recovered.append(attempt)
    _clear_stale_execution_lock(root)
    record = record.model_copy(update={
        "status": RunStatus.PREPARED,
        "updated_at": _now(),
        "attempts": recovered,
        "execution_status": None,
        "validation_status": None,
        "overall_status": None,
        "validation_result": None,
        "terminal_reason_code": None,
        "sealed": False,
    })
    _write_record(root / "run_record.json", record)
    write_json(root / "runtime" / "interrupted_run_recovery.json", {
        "schema_version": "v0571.interrupted-run-recovery.v1",
        "recovered_at": _now(),
        "reason": reason,
        "attempt_count": len(recovered),
        "next_attempt_sequence": len(recovered) + 1,
    })
    return record


def finalize_interrupted_run(
    bundle_root: str | Path,
    *,
    status: RunStatus = RunStatus.CANCELLED,
    reason_code: str = "EXECUTION_CANCELLED",
    reason: str = "execution cancelled by queue worker",
) -> RunRecord:
    """Persist a terminal lifecycle state when an external worker stops the process tree."""
    if status not in {RunStatus.CANCELLED, RunStatus.FAILED}:
        raise ValueError("interrupted run may only be finalized as CANCELLED or FAILED")
    root = Path(bundle_root)
    record = RunRecord.model_validate(_read_json(root / "run_record.json"))
    attempts = _load_attempt_records(root)
    finalized: list[ExecutionAttempt] = []
    for attempt in attempts:
        if attempt.status == AttemptStatus.RUNNING:
            attempt_status = AttemptStatus.CANCELLED if status == RunStatus.CANCELLED else AttemptStatus.ABORTED
            failure = None if status == RunStatus.CANCELLED else ExecutionFailure(
                reason_code=reason_code,
                failure_class="infrastructure",
                message=reason,
                retryable=True,
            )
            attempt = attempt.model_copy(update={
                "status": attempt_status,
                "finished_at": _now(),
                "failure": failure,
            })
            _write_attempt(root, attempt)
        finalized.append(attempt)
    execution_status = ExecutionStatus.CANCELLED if status == RunStatus.CANCELLED else ExecutionStatus.FAILED
    record = record.model_copy(update={
        "status": status,
        "updated_at": _now(),
        "attempts": finalized,
        "execution_status": execution_status,
        "validation_status": ValidationResult.NOT_EVALUATED if status == RunStatus.CANCELLED else ValidationResult.FAIL,
        "overall_status": ValidationResult.NOT_EVALUATED if status == RunStatus.CANCELLED else ValidationResult.FAIL,
        "validation_result": ValidationResult.NOT_EVALUATED if status == RunStatus.CANCELLED else ValidationResult.FAIL,
        "terminal_reason_code": reason_code,
        "sealed": False,
    })
    _write_record(root / "run_record.json", record)
    write_json(root / "runtime" / "external_termination.json", {
        "schema_version": "v0571.external-termination.v1",
        "recorded_at": _now(),
        "status": status.value,
        "reason_code": reason_code,
        "reason": reason,
    })
    _clear_stale_execution_lock(root)
    return record


def classify_execution_failure(exc: BaseException) -> ExecutionFailure:
    text = f"{type(exc).__name__}: {exc}"
    lower = text.lower()
    retryable = any(token in lower for token in ("worker unavailable", "connection reset", "temporar", "resource busy", "i/o error", "input/output error"))
    if isinstance(exc, TimeoutError) or "timed out" in lower or "timeout" in lower:
        return ExecutionFailure(reason_code="EXECUTION_TIMEOUT", failure_class="infrastructure", message=str(exc), retryable=True, exception_type=type(exc).__name__)
    if retryable:
        return ExecutionFailure(reason_code="INFRASTRUCTURE_FAILURE", failure_class="infrastructure", message=str(exc), retryable=True, exception_type=type(exc).__name__)
    if any(token in lower for token in ("unsupported", "not implemented", "unknown capability", "validation")):
        return ExecutionFailure(reason_code="CAPABILITY_VALIDATION_FAILED", failure_class="capability", message=str(exc), retryable=False, exception_type=type(exc).__name__)
    return ExecutionFailure(reason_code="EXECUTION_FAILED", failure_class="runtime", message=str(exc), retryable=False, exception_type=type(exc).__name__)


def _write_streaming_json(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    path.write_bytes(encoded)
    return {"path": str(path), "size_bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}


def _write_streaming_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    count = 0
    with path.open("wb") as handle:
        for row in rows:
            line = json.dumps(dict(row), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
            handle.write(line)
            digest.update(line)
            size += len(line)
            count += 1
    return {"path": str(path), "size_bytes": size, "sha256": digest.hexdigest(), "row_count": count}


def _child_execute(
    queue: Any,
    compiled: CompiledTask,
    spec: dict[str, Any],
    result_path: str,
    execution_plan_sha256: str,
    request_id: str,
    bundle_root: str,
    timeout_s: float,
    resource_locks: tuple[str, ...],
) -> None:
    try:
        if os.name != "nt":
            os.setsid()
        result = execute_compiled_task(
            compiled,
            task_spec=spec,
            write_dataset=False,
            execution_plan_sha256=execution_plan_sha256,
            request_id=request_id,
            bundle_root=bundle_root,
            timeout_s=timeout_s,
            resource_locks=resource_locks,
        )
        root = Path(result_path).parent
        root.mkdir(parents=True, exist_ok=True)
        summary_manifest = _write_streaming_json(root / "summary.json", result.summary)
        metadata_manifest = _write_streaming_json(root / "runtime_metadata.json", result.runtime_metadata or {})
        trace_manifest = _write_streaming_jsonl(root / "telemetry.jsonl", result.trace_rows)
        queue.put({
            "ok": True,
            "summary": summary_manifest,
            "runtime_metadata": metadata_manifest,
            "trace_rows": trace_manifest,
            "transport": "streaming_file_manifest_v2",
        })
    except BaseException as exc:  # pragma: no cover - exercised through parent behavior
        queue.put({"ok": False, "exception_type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()})


def _verify_artifact(manifest: Mapping[str, Any]) -> Path:
    path = Path(str(manifest.get("path") or ""))
    if not path.is_file():
        raise RuntimeError("runner subprocess returned a missing result artifact")
    expected_size = int(manifest.get("size_bytes") or -1)
    expected_sha = str(manifest.get("sha256") or "")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    if size != expected_size or digest.hexdigest() != expected_sha:
        raise RuntimeError("runner subprocess result artifact failed size/hash verification")
    return path


def _read_verified_artifact(manifest: Mapping[str, Any]) -> bytes:
    # Summary and runtime metadata are small.  Large telemetry is verified and
    # parsed line-by-line by _read_verified_jsonl to avoid a duplicate bytes copy.
    return _verify_artifact(manifest).read_bytes()


def _read_verified_jsonl(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    path = _verify_artifact(manifest)
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                payload = json.loads(line)
                if isinstance(payload, dict):
                    rows.append(payload)
    expected_rows = int(manifest.get("row_count") or -1)
    if len(rows) != expected_rows:
        raise RuntimeError("runner subprocess telemetry row count verification failed")
    return rows

def _terminate_process_tree(process: Any, *, grace_s: float = 5.0) -> None:
    if not process.is_alive():
        return
    if os.name != "nt" and process.pid:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            process.terminate()
    elif os.name == "nt" and process.pid:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
    else:
        process.terminate()
    process.join(grace_s)
    if process.is_alive():
        try:
            if os.name != "nt" and process.pid:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            else:
                process.kill()
        except (ProcessLookupError, PermissionError, OSError):
            process.kill()
        process.join(grace_s)


def _execute_with_timeout(
    compiled: CompiledTask,
    spec: dict[str, Any],
    timeout_s: float,
    *,
    execution_plan_sha256: str,
    request_id: str,
    bundle_root: str | Path,
    resource_locks: Sequence[str],
) -> TaskRunResult:
    available = mp.get_all_start_methods()
    method = "spawn" if "spawn" in available else available[0]
    context = mp.get_context(method)
    queue = context.Queue()
    with tempfile.TemporaryDirectory(prefix="sat_sim_timeout_") as tmpdir:
        result_path = str(Path(tmpdir) / "task_run_result.json")
        process = context.Process(
            target=_child_execute,
            args=(
                queue,
                compiled,
                spec,
                result_path,
                execution_plan_sha256,
                request_id,
                str(bundle_root),
                timeout_s,
                tuple(resource_locks),
            ),
            daemon=False,
        )
        process.start()
        process.join(timeout_s)
        if process.is_alive():
            _terminate_process_tree(process)
            raise TimeoutError(f"deterministic runner exceeded timeout_s={timeout_s}")
        if queue.empty():
            raise RuntimeError(f"runner subprocess exited with code {process.exitcode} without a result")
        manifest = queue.get()
        if not manifest.get("ok"):
            raise RuntimeError(f"{manifest.get('exception_type')}: {manifest.get('message')}\n{manifest.get('traceback')}")
        summary = json.loads(_read_verified_artifact(manifest.get("summary") or {}).decode("utf-8"))
        runtime_metadata = json.loads(_read_verified_artifact(manifest.get("runtime_metadata") or {}).decode("utf-8"))
        rows = _read_verified_jsonl(manifest.get("trace_rows") or {})
    return TaskRunResult(
        compiled=compiled,
        summary=dict(summary or {}),
        trace_rows=tuple(rows),
        dataset=None,
        runtime_metadata=dict(runtime_metadata or {}),
    )


def _plan_next(plan: ExecutionPlan, node_id: str, condition: str) -> str:
    matches = [edge.to_node for edge in plan.edges if edge.from_node == node_id and edge.condition == condition]
    if len(matches) != 1:
        raise RuntimeError(f"PLAN_RUNTIME_BRANCH_INVALID: node={node_id} condition={condition} matches={matches}")
    return matches[0]


def _append_plan_event(
    events: list[PlanExecutionEvent],
    plan: ExecutionPlan,
    node_id: str,
    *,
    status: str,
    condition: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> str | None:
    next_node = _plan_next(plan, node_id, condition) if condition is not None and node_id not in plan.terminal_nodes else None
    events.append(PlanExecutionEvent(
        sequence=len(events) + 1,
        node_id=node_id,
        status=status,
        condition=condition,
        next_node=next_node,
        timestamp=_now(),
        details=dict(details or {}),
    ))
    return next_node


def _load_bundle(root: Path) -> tuple[dict[str, Any], ResolvedSpec, ExecutionPlan, CompiledTask, RunRecord]:
    spec = _read_json(root / "input" / "task_spec.json")
    resolved = ResolvedSpec.model_validate(_read_json(root / "input" / "resolved_spec.json"))
    plan = ExecutionPlan.model_validate(_read_json(root / "input" / "execution_plan.json"))
    report = validate_execution_plan(plan, resolved)
    if not report.ok:
        raise ValueError(f"EXECUTION_PLAN_INVALID: {[issue.code for issue in report.errors]}")
    compiled_payload = _read_json(root / "runtime" / "compiled_task.json")
    compiled = CompiledTask(**compiled_payload)
    record = RunRecord.model_validate(_read_json(root / "run_record.json"))
    return spec, resolved, plan, compiled, record


def _write_attempt(root: Path, attempt: ExecutionAttempt) -> None:
    attempt_root = root / "attempts" / attempt.attempt_id
    attempt_root.mkdir(parents=True, exist_ok=True)
    _write_record(attempt_root / "attempt.json", attempt)


def execute_prepared_run(
    bundle_root: str | Path,
    *,
    expected_plan_sha256: str,
    max_attempts: int = 2,
    hard_timeout: bool = True,
    executor: Callable[[CompiledTask, Mapping[str, Any]], TaskRunResult] | None = None,
) -> RunExecutionResult:
    """Commit and execute the exact prepared plan.

    ``executor`` is a test/integration seam.  Production calls use the registered
    deterministic runner.  Retries are permitted only for classified
    infrastructure failures and never modify TaskSpec, thresholds or QoI.
    """

    root = Path(bundle_root)
    if (root / "SEALED.json").exists():
        raise RuntimeError("RUN_ALREADY_SEALED")
    spec, resolved, plan, compiled, record = _load_bundle(root)
    if plan.plan_sha256 != expected_plan_sha256:
        raise ValueError(f"PLAN_HASH_MISMATCH: expected {expected_plan_sha256}, prepared {plan.plan_sha256}")
    if record.status != RunStatus.PREPARED:
        raise RuntimeError(f"RUN_NOT_PREPARED: {record.status}")
    max_attempts = max(1, min(int(max_attempts), 2))
    plan_events: list[PlanExecutionEvent] = []
    current_node = plan.entry_node
    for node_id in ("validate_task", "validate_guards", "resolve_dependencies", "validate_causal_requirements", "validate_parameter_consumption", "bind_effects", "bind_outputs"):
        if current_node != node_id:
            raise RuntimeError(f"PLAN_RUNTIME_ORDER_INVALID: expected {node_id}, got {current_node}")
        current_node = _append_plan_event(plan_events, plan, node_id, status="complete", condition="SUCCESS")

    summary: dict[str, Any] = {}
    rows: tuple[dict[str, Any], ...] = tuple()
    runtime_metadata: dict[str, Any] = {}
    execution_port_evidence: dict[str, Any] = {}
    attempts = list(record.attempts)
    attempt_diagnostics: list[dict[str, Any]] = []
    execution_status = ExecutionStatus.FAILED
    terminal_reason = "EXECUTION_FAILED"
    with _execution_lock(root):
        if current_node != "acquire_resources":
            raise RuntimeError(f"PLAN_RUNTIME_ORDER_INVALID: expected acquire_resources, got {current_node}")
        current_node = _append_plan_event(plan_events, plan, "acquire_resources", status="complete", condition="SUCCESS", details={"locks": resolved.resource_locks})
        record = record.model_copy(update={"status": RunStatus.RUNNING, "updated_at": _now()})
        _write_record(root / "run_record.json", record)
        if current_node != "execute_primary":
            raise RuntimeError(f"PLAN_RUNTIME_ORDER_INVALID: expected execute_primary, got {current_node}")
        start_sequence = len(attempts) + 1
        for sequence in range(start_sequence, max_attempts + 1):
            clear_runtime_diagnostics()
            attempt_id = f"attempt_{sequence:03d}"
            started = _now()
            attempt = ExecutionAttempt(
                attempt_id=attempt_id,
                sequence=sequence,
                status=AttemptStatus.RUNNING,
                started_at=started,
                plan_sha256=plan.plan_sha256,
            )
            _write_attempt(root, attempt)
            attempt_root = root / "attempts" / attempt_id
            execution_request_id = f"{record.run_id}:{attempt_id}"
            planned_adapter_key = (
                "test.executor"
                if executor is not None
                else "application.campaign"
                if compiled.task_type == "campaign"
                else adapter_key_for_compiled(compiled)
            )
            execution_port_evidence = {
                "schema_version": "sat-sim.execution-evidence.v1",
                "request_id": execution_request_id,
                "execution_plan_sha256": plan.plan_sha256,
                "adapter_key": planned_adapter_key,
                "legacy_mode": planned_adapter_key.startswith("legacy."),
                "legacy_bridge_called": False,
                "status": "STARTED",
                "capability_id": resolved.primary_capability_id,
                "task_type": compiled.task_type,
                "attempt_id": attempt_id,
            }
            if executor is not None:
                execution_port_evidence["test_integration_seam"] = True
            write_json(attempt_root / "execution_port.json", execution_port_evidence)
            if _cancel_requested(root):
                execution_port_evidence["status"] = "CANCELLED_BEFORE_EXECUTION"
                write_json(attempt_root / "execution_port.json", execution_port_evidence)
                diagnostic_report = runtime_diagnostics_report(clear=True)
                diagnostic_report.update({"schema_version": "v31.runtime-diagnostics.v1", "attempt_id": attempt_id})
                write_json(attempt_root / "runtime_diagnostics.json", diagnostic_report)
                attempt_diagnostics.append(diagnostic_report)
                attempt = attempt.model_copy(update={
                    "status": AttemptStatus.CANCELLED,
                    "finished_at": _now(),
                    "elapsed_seconds": 0.0,
                    "artifacts": {
                        "execution_port": "execution_port.json",
                        "runtime_diagnostics": "runtime_diagnostics.json",
                    },
                })
                _write_attempt(root, attempt)
                attempts.append(attempt)
                execution_status = ExecutionStatus.CANCELLED
                terminal_reason = "EXECUTION_CANCELLED"
                current_node = _append_plan_event(plan_events, plan, "execute_primary", status="cancelled", condition="CANCELLED", details={"attempt_id": attempt_id})
                break
            started_clock = monotonic()
            try:
                if executor is not None:
                    result = executor(compiled, spec)
                elif hard_timeout:
                    result = _execute_with_timeout(
                        compiled,
                        spec,
                        resolved.runtime_timeout_s,
                        execution_plan_sha256=plan.plan_sha256,
                        request_id=execution_request_id,
                        bundle_root=root,
                        resource_locks=resolved.resource_locks,
                    )
                else:
                    result = execute_compiled_task(
                        compiled,
                        task_spec=spec,
                        write_dataset=False,
                        execution_plan_sha256=plan.plan_sha256,
                        request_id=execution_request_id,
                        bundle_root=root,
                        timeout_s=resolved.runtime_timeout_s,
                        resource_locks=resolved.resource_locks,
                    )
                elapsed = monotonic() - started_clock
                actual_execution_evidence = dict((result.runtime_metadata or {}).get("unified_execution") or {})
                if actual_execution_evidence:
                    execution_port_evidence.update(actual_execution_evidence)
                execution_port_evidence.update({
                    "request_id": execution_request_id,
                    "execution_plan_sha256": plan.plan_sha256,
                    "attempt_id": attempt_id,
                })
                if _cancel_requested(root):
                    execution_port_evidence["status"] = "CANCELLED_AFTER_EXECUTION"
                    write_json(attempt_root / "execution_port.json", execution_port_evidence)
                    attempt = attempt.model_copy(update={
                        "status": AttemptStatus.CANCELLED,
                        "finished_at": _now(),
                        "elapsed_seconds": elapsed,
                        "artifacts": {"execution_port": "execution_port.json"},
                    })
                    _write_attempt(root, attempt)
                    attempts.append(attempt)
                    execution_status = ExecutionStatus.CANCELLED
                    terminal_reason = "EXECUTION_CANCELLED"
                    break
                summary = dict(result.summary)
                rows = tuple(dict(row) for row in result.trace_rows)
                runtime_metadata = dict(result.runtime_metadata or {})
                execution_port_evidence["status"] = "SUCCEEDED"
                runtime_metadata["unified_execution"] = execution_port_evidence
                write_json(attempt_root / "summary.json", summary)
                write_json(attempt_root / "execution_port.json", execution_port_evidence)
                write_jsonl(attempt_root / "telemetry.jsonl", rows)
                diagnostic_report = runtime_diagnostics_report(clear=True)
                diagnostic_report.update({"schema_version": "v31.runtime-diagnostics.v1", "attempt_id": attempt_id})
                write_json(attempt_root / "runtime_diagnostics.json", diagnostic_report)
                attempt_diagnostics.append(diagnostic_report)
                attempt = attempt.model_copy(update={
                    "status": AttemptStatus.SUCCEEDED,
                    "finished_at": _now(),
                    "elapsed_seconds": elapsed,
                    "artifacts": {
                        "summary": "summary.json",
                        "telemetry": "telemetry.jsonl",
                        "execution_port": "execution_port.json",
                        "runtime_diagnostics": "runtime_diagnostics.json",
                    },
                })
                _write_attempt(root, attempt)
                attempts.append(attempt)
                execution_status = ExecutionStatus.SUCCEEDED
                terminal_reason = "EXECUTION_SUCCEEDED"
                current_node = _append_plan_event(plan_events, plan, "execute_primary", status="complete", condition="SUCCESS", details={"attempt_id": attempt_id, "elapsed_seconds": elapsed})
                break
            except BaseException as exc:
                elapsed = monotonic() - started_clock
                failure = classify_execution_failure(exc)
                attempt_root = root / "attempts" / attempt_id
                trace_path = attempt_root / "exception.txt"
                trace_path.write_text(traceback.format_exc(), encoding="utf-8")
                failure = failure.model_copy(update={"traceback_file": "exception.txt"})
                execution_port_evidence.update({
                    "status": "TIMED_OUT" if failure.reason_code == "EXECUTION_TIMEOUT" else "FAILED",
                    "failure_reason_code": failure.reason_code,
                    "failure_class": failure.failure_class,
                    "retryable": failure.retryable,
                    "elapsed_seconds": elapsed,
                })
                write_json(attempt_root / "execution_port.json", execution_port_evidence)
                diagnostic_report = runtime_diagnostics_report(clear=True)
                diagnostic_report.update({"schema_version": "v31.runtime-diagnostics.v1", "attempt_id": attempt_id})
                write_json(attempt_root / "runtime_diagnostics.json", diagnostic_report)
                attempt_diagnostics.append(diagnostic_report)
                status = AttemptStatus.TIMED_OUT if failure.reason_code == "EXECUTION_TIMEOUT" else AttemptStatus.FAILED
                attempt = attempt.model_copy(update={
                    "status": status,
                    "finished_at": _now(),
                    "elapsed_seconds": elapsed,
                    "failure": failure,
                    "artifacts": {
                        "exception": "exception.txt",
                        "execution_port": "execution_port.json",
                        "runtime_diagnostics": "runtime_diagnostics.json",
                    },
                })
                _write_attempt(root, attempt)
                attempts.append(attempt)
                terminal_reason = failure.reason_code
                if not failure.retryable or sequence >= max_attempts:
                    break

        if execution_status == ExecutionStatus.FAILED and current_node == "execute_primary":
            current_node = _append_plan_event(plan_events, plan, "execute_primary", status="failed", condition="FAILURE", details={"reason_code": terminal_reason})

        if execution_status == ExecutionStatus.SUCCEEDED:
            if current_node != "collect_evidence":
                raise RuntimeError(f"PLAN_RUNTIME_ORDER_INVALID: expected collect_evidence, got {current_node}")
            current_node = _append_plan_event(plan_events, plan, "collect_evidence", status="complete", condition="SUCCESS", details={"trace_row_count": len(rows)})

        assertion_config = {}
        model_binding = spec.get("model") if isinstance(spec.get("model"), Mapping) else {}
        validation_config = model_binding.get("validation") if isinstance(model_binding.get("validation"), Mapping) else {}
        assertion_config = validation_config.get("acceptance_assertions") or []
        assertion_results = evaluate_assertions(summary, assertion_config)
        if assertion_results["status"] != "NOT_CONFIGURED":
            summary.update({
                "acceptance_assertion_status": assertion_results["status"],
                "acceptance_assertion_count": assertion_results["assertion_count"],
                "acceptance_assertion_pass_count": assertion_results["pass_count"],
                "acceptance_assertion_fail_count": assertion_results["fail_count"],
            })

        validation = evaluate_validation_outcome(
            spec=spec,
            resolved=resolved,
            summary=summary,
            trace_rows=rows,
            execution_status=execution_status,
            execution_reason_code=terminal_reason,
        )
        claims = build_claim_report(spec=spec, resolved=resolved, validation=validation)

        if execution_status == ExecutionStatus.SUCCEEDED:
            if current_node != "validate_qoi":
                raise RuntimeError(f"PLAN_RUNTIME_ORDER_INVALID: expected validate_qoi, got {current_node}")
            qoi_condition = validation.result.value
            current_node = _append_plan_event(plan_events, plan, "validate_qoi", status="complete", condition=qoi_condition, details={"validation_result": validation.result.value})
            if current_node != "build_claim_report":
                raise RuntimeError(f"PLAN_RUNTIME_ORDER_INVALID: expected build_claim_report, got {current_node}")
            current_node = _append_plan_event(plan_events, plan, "build_claim_report", status="complete", condition="SUCCESS", details={"allowed_claim_count": len(claims.allowed_claims)})

        dataset_artifact: DatasetArtifact | None = None
        if execution_status == ExecutionStatus.SUCCEEDED:
            write_json(root / "results" / "summary.json", summary)
            write_trace_csv(root / "results" / "telemetry.csv", rows)
            write_jsonl(root / "results" / "telemetry.jsonl", rows)
            dataset = write_task_dataset(
                output_root=root / "results" / "dataset",
                compiled=compiled,
                task_spec=spec,
                summary=summary,
                trace_rows=rows,
                status="complete",
                runtime_metadata=runtime_metadata,
                validation_outcome=validation.model_dump(mode="json"),
                run_id=record.run_id,
            )
            write_json(root / "results" / "dataset_write_result.json", dataset.to_dict())
            write_json(root / "results" / "metrics.json", _metrics_payload(summary))
            write_json(root / "results" / "events.json", _events_payload(spec, summary))
            write_json(root / "results" / "assertions.json", assertion_results)
            write_json(root / "results" / "plot_manifest.json", _plot_manifest_payload(spec, rows))
            # The HTML report reads validation and claim artifacts, so write them before report generation.
            write_json(root / "validation" / "validation_outcome.json", validation.model_dump(mode="json"))
            write_json(root / "validation" / "claim_report.json", claims.model_dump(mode="json"))
            generate_run_report(root)
            dataset_artifact = DatasetArtifact(
                output_root=str(dataset.output_root),
                files=dict(dataset.files),
                manifest=dict(dataset.manifest),
            )
        write_json(root / "validation" / "validation_outcome.json", validation.model_dump(mode="json"))
        write_json(root / "validation" / "claim_report.json", claims.model_dump(mode="json"))

        if current_node != "release_resources":
            raise RuntimeError(f"PLAN_RUNTIME_ORDER_INVALID: expected release_resources, got {current_node}")
        release_condition = (
            "CANCELLED" if execution_status == ExecutionStatus.CANCELLED
            else "FAILURE" if execution_status == ExecutionStatus.FAILED
            else validation.result.value
        )
        terminal_node = _append_plan_event(plan_events, plan, "release_resources", status="complete", condition=release_condition, details={"locks": resolved.resource_locks})
        if terminal_node is None or terminal_node not in plan.terminal_nodes:
            raise RuntimeError(f"PLAN_RUNTIME_TERMINAL_INVALID: {terminal_node}")
        overall_status = (
            validation.result
            if execution_status == ExecutionStatus.SUCCEEDED
            else ValidationResult.NOT_EVALUATED if execution_status == ExecutionStatus.CANCELLED
            else ValidationResult.FAIL
        )
        plan_events.append(PlanExecutionEvent(
            sequence=len(plan_events) + 1, node_id=terminal_node, status="terminal",
            timestamp=_now(), details={
                "execution_status": execution_status.value,
                "validation_status": validation.result.value,
                "overall_status": overall_status.value,
            },
        ))
        runtime_diagnostic_summary = {
            "schema_version": "v31.run-runtime-diagnostics.v1",
            "attempt_count": len(attempt_diagnostics),
            "event_count": sum(int(item.get("event_count", 0)) for item in attempt_diagnostics),
            "critical_event_count": sum(int(item.get("critical_event_count", 0)) for item in attempt_diagnostics),
            "attempts": attempt_diagnostics,
        }
        write_json(root / "runtime" / "runtime_diagnostics.json", runtime_diagnostic_summary)
        write_json(root / "runtime" / "execution_port.json", execution_port_evidence or {
            "schema_version": "sat-sim.execution-evidence.v1",
            "execution_plan_sha256": plan.plan_sha256,
            "status": "no_successful_adapter_result",
        })
        write_json(root / "runtime" / "execution_trace.json", {
            "schema_version": "v25.plan-execution-trace.v1",
            "plan_sha256": plan.plan_sha256,
            "events": [event.model_dump(mode="json") for event in plan_events],
            "terminal_node": terminal_node,
        })

        run_status = (
            RunStatus.CANCELLED if execution_status == ExecutionStatus.CANCELLED
            else RunStatus.FAILED if execution_status == ExecutionStatus.FAILED
            else RunStatus.SUCCEEDED
        )
        record = record.model_copy(update={
            "status": run_status,
            "updated_at": _now(),
            "attempts": attempts,
            "execution_status": execution_status,
            "validation_status": validation.result,
            "overall_status": overall_status,
            "validation_result": validation.result,
            "terminal_reason_code": terminal_reason if validation.result == ValidationResult.NOT_EVALUATED else validation.reason_code,
            "sealed": True,
        })
        _write_record(root / "run_record.json", record)
        write_json(root / "results" / "artifact_index.json", _artifact_index_payload(root))
        manifest = _bundle_manifest(root, record)
        write_json(root / "bundle_manifest.json", manifest)
        write_json(root / "SEALED.json", {
            "schema_version": RUN_BUNDLE_VERSION,
            "run_id": record.run_id,
            "sealed_at": manifest["sealed_at"],
            "bundle_manifest_sha256": _sha256_file(root / "bundle_manifest.json"),
            "immutability_policy": "append-only attempts; no artifact overwrite after sealing",
        })
    return RunExecutionResult(
        run_record=record,
        validation=validation,
        claim_report=claims,
        bundle_root=str(root),
        bundle_manifest=manifest,
        summary=summary,
        trace_rows=list(rows),
        dataset=dataset_artifact,
    )


def run_record_schema() -> dict[str, Any]:
    schema = RunRecord.model_json_schema(mode="validation")
    schema["$id"] = "https://example.local/sat-sim/run-record-v25.schema.json"
    schema["title"] = "Satellite Simulation RunRecord V25"
    return schema


def execution_attempt_schema() -> dict[str, Any]:
    schema = ExecutionAttempt.model_json_schema(mode="validation")
    schema["$id"] = "https://example.local/sat-sim/execution-attempt-v25.schema.json"
    schema["title"] = "Satellite Simulation ExecutionAttempt V25"
    return schema


def prepared_run_schema() -> dict[str, Any]:
    schema = PreparedRun.model_json_schema(mode="validation")
    schema["$id"] = "https://example.local/sat-sim/prepared-run-v25.schema.json"
    schema["title"] = "Satellite Simulation PreparedRun V25"
    return schema


__all__ = [
    "RUN_BUNDLE_VERSION",
    "RUN_RECORD_VERSION",
    "ATTEMPT_RECORD_VERSION",
    "RunStatus",
    "AttemptStatus",
    "PreparedRun",
    "ExecutionFailure",
    "ExecutionAttempt",
    "PlanExecutionEvent",
    "RunRecord",
    "DatasetArtifact",
    "RunExecutionResult",
    "prepare_run",
    "request_cancel",
    "recover_interrupted_run",
    "finalize_interrupted_run",
    "execute_prepared_run",
    "classify_execution_failure",
    "verify_run_bundle",
    "run_record_schema",
    "execution_attempt_schema",
    "prepared_run_schema",
]
