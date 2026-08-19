"""Basilisk adapter for the A4R bound composite digital-twin graph."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sat_sim_execution import ExecutionRequest, RunResult, RunState
from sat_sim_model_assets.composite_digital_twin import (
    COMPOSITE_ADAPTER_KEY,
    COMPOSITE_BINDINGS,
    COMPOSITE_IMPLEMENTATION,
    COMPOSITE_OBJECT,
    COMPOSITE_PROJECTION,
    COMPOSITE_SPACECRAFT_REF,
)

from ..adapters.whole_spacecraft_composite_digital_twin import WholeSpacecraftCompositeDigitalTwinAdapter
from ..capability_registry import get_capability
from ..dataset_writer import write_task_dataset
from ..task_compiler import CompiledTask
from ..task_runner import TaskRunResult


class CompositeDigitalTwinGraphExecutionAdapter:
    """Execute the A4R bound composite graph without using the Legacy runner bridge."""

    adapter_key = COMPOSITE_ADAPTER_KEY

    @staticmethod
    def _mapping(metadata: Mapping[str, Any], key: str) -> dict[str, Any]:
        value = metadata.get(key)
        if not isinstance(value, Mapping):
            raise TypeError(f"A4R request metadata requires mapping {key!r}")
        return dict(value)

    @staticmethod
    def _object_state(summary: Mapping[str, Any], rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        final = rows[-1] if rows else {}
        coupling_status = str(summary.get("proxy_coupling_runtime_status") or "UNKNOWN")
        effect_count = int(summary.get("requested_effect_count") or 0)
        state = {
            "object_ref": str(COMPOSITE_OBJECT.object_ref),
            "health_state": str(final.get("label.health_state") or summary.get("mode") or "unknown"),
            "coupling_status": coupling_status,
            "effect_active_count": int(final.get("effect.active_count") or 0),
        }
        evidence = {
            "evidence_ref": "spacecraft.composite_digital_twin.coupling_evidence",
            "binding_id": "coupling-evidence",
            "proxy_coupling_runtime_status": coupling_status,
            "requested_effect_count": effect_count,
            "active_effect_sample_count": int(summary.get("active_effect_sample_count") or 0),
        }
        return state, evidence

    @staticmethod
    def _run_state_from_summary(summary: Mapping[str, Any]) -> RunState:
        failure_keys = (
            "effect_execution_status",
            "validation_status",
            "overall_status",
            "runtime_fault_event_status",
        )
        for key in failure_keys:
            value = str(summary.get(key) or "").upper()
            if value in {"FAIL", "FAILED", "ERROR", "INCONCLUSIVE"} or value.startswith("FAILED"):
                return RunState.FAILED
        status = str(summary.get("status") or "").upper()
        if status in {"FAIL", "FAILED", "ERROR", "INCONCLUSIVE"} or status.startswith("FAIL"):
            return RunState.FAILED
        return RunState.SUCCEEDED

    def execute(self, request: ExecutionRequest) -> RunResult:
        if request.model_ref != COMPOSITE_SPACECRAFT_REF:
            raise ValueError(f"A4R adapter requires model_ref={COMPOSITE_SPACECRAFT_REF}")
        if request.implementation_id != COMPOSITE_IMPLEMENTATION:
            raise ValueError(f"A4R adapter requires implementation_id={COMPOSITE_IMPLEMENTATION}")
        metadata = request.metadata
        bound = self._mapping(metadata, "bound_model_graph")
        compiled_payload = self._mapping(metadata, "compiled_task")
        canonical_task_spec = self._mapping(metadata, "canonical_task_spec")
        runtime_task_spec = self._mapping(metadata, "resolved_runtime_task_spec")
        compiled = CompiledTask(**compiled_payload)

        contract = get_capability(str(request.capability_id))
        simulation_result = WholeSpacecraftCompositeDigitalTwinAdapter().run(runtime_task_spec, contract.data)
        summary = dict(simulation_result.summary)
        rows = [dict(row) for row in simulation_result.trace_rows]
        run_state = self._run_state_from_summary(summary)
        object_state, binding_evidence = self._object_state(summary, rows)
        for row in rows:
            row["object.composite_digital_twin.health_state"] = str(row.get("label.health_state") or "unknown")
            row["object.composite_digital_twin.effect_evidence"] = int(row.get("effect.active_count") or 0) > 0
            row["object.composite_digital_twin.coupling_evidence"] = (
                str(summary.get("proxy_coupling_runtime_status") or "").upper() == "PASS"
            )
        summary["operations_object"] = object_state
        summary["binding_evidence"] = binding_evidence

        execution_evidence = {
            "schema_version": "sat-sim.execution-evidence.v2",
            "request_id": request.request_id,
            "request_sha256": request.request_sha256,
            "execution_plan_sha256": request.execution_plan_sha256,
            "adapter_key": request.adapter_key,
            "route": "bound_model_graph",
            "legacy_mode": False,
            "capability_id": request.capability_id,
            "model_ref": str(request.model_ref),
            "implementation_id": str(request.implementation_id),
            "bound_model_graph_sha256": bound.get("content_sha256"),
            "model_graph_sha256": bound.get("graph_sha256"),
            "parameter_set_sha256": bound.get("parameter_set_sha256"),
            "binding_set_sha256": bound.get("binding_set_sha256"),
            "capability_projection_sha256": bound.get("capability_projection_sha256"),
            "legacy_bridge_called": False,
        }
        model_asset_evidence = {
            "schema_version": "sat-sim.model-asset-execution.v1",
            **bound,
            "model_graph_sha256": bound.get("graph_sha256"),
            "operations_object_ref": str(COMPOSITE_OBJECT.object_ref),
            "operations_object_sha256": COMPOSITE_OBJECT.content_sha256,
            "binding_set_sha256": COMPOSITE_BINDINGS.content_sha256,
            "capability_projection_sha256": COMPOSITE_PROJECTION.content_sha256,
            "legacy_bridge_called": False,
            "object_state": object_state,
            "evidence_binding": binding_evidence,
        }
        runtime_metadata = dict(simulation_result.metadata)
        runtime_metadata["unified_execution"] = execution_evidence
        runtime_metadata["model_asset_execution"] = model_asset_evidence
        summary_metadata = {key: value for key, value in runtime_metadata.items() if key != "native_multi_rate_telemetry"}
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
                status="complete" if run_state == RunState.SUCCEEDED else "failed",
                runtime_metadata=runtime_metadata,
            )
        native = TaskRunResult(
            compiled=compiled,
            summary=summary,
            trace_rows=tuple(rows),
            dataset=dataset,
            runtime_metadata=runtime_metadata,
        )
        return RunResult(
            request_id=request.request_id,
            task_id=request.task_id,
            execution_plan_sha256=request.execution_plan_sha256,
            adapter_key=request.adapter_key,
            state=run_state,
            summary=summary,
            trace_rows=tuple(rows),
            runtime_metadata=runtime_metadata,
            evidence={
                "execution_port": execution_evidence,
                "model_asset_execution": model_asset_evidence,
                "binding_evidence": binding_evidence,
            },
            artifacts={"dataset": dataset.to_dict()} if dataset is not None else {},
            diagnostics=tuple(dict(item) for item in metadata.get("a4r_diagnostics", ()) if isinstance(item, Mapping)),
            legacy_compatibility={"enabled": False, "legacy_runner": None},
            native_result=native,
        )


__all__ = ["CompositeDigitalTwinGraphExecutionAdapter"]
