"""Engine-independent execution result contract."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from collections.abc import Mapping, Sequence
from typing import Any

from .contracts import validate_adapter_key
from .errors import AdapterResultError

RUN_RESULT_VERSION = "sat-sim.run-result.v1"


class RunState(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class RunResult:
    request_id: str
    task_id: str
    execution_plan_sha256: str
    adapter_key: str
    state: RunState
    summary: Mapping[str, Any] = field(default_factory=dict)
    trace_rows: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    runtime_metadata: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    artifacts: Mapping[str, Any] = field(default_factory=dict)
    diagnostics: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    legacy_compatibility: Mapping[str, Any] = field(default_factory=dict)
    native_result: Any = field(default=None, repr=False, compare=False)
    schema_version: str = RUN_RESULT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id:
            raise AdapterResultError("request_id must be a non-empty string")
        if not isinstance(self.task_id, str) or not self.task_id:
            raise AdapterResultError("task_id must be a non-empty string")
        validate_adapter_key(self.adapter_key)
        if not isinstance(self.state, RunState):
            object.__setattr__(self, "state", RunState(str(self.state)))
        for name in ("summary", "runtime_metadata", "evidence", "artifacts", "legacy_compatibility"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise AdapterResultError(f"{name} must be a mapping")
            object.__setattr__(self, name, dict(value))
        rows: list[dict[str, Any]] = []
        for row in self.trace_rows:
            if not isinstance(row, Mapping):
                raise AdapterResultError("trace_rows must contain mappings")
            rows.append(dict(row))
        object.__setattr__(self, "trace_rows", tuple(rows))
        diagnostics: list[dict[str, Any]] = []
        for item in self.diagnostics:
            if not isinstance(item, Mapping):
                raise AdapterResultError("diagnostics must contain mappings")
            diagnostics.append(dict(item))
        object.__setattr__(self, "diagnostics", tuple(diagnostics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "execution_plan_sha256": self.execution_plan_sha256,
            "adapter_key": self.adapter_key,
            "state": self.state.value,
            "summary": dict(self.summary),
            "trace_rows": [dict(row) for row in self.trace_rows],
            "runtime_metadata": dict(self.runtime_metadata),
            "evidence": dict(self.evidence),
            "artifacts": dict(self.artifacts),
            "diagnostics": [dict(item) for item in self.diagnostics],
            "legacy_compatibility": dict(self.legacy_compatibility),
        }


__all__ = ["RUN_RESULT_VERSION", "RunResult", "RunState"]
