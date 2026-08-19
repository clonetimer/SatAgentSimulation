"""Basilisk adapter for the A3R bound ReactionWheel -> ADCS -> spacecraft graph."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sat_sim_execution import ExecutionRequest, RunResult, RunState
from sat_sim_model_assets.attitude_control import (
    A3_ADAPTER_KEY,
    ATTITUDE_CONTROL_BINDINGS,
    ATTITUDE_CONTROL_OBJECT,
    ATTITUDE_CONTROL_PROJECTION,
    BASILISK_SPACECRAFT_IMPLEMENTATION,
    SPACECRAFT_REF,
)

from ..bsk_engine.unified_native import WholeSpacecraftUnifiedNativeAdapter
from ..capability_registry import get_capability
from ..dataset_writer import write_task_dataset
from ..modifiers import modifiers_task_spec_payload
from ..task_compiler import CompiledTask
from ..task_runner import TaskRunResult


class AttitudeControlGraphExecutionAdapter:
    """Execute the bound graph directly; never calls the Legacy runner bridge."""

    adapter_key = A3_ADAPTER_KEY

    @staticmethod
    def _mapping(metadata: Mapping[str, Any], key: str) -> dict[str, Any]:
        value = metadata.get(key)
        if not isinstance(value, Mapping):
            raise TypeError(f"A3R request metadata requires mapping {key!r}")
        return dict(value)

    @staticmethod
    def _object_state(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        max_error = max((float(row.get("adcs.pointing_error_deg", 0.0)) for row in rows), default=0.0)
        final = rows[-1] if rows else {}
        final_error = float(final.get("adcs.pointing_error_deg", 0.0))
        speeds = [float(final.get(f"adcs.rw.speed_rad_s_{idx}", 0.0)) for idx in range(3)]
        state = {
            "object_ref": str(ATTITUDE_CONTROL_OBJECT.object_ref),
            "pointing_status": "STABLE" if final_error < 0.5 else "UNSTABLE",
            "rw_speed_rad_s": speeds,
            "safe_mode_active": bool(final.get("label.power_safe_mode_engaged", False)),
        }
        evidence = {
            "evidence_ref": "spacecraft.attitude_control.pointing_evidence",
            "binding_id": "pointing-evidence",
            "final_pointing_error_deg": final_error,
            "max_pointing_error_deg": max_error,
            "final_stable": final_error < 0.5,
        }
        return state, evidence

    def execute(self, request: ExecutionRequest) -> RunResult:
        if request.model_ref != SPACECRAFT_REF:
            raise ValueError(f"A3R adapter requires model_ref={SPACECRAFT_REF}")
        if request.implementation_id != BASILISK_SPACECRAFT_IMPLEMENTATION:
            raise ValueError(f"A3R adapter requires implementation_id={BASILISK_SPACECRAFT_IMPLEMENTATION}")
        metadata = request.metadata
        bound = self._mapping(metadata, "bound_model_graph")
        compiled_payload = self._mapping(metadata, "compiled_task")
        canonical_task_spec = self._mapping(metadata, "canonical_task_spec")
        runtime_task_spec = self._mapping(metadata, "resolved_runtime_task_spec")
        compiled = CompiledTask(**compiled_payload)

        contract = get_capability(str(request.capability_id))
        simulation_result = WholeSpacecraftUnifiedNativeAdapter().run(runtime_task_spec, contract.data)
        summary = dict(simulation_result.summary)
        rows = [dict(row) for row in simulation_result.trace_rows]

        modifier_payload = modifiers_task_spec_payload(runtime_task_spec)
        if modifier_payload.get("count", 0):
            active_sample_count = sum(
                1 for row in rows
                if row.get("label.modifier_active") is True
                or row.get("label.fault_active") is True
                or row.get("label.degradation_active") is True
                or row.get("label.constraint_active") is True
            )
            modifier_payload["active_sample_count"] = active_sample_count
            events_summary = summary.setdefault("events", {})
            if isinstance(events_summary, dict):
                events_summary["applied_modifiers"] = modifier_payload
            summary.update({
                "modifier_count": modifier_payload["count"],
                "fault_modifier_count": modifier_payload["fault_count"],
                "degradation_modifier_count": modifier_payload["degradation_count"],
                "constraint_modifier_count": modifier_payload["constraint_count"],
            })
        if simulation_result.labels:
            summary.setdefault("adapter_labels", simulation_result.labels)

        object_state, binding_evidence = self._object_state(rows)
        for row in rows:
            error = float(row.get("adcs.pointing_error_deg", 0.0))
            row["object.attitude_control.pointing_status"] = "STABLE" if error < 0.5 else "UNSTABLE"
            row["object.attitude_control.pointing_evidence"] = bool(error < 0.5)
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
        }
        model_asset_evidence = {
            "schema_version": "sat-sim.model-asset-execution.v1",
            **bound,
            "operations_object_ref": str(ATTITUDE_CONTROL_OBJECT.object_ref),
            "operations_object_sha256": ATTITUDE_CONTROL_OBJECT.content_sha256,
            "binding_set_sha256": ATTITUDE_CONTROL_BINDINGS.content_sha256,
            "capability_projection_sha256": ATTITUDE_CONTROL_PROJECTION.content_sha256,
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
                status="complete",
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
            state=RunState.SUCCEEDED,
            summary=summary,
            trace_rows=tuple(rows),
            runtime_metadata=runtime_metadata,
            evidence={
                "execution_port": execution_evidence,
                "model_asset_execution": model_asset_evidence,
                "binding_evidence": binding_evidence,
            },
            artifacts={"dataset": dataset.to_dict()} if dataset is not None else {},
            diagnostics=tuple(dict(item) for item in metadata.get("a3r_diagnostics", ()) if isinstance(item, Mapping)),
            legacy_compatibility={"enabled": False, "legacy_runner": None},
            native_result=native,
        )


__all__ = ["AttitudeControlGraphExecutionAdapter"]
