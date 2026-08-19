"""V26 finite, auditable TaskSpec repair.

Only representation and operational-path repairs are allowed.  This module
never changes a physical model, requested event, QoI, assertion threshold,
parameter profile or claim level.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from pathlib import PurePath
from typing import Any, Literal, Mapping

from .capability_registry import get_capability
from .task_models import canonicalize_task_spec, is_canonical_task_spec
from .task_validator import validate_task_spec

REPAIR_POLICY_VERSION = "v26.limited-repair.v1"


@dataclass(frozen=True)
class RepairDiff:
    path: str
    before: Any
    after: Any
    reason_code: str
    semantic_change: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RepairReport:
    policy_version: str
    status: Literal["unchanged", "repaired", "blocked"]
    repair_class: Literal["repairable", "requires_user_input", "requires_model_extension", "non_repairable"]
    original_valid: bool
    repaired_valid: bool
    repaired_spec: dict[str, Any] | None
    diffs: tuple[RepairDiff, ...] = field(default_factory=tuple)
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    forbidden_changes_checked: tuple[str, ...] = (
        "events",
        "outputs.qoi",
        "model.validation.assertions",
        "parameters.profile",
        "assurance.claim_level",
        "assurance.fidelity_level",
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "status": self.status,
            "repair_class": self.repair_class,
            "original_valid": self.original_valid,
            "repaired_valid": self.repaired_valid,
            "repaired_spec": copy.deepcopy(self.repaired_spec),
            "diffs": [item.to_dict() for item in self.diffs],
            "reason_codes": list(self.reason_codes),
            "forbidden_changes_checked": list(self.forbidden_changes_checked),
        }


def _safe_relative_output_root(value: Any, task_id: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return f"runs/{task_id}"
    path = PurePath(text)
    if path.is_absolute() or ".." in path.parts:
        return f"runs/{task_id}"
    return text


def _deprecated_replacement(capability_id: str | None) -> str | None:
    if not capability_id:
        return None
    try:
        contract = get_capability(str(capability_id))
    except Exception:
        return None
    lifecycle = contract.data.get("lifecycle") if isinstance(contract.data.get("lifecycle"), Mapping) else {}
    if lifecycle.get("status") == "deprecated" and lifecycle.get("replacement"):
        return str(lifecycle["replacement"])
    return None


def repair_task_spec_limited(spec: Mapping[str, Any], *, max_repairs: int = 2) -> RepairReport:
    """Apply at most two whitelisted non-semantic repairs."""

    original = copy.deepcopy(dict(spec))
    original_validation = validate_task_spec(original)
    working = copy.deepcopy(original)
    diffs: list[RepairDiff] = []
    reasons: list[str] = []

    # Repair 1: deterministic legacy/candidate normalization.  This changes the
    # representation only; the migration report is carried elsewhere.
    try:
        canonical = canonicalize_task_spec(working)
        if not is_canonical_task_spec(working):
            diffs.append(RepairDiff("$", working, canonical, "TASKSPEC_LEGACY_MIGRATED", semantic_change=False))
            reasons.append("TASKSPEC_LEGACY_MIGRATED")
        working = canonical
    except Exception:
        return RepairReport(
            policy_version=REPAIR_POLICY_VERSION,
            status="blocked",
            repair_class="requires_user_input",
            original_valid=original_validation.ok,
            repaired_valid=False,
            repaired_spec=None,
            reason_codes=("TASKSPEC_MIGRATION_FAILED",),
        )

    task_id = str(((working.get("task") or {}).get("id")) or "task")
    outputs = working.get("outputs") if isinstance(working.get("outputs"), dict) else {}
    before_root = outputs.get("output_root")
    safe_root = _safe_relative_output_root(before_root, task_id)
    if before_root != safe_root and len(diffs) < max_repairs:
        outputs["output_root"] = safe_root
        working["outputs"] = outputs
        diffs.append(RepairDiff("$.outputs.output_root", before_root, safe_root, "OUTPUT_ROOT_NORMALIZED", semantic_change=False))
        reasons.append("OUTPUT_ROOT_NORMALIZED")

    model = working.get("model") if isinstance(working.get("model"), dict) else {}
    cid = model.get("capability_id")
    replacement = _deprecated_replacement(str(cid) if cid else None)
    if replacement and replacement != cid and len(diffs) < max_repairs:
        model["capability_id"] = replacement
        working["model"] = model
        diffs.append(RepairDiff("$.model.capability_id", cid, replacement, "DEPRECATED_CAPABILITY_MIGRATED", semantic_change=False))
        reasons.append("DEPRECATED_CAPABILITY_MIGRATED")

    repaired_validation = validate_task_spec(working)
    if repaired_validation.ok:
        status: Literal["unchanged", "repaired", "blocked"] = "repaired" if diffs else "unchanged"
        return RepairReport(
            policy_version=REPAIR_POLICY_VERSION,
            status=status,
            repair_class="repairable",
            original_valid=original_validation.ok,
            repaired_valid=True,
            repaired_spec=working,
            diffs=tuple(diffs),
            reason_codes=tuple(reasons),
        )

    error_codes = {issue.code for issue in repaired_validation.errors}
    if any(code in {"unsupported_effect", "capability"} for code in error_codes):
        repair_class = "requires_model_extension"
        reasons.append("REQUIRES_MODEL_EXTENSION")
    elif any("required" in issue.message.lower() or "missing" in issue.message.lower() for issue in repaired_validation.errors):
        repair_class = "requires_user_input"
        reasons.append("REQUIRED_USER_INPUT")
    else:
        repair_class = "non_repairable"
        reasons.append("NON_REPAIRABLE_VALIDATION_FAILURE")
    return RepairReport(
        policy_version=REPAIR_POLICY_VERSION,
        status="blocked",
        repair_class=repair_class,
        original_valid=original_validation.ok,
        repaired_valid=False,
        repaired_spec=working,
        diffs=tuple(diffs),
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


__all__ = [
    "REPAIR_POLICY_VERSION",
    "RepairDiff",
    "RepairReport",
    "repair_task_spec_limited",
]
