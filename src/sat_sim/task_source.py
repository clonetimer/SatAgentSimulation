"""Bind optional run submissions to a successful persisted parse receipt."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping

from .canonical_transport import parse_canonical_transport
from .task_spec import spec_sha256


class TaskSourceValidationError(ValueError):
    """An unusable receipt, with a stable reason for the API's 409 response."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


def validate_task_source(
    artifact_root: Path, request_id: str, task_spec: Mapping[str, Any],
) -> dict[str, str]:
    """Validate a successful parse report and its exact canonical task binding.

    No directories or files are written. The caller must perform this check
    before saving a task or preparing a run, then retain the validated ID in
    the run's existing sealed input provenance.
    """
    if not isinstance(request_id, str) or re.fullmatch(r"[0-9a-f]{32}", request_id) is None:
        raise TaskSourceValidationError("TASK_SOURCE_INVALID_ID", "Source request ID must be 32 lowercase hexadecimal characters.")
    tasks_root = Path(artifact_root) / "tasks"
    report_path = tasks_root / request_id / "unified_agent_report.json"
    try:
        if not report_path.resolve().is_relative_to(tasks_root.resolve()):
            raise TaskSourceValidationError("TASK_SOURCE_INVALID_ID", "Source report must remain within the task artifact directory.")
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except TaskSourceValidationError:
        raise
    except FileNotFoundError as exc:
        raise TaskSourceValidationError("TASK_SOURCE_MISSING", "Source parse report was not found.") from exc
    except (OSError, ValueError, RecursionError) as exc:
        raise TaskSourceValidationError("TASK_SOURCE_INVALID_REPORT", "Source parse report is unreadable or invalid JSON.") from exc
    if not isinstance(report, dict):
        raise TaskSourceValidationError("TASK_SOURCE_INVALID_REPORT", "Source parse report must be a structured object.")
    if report.get("ok") is not True:
        raise TaskSourceValidationError("TASK_SOURCE_NOT_SUCCESSFUL", "Source parse report does not record a successful parse.")
    source_spec = report.get("task_spec")
    declared_hash = report.get("task_spec_sha256")
    if not isinstance(source_spec, dict) or not source_spec or not isinstance(declared_hash, str) or re.fullmatch(r"[0-9a-f]{64}", declared_hash) is None:
        raise TaskSourceValidationError("TASK_SOURCE_INVALID_REPORT", "Source report must contain a valid TaskSpec object and SHA-256 digest.")
    try:
        canonical_source = parse_canonical_transport(None, json.dumps(source_spec, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
        source_hash = spec_sha256(canonical_source)
        raw_source_hash = spec_sha256(source_spec)
    except (ValueError, TypeError, RecursionError) as exc:
        raise TaskSourceValidationError("TASK_SOURCE_INVALID_REPORT", "Source report TaskSpec is invalid.") from exc
    if declared_hash != raw_source_hash or declared_hash != source_hash:
        raise TaskSourceValidationError("TASK_SOURCE_HASH_MISMATCH", "Source report digest does not match its canonical TaskSpec.")
    if spec_sha256(parse_canonical_transport(task_spec)) != source_hash:
        raise TaskSourceValidationError("TASK_SOURCE_TASK_MISMATCH", "Submitted TaskSpec differs from the successful source parse.")
    return {"request_id": request_id, "task_spec_sha256": source_hash}
