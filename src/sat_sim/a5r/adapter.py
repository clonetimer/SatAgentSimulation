"""Non-Legacy execution adapters for the A5R focused subsystem graphs."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from sat_sim_execution import ExecutionRequest, RunResult, RunState
from sat_sim_model_assets.subsystem_verticals import (
    COMM_BUNDLE,
    EPS_BUNDLE,
    PROPULSION_BUNDLE,
    THERMAL_BUNDLE,
    SubsystemAssetBundle,
)

from ..capability_registry import get_capability
from ..dataset_writer import write_task_dataset
from ..task_compiler import CompiledTask
from ..task_runner import TaskRunResult


def _physical_adapter_factory(capability_id: str) -> Callable[[], object]:
    if capability_id == EPS_BUNDLE.capability_id:
        from ..adapters.subsystem_eps_unified_native import EpsUnifiedNativeAdapter
        return EpsUnifiedNativeAdapter
    if capability_id == COMM_BUNDLE.capability_id:
        from ..adapters.subsystem_comm_data_unified_native import CommDataUnifiedNativeAdapter
        return CommDataUnifiedNativeAdapter
    if capability_id == THERMAL_BUNDLE.capability_id:
        from ..adapters.subsystem_source_native import ThermalSourceNativeAdapter
        return ThermalSourceNativeAdapter
    if capability_id == PROPULSION_BUNDLE.capability_id:
        from ..adapters.subsystem_propulsion_unified_native import PropulsionUnifiedNativeAdapter
        return PropulsionUnifiedNativeAdapter
    raise KeyError(f"no physical adapter factory for {capability_id}")


def _run_state(summary: Mapping[str, Any]) -> RunState:
    for key in ("status", "validation_status", "overall_status", "runtime_truth_status"):
        value = str(summary.get(key) or "").upper()
        if value in {"FAIL", "FAILED", "ERROR", "INCONCLUSIVE"} or value.startswith("FAIL"):
            return RunState.FAILED
    return RunState.SUCCEEDED


def _validate_physical_adapter(adapter: object, spec: Mapping[str, Any], capability: Mapping[str, Any]) -> None:
    validate = getattr(adapter, "validate", None)
    if not callable(validate):
        return
    issues = tuple(validate(spec, capability))
    errors = [item for item in issues if str(getattr(item, "severity", "")).lower() == "error"]
    if errors:
        details = "; ".join(
            f"{getattr(item, 'path', '$')}: {getattr(item, 'message', str(item))}" for item in errors[:8]
        )
        raise ValueError(f"A5R physical adapter validation failed: {details}")


def _object_state(bundle: SubsystemAssetBundle, summary: Mapping[str, Any], rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    final = rows[-1] if rows else {}
    if bundle.capability_id == EPS_BUNDLE.capability_id:
        soc_values = [float(row.get("eps.battery_soc", 0.0)) for row in rows]
        state = {
            "object_ref": str(bundle.operations_object.object_ref),
            "soc": float(final.get("eps.battery_soc", 0.0)),
            "power_balance_w": float(final.get("eps.net_power_w", 0.0)),
            "load_shed_state": bool(final.get("eps.load_shed_active", False)),
        }
        evidence = {
            "evidence_ref": "spacecraft.eps.soc_evidence",
            "binding_id": "soc-evidence",
            "final_soc": state["soc"],
            "minimum_soc": min(soc_values, default=state["soc"]),
        }
    elif bundle.capability_id == COMM_BUNDLE.capability_id:
        state = {
            "object_ref": str(bundle.operations_object.object_ref),
            "storage_bits": float(final.get("comm_data.storage_level_bits", 0.0)),
            "downlink_baud_bps": float(final.get("comm_data.transmitter_baud_bps", 0.0)),
            "storage_drain_enabled": bool(final.get("comm_data.native_storage_drain_enabled", False)),
        }
        evidence = {
            "evidence_ref": "spacecraft.comm_data.storage_evidence",
            "binding_id": "storage-evidence",
            "final_storage_bits": state["storage_bits"],
            "estimated_native_downlinked_bits": float(summary.get("estimated_native_downlinked_bits") or 0.0),
        }
    elif bundle.capability_id == THERMAL_BUNDLE.capability_id:
        battery_values = [float(row.get("thermal.source_native.battery_temp_k", 0.0)) for row in rows]
        electronics_values = [float(row.get("thermal.source_native.electronics_temp_k", 0.0)) for row in rows]
        state = {
            "object_ref": str(bundle.operations_object.object_ref),
            "battery_temp_k": float(final.get("thermal.source_native.battery_temp_k", 0.0)),
            "electronics_temp_k": float(final.get("thermal.source_native.electronics_temp_k", 0.0)),
            "safe_request": bool(final.get("thermal.source_native.thermal_safe_request", False)),
        }
        evidence = {
            "evidence_ref": "spacecraft.thermal.safety_evidence",
            "binding_id": "safety-evidence",
            "maximum_battery_temp_k": max(battery_values, default=state["battery_temp_k"]),
            "maximum_electronics_temp_k": max(electronics_values, default=state["electronics_temp_k"]),
            "safe_request_count": sum(1 for row in rows if row.get("thermal.source_native.thermal_safe_request")),
        }
    elif bundle.capability_id == PROPULSION_BUNDLE.capability_id:
        initial_fuel = float(rows[0].get("propulsion.fuel_mass_kg", 0.0)) if rows else 0.0
        final_fuel = float(final.get("propulsion.fuel_mass_kg", 0.0))
        state = {
            "object_ref": str(bundle.operations_object.object_ref),
            "fuel_mass_kg": final_fuel,
            "velocity_x_m_s": float(final.get("propulsion.velocity_x_m_s", 0.0)),
            "thrust_active": float(final.get("propulsion.thrust_force_n", 0.0)) > 0.0,
        }
        evidence = {
            "evidence_ref": "spacecraft.propulsion.propellant_evidence",
            "binding_id": "propellant-evidence",
            "propellant_used_kg": max(0.0, initial_fuel - final_fuel),
            "final_velocity_x_m_s": state["velocity_x_m_s"],
        }
    else:  # pragma: no cover - constructor restricts supported bundles
        raise KeyError(bundle.capability_id)
    return state, evidence


def _enrich_rows(bundle: SubsystemAssetBundle, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        if bundle.capability_id == EPS_BUNDLE.capability_id:
            row["object.eps.soc"] = float(row.get("eps.battery_soc", 0.0))
            row["object.eps.load_shed_state"] = bool(row.get("eps.load_shed_active", False))
        elif bundle.capability_id == COMM_BUNDLE.capability_id:
            row["object.comm_data.storage_bits"] = float(row.get("comm_data.storage_level_bits", 0.0))
            row["object.comm_data.storage_drain_enabled"] = bool(row.get("comm_data.native_storage_drain_enabled", False))
        elif bundle.capability_id == THERMAL_BUNDLE.capability_id:
            row["object.thermal.battery_temp_k"] = float(row.get("thermal.source_native.battery_temp_k", 0.0))
            row["object.thermal.safe_request"] = bool(row.get("thermal.source_native.thermal_safe_request", False))
        elif bundle.capability_id == PROPULSION_BUNDLE.capability_id:
            row["object.propulsion.fuel_mass_kg"] = float(row.get("propulsion.fuel_mass_kg", 0.0))
            row["object.propulsion.thrust_active"] = float(row.get("propulsion.thrust_force_n", 0.0)) > 0.0


class SubsystemGraphExecutionAdapter:
    """Execute one governed focused-subsystem graph without Legacy bridge use."""

    def __init__(self, bundle: SubsystemAssetBundle) -> None:
        self.bundle = bundle
        self.adapter_key = bundle.adapter_key

    @staticmethod
    def _mapping(metadata: Mapping[str, Any], key: str) -> dict[str, Any]:
        value = metadata.get(key)
        if not isinstance(value, Mapping):
            raise TypeError(f"A5R request metadata requires mapping {key!r}")
        return dict(value)

    def execute(self, request: ExecutionRequest) -> RunResult:
        bundle = self.bundle
        if request.model_ref != bundle.model_ref:
            raise ValueError(f"A5R adapter requires model_ref={bundle.model_ref}")
        if request.implementation_id != bundle.implementation_id:
            raise ValueError(f"A5R adapter requires implementation_id={bundle.implementation_id}")
        if request.capability_id != bundle.capability_id:
            raise ValueError(f"A5R adapter requires capability_id={bundle.capability_id}")

        metadata = request.metadata
        bound = self._mapping(metadata, "bound_model_graph")
        compiled_payload = self._mapping(metadata, "compiled_task")
        canonical_task_spec = self._mapping(metadata, "canonical_task_spec")
        runtime_task_spec = self._mapping(metadata, "resolved_runtime_task_spec")
        compiled = CompiledTask(**compiled_payload)

        contract = get_capability(bundle.capability_id)
        physical_adapter = _physical_adapter_factory(bundle.capability_id)()
        _validate_physical_adapter(physical_adapter, runtime_task_spec, contract.data)
        simulation_result = physical_adapter.run(runtime_task_spec, contract.data)
        summary = dict(simulation_result.summary)
        rows = [dict(row) for row in simulation_result.trace_rows]
        state = _run_state(summary)
        object_state, binding_evidence = _object_state(bundle, summary, rows)
        _enrich_rows(bundle, rows)
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
            "operations_object_ref": str(bundle.operations_object.object_ref),
            "operations_object_sha256": bundle.operations_object.content_sha256,
            "binding_set_sha256": bundle.binding_set.content_sha256,
            "capability_projection_sha256": bundle.projection.content_sha256,
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
                status="complete" if state == RunState.SUCCEEDED else "failed",
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
            state=state,
            summary=summary,
            trace_rows=tuple(rows),
            runtime_metadata=runtime_metadata,
            evidence={
                "execution_port": execution_evidence,
                "model_asset_execution": model_asset_evidence,
                "binding_evidence": binding_evidence,
            },
            artifacts={"dataset": dataset.to_dict()} if dataset is not None else {},
            diagnostics=tuple(
                dict(item) for item in metadata.get(bundle.diagnostics_key, ()) if isinstance(item, Mapping)
            ),
            legacy_compatibility={"enabled": False, "legacy_runner": None},
            native_result=native,
        )


def create_subsystem_execution_adapters() -> tuple[SubsystemGraphExecutionAdapter, ...]:
    return tuple(SubsystemGraphExecutionAdapter(bundle) for bundle in (EPS_BUNDLE, COMM_BUNDLE, THERMAL_BUNDLE, PROPULSION_BUNDLE))


__all__ = ["SubsystemGraphExecutionAdapter", "create_subsystem_execution_adapters"]
