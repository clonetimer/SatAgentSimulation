"""Capability-specific TaskSpec pruning for Agent-generated drafts.

A4 keeps capability-python generation deterministic and compact by removing
sections that belong to other capability families.  This prevents template
pollution such as standalone component requests carrying whole-spacecraft or
orbit_environment payloads.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .capability_registry import get_capability


COMMON_TOP_LEVEL_KEYS = {
    "schema_version",
    "task_id",
    "task_type",
    "capability_id",
    "description",
    "tags",
    "template",
    "target",
    "simulation",
    "outputs",
    "metadata",
    "modifiers",
    "faults",
    "degradations",
}

# Capability/task sections that are allowed in addition to COMMON_TOP_LEVEL_KEYS.
SECTIONS_BY_LEVEL: dict[str, set[str]] = {
    "component": {"parameters"},
    "subsystem": {"parameters"},
    "integrated": {"parameters", "orbit_environment"},
    "orbit_environment": {"parameters", "orbit_environment"},
    "whole_spacecraft": {"parameters", "spacecraft", "orbit_environment"},
}

# These capabilities intentionally use a richer top-level shape.
EXPLICIT_CAPABILITY_SECTIONS: dict[str, set[str]] = {
    "orbit_environment.leo_simple.v1": {"parameters", "orbit_environment"},
    "orbit_environment.medium_fidelity.v1": {"parameters", "orbit_environment"},
    "subsystem.comm.basic_ground_pass.v1": {"parameters", "orbit_environment"},
    "whole_spacecraft.basic_power_orbit.v1": {"parameters", "spacecraft", "orbit_environment"},
    "whole_spacecraft.basic_power_attitude_orbit.v1": {"parameters", "spacecraft", "orbit_environment"},
    "whole_spacecraft.basic_power_thermal_orbit.v1": {"parameters", "spacecraft", "orbit_environment"},
    "whole_spacecraft.power_thermal_orbit_coupled.v1": {"parameters", "spacecraft", "orbit_environment", "validation"},
    "whole_spacecraft.comm_payload_mission_coupled.v1": {"parameters", "spacecraft", "orbit_environment", "validation"},
    "whole_spacecraft.maneuver_orbit_attitude.v1": {"parameters", "spacecraft", "orbit_environment", "validation"},
}


@dataclass(frozen=True)
class TaskSpecPruneResult:
    task_spec: dict[str, Any]
    pruned_fields: tuple[str, ...] = field(default_factory=tuple)
    allowed_top_level_keys: tuple[str, ...] = field(default_factory=tuple)

    @property
    def changed(self) -> bool:
        return bool(self.pruned_fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "pruned_fields": list(self.pruned_fields),
            "allowed_top_level_keys": list(self.allowed_top_level_keys),
        }


def allowed_task_spec_sections(capability_id: str | None, *, task_type: str | None = None) -> set[str]:
    """Return allowed top-level TaskSpec keys for a capability.

    Campaign specs are intentionally left broad because they carry nested base
    specs.  For runnable single-capability TaskSpecs, the capability contract's
    target level determines whether sections such as ``spacecraft`` or
    ``orbit_environment`` are meaningful.
    """

    if task_type == "campaign":
        return set(COMMON_TOP_LEVEL_KEYS) | {"campaign", "validation", "parameters", "spacecraft", "orbit_environment"}
    if capability_id in EXPLICIT_CAPABILITY_SECTIONS:
        return set(COMMON_TOP_LEVEL_KEYS) | set(EXPLICIT_CAPABILITY_SECTIONS[capability_id or ""])
    level = task_type or "component"
    if capability_id:
        try:
            contract = get_capability(capability_id)
            level = contract.target_level or level
        except Exception as exc:
            record_runtime_diagnostic(
                code='CAPABILITY_SECTION_POLICY_LOOKUP_FAILED',
                category=DiagnosticCategory.REGISTRY_SCHEMA_FAILURE,
                location='src/sat_sim/taskspec_pruning.py:allowed_task_spec_sections:01',
                exception=exc,
                strict=None,
            )
    return set(COMMON_TOP_LEVEL_KEYS) | set(SECTIONS_BY_LEVEL.get(level, {"parameters"}))


def _path_for(key: str) -> str:
    return f"$.{key}"


def prune_task_spec_for_capability(spec: Mapping[str, Any], *, capability_id: str | None = None, record_metadata: bool = True) -> TaskSpecPruneResult:
    """Prune unrelated top-level sections from a TaskSpec copy.

    The function is conservative: it only removes top-level sections that are
    valid in the global schema but not meaningful for the selected capability.
    It never edits Python, imports, adapter routing, or source bindings.
    """

    out = dict(spec)
    cid = capability_id or (out.get("capability_id") if isinstance(out.get("capability_id"), str) else None)
    task_type = out.get("task_type") if isinstance(out.get("task_type"), str) else None
    allowed = allowed_task_spec_sections(cid, task_type=task_type)
    pruned: list[str] = []
    for key in list(out.keys()):
        if key not in allowed:
            pruned.append(_path_for(key))
            out.pop(key, None)
    # Remove semantically unrelated globally-valid sections.
    for key in ("spacecraft", "orbit_environment", "campaign", "validation"):
        if key in out and key not in allowed:
            pruned.append(_path_for(key))
            out.pop(key, None)
    if record_metadata:
        metadata = dict(out.get("metadata") or {}) if isinstance(out.get("metadata"), Mapping) else {}
        if pruned:
            existing = list(metadata.get("pruned_fields") or []) if isinstance(metadata.get("pruned_fields"), Sequence) and not isinstance(metadata.get("pruned_fields"), (str, bytes)) else []
            for item in pruned:
                if item not in existing:
                    existing.append(item)
            metadata["pruned_fields"] = existing
            metadata["taskspec_pruning"] = {
                "schema_version": "a4.taskspec_pruning.v1",
                "capability_id": cid,
                "pruned_fields": list(pruned),
                "allowed_top_level_keys": sorted(allowed),
            }
            out["metadata"] = metadata
    return TaskSpecPruneResult(task_spec=out, pruned_fields=tuple(pruned), allowed_top_level_keys=tuple(sorted(allowed)))


__all__ = ["TaskSpecPruneResult", "allowed_task_spec_sections", "prune_task_spec_for_capability"]
