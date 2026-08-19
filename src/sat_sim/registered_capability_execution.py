"""Non-Legacy execution route for registry-backed Capability adapters.

This adapter is the generic execution boundary for active capabilities that do
not yet use a model-asset-specific execution adapter.  It invokes the adapter
registered by the Capability Registry directly and never calls the historical
``run_compiled_task`` compatibility bridge.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sat_sim_execution import ExecutionRequest, RunResult, RunState

from .capability_registry import get_capability
from .dataset_writer import write_task_dataset
from .task_compiler import CompiledTask
from .task_models import to_runtime_task_spec
from .task_runner import TaskRunResult, run_registered_capability_adapter

REGISTERED_CAPABILITY_ADAPTER_KEY = "capability.registered_adapter"


def _mapping(metadata: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = metadata.get(key)
    if not isinstance(value, Mapping):
        raise TypeError(f"registered capability request metadata requires mapping {key!r}")
    return dict(value)


def _run_state(summary: Mapping[str, Any]) -> RunState:
    for key in ("status", "validation_status", "overall_status", "runtime_truth_status"):
        value = str(summary.get(key) or "").strip().upper()
        if value in {"FAIL", "FAILED", "ERROR", "INCONCLUSIVE"} or value.startswith("FAIL"):
            return RunState.FAILED
    return RunState.SUCCEEDED


class RegisteredCapabilityExecutionAdapter:
    """Execute a registry-selected Capability adapter without Legacy fallback."""

    adapter_key = REGISTERED_CAPABILITY_ADAPTER_KEY

    def execute(self, request: ExecutionRequest) -> RunResult:
        metadata = request.metadata
        compiled_payload = _mapping(metadata, "compiled_task")
        canonical_task_spec = _mapping(metadata, "canonical_task_spec")
        compiled = CompiledTask(**compiled_payload)
        capability_id = request.capability_id
        if not isinstance(capability_id, str) or not capability_id:
            raise ValueError("registered capability execution requires capability_id")
        compiled_capability = compiled.metadata.get("capability_id") if isinstance(compiled.metadata, Mapping) else None
        if compiled_capability != capability_id:
            raise ValueError(
                f"compiled capability_id={compiled_capability!r} does not match request capability_id={capability_id!r}"
            )

        contract = get_capability(capability_id)
        if not (contract.is_active and contract.exposed_to_agent):
            raise ValueError(f"capability {capability_id!r} is not active and Agent-visible")
        implementation = contract.data.get("implementation") if isinstance(contract.data.get("implementation"), Mapping) else {}
        execution = contract.data.get("execution") if isinstance(contract.data.get("execution"), Mapping) else {}
        if bool(implementation.get("uses_legacy_runner", False)):
            raise ValueError(f"capability {capability_id!r} declares uses_legacy_runner=true")
        if bool(execution.get("legacy_fallback_enabled", False)):
            raise ValueError(f"capability {capability_id!r} declares legacy_fallback_enabled=true")

        runtime_task_spec = to_runtime_task_spec(canonical_task_spec)
        summary, rows, adapter_metadata = run_registered_capability_adapter(compiled, runtime_task_spec)
        state = _run_state(summary)
        execution_evidence = {
            "schema_version": "sat-sim.execution-evidence.v2",
            "request_id": request.request_id,
            "request_sha256": request.request_sha256,
            "execution_plan_sha256": request.execution_plan_sha256,
            "adapter_key": request.adapter_key,
            "route": "registered_capability_adapter",
            "legacy_mode": False,
            "legacy_bridge_called": False,
            "capability_id": capability_id,
            "capability_adapter": contract.adapter_class_path,
            "task_type": compiled.task_type,
            "runner": compiled.runner,
        }
        runtime_metadata = dict(adapter_metadata)
        runtime_metadata["unified_execution"] = execution_evidence
        summary = dict(summary)
        summary_metadata = {
            key: value for key, value in runtime_metadata.items()
            if key != "native_multi_rate_telemetry"
        }
        if summary_metadata:
            summary.setdefault("adapter_metadata", summary_metadata)

        dataset = None
        if request.write_dataset:
            root = Path(request.output_root or compiled.outputs.get("output_root") or compiled.task_id)
            dataset = write_task_dataset(
                output_root=root,
                compiled=compiled,
                task_spec=canonical_task_spec,
                summary=summary,
                trace_rows=rows,
                status="complete" if state == RunState.SUCCEEDED else "failed",
                runtime_metadata=runtime_metadata,
            )
        native = TaskRunResult(
            compiled=compiled,
            summary=summary,
            trace_rows=rows,
            dataset=dataset,
            runtime_metadata=runtime_metadata,
        )
        return RunResult(
            request_id=request.request_id,
            task_id=request.task_id,
            execution_plan_sha256=request.execution_plan_sha256,
            adapter_key=request.adapter_key,
            state=state,
            summary=summary,
            trace_rows=rows,
            runtime_metadata=runtime_metadata,
            evidence={"execution_port": execution_evidence},
            artifacts={"dataset": dataset.to_dict()} if dataset is not None else {},
            legacy_compatibility={"enabled": False, "legacy_runner": None},
            native_result=native,
        )


__all__ = ["REGISTERED_CAPABILITY_ADAPTER_KEY", "RegisteredCapabilityExecutionAdapter"]
