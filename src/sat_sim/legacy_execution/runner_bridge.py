"""Single compatibility bridge from A2R adapters to the historical runner."""
from __future__ import annotations

from dataclasses import replace
from collections.abc import Mapping
from typing import Any

from sat_sim_execution import ExecutionRequest, RunResult, RunState


def execute_legacy_runner(request: ExecutionRequest, *, route: str) -> RunResult:
    """Execute the opaque A2R compatibility payload through the legacy runner.

    This function is intentionally the only production call site for
    ``run_compiled_task`` after A2R.  Physical behavior remains unchanged while
    all product-facing entry points are routed through ``ModelExecutionPort``.
    """

    payload = request.legacy_payload
    if not isinstance(payload, Mapping):
        raise TypeError("legacy execution requires a mapping legacy_payload")
    compiled = payload.get("compiled")
    task_spec = payload.get("task_spec")
    if compiled is None or not isinstance(task_spec, Mapping):
        raise TypeError("legacy_payload requires compiled and task_spec")

    # Lazy import keeps the execution contracts and registry independent from
    # the legacy physical runner and its concrete model imports.
    from ..task_runner import run_compiled_task

    native = run_compiled_task(
        compiled,
        task_spec=task_spec,
        output_root=payload.get("output_root"),
        write_dataset=bool(payload.get("write_dataset", False)),
    )
    execution_evidence = {
        "schema_version": "sat-sim.execution-evidence.v1",
        "request_id": request.request_id,
        "request_sha256": request.request_sha256,
        "execution_plan_sha256": request.execution_plan_sha256,
        "adapter_key": request.adapter_key,
        "route": route,
        "legacy_mode": True,
        "capability_id": request.capability_id,
        "task_type": getattr(compiled, "task_type", None),
        "runner": getattr(compiled, "runner", None),
    }
    runtime_metadata = dict(getattr(native, "runtime_metadata", None) or {})
    runtime_metadata["unified_execution"] = execution_evidence
    native = replace(native, runtime_metadata=runtime_metadata)
    dataset = native.dataset.to_dict() if native.dataset is not None else None
    return RunResult(
        request_id=request.request_id,
        task_id=request.task_id,
        execution_plan_sha256=request.execution_plan_sha256,
        adapter_key=request.adapter_key,
        state=RunState.SUCCEEDED,
        summary=dict(native.summary),
        trace_rows=tuple(dict(row) for row in native.trace_rows),
        runtime_metadata=runtime_metadata,
        evidence={"execution_port": execution_evidence},
        artifacts={"dataset": dataset} if dataset is not None else {},
        legacy_compatibility={
            "enabled": True,
            "route": route,
            "legacy_runner": "sat_sim.task_runner.run_compiled_task",
        },
        native_result=native,
    )


__all__ = ["execute_legacy_runner"]
