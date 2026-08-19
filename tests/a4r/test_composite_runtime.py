from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from sat_sim.a4r.adapter import CompositeDigitalTwinGraphExecutionAdapter
from sat_sim.a4r.resolution import resolve_composite_digital_twin_execution
from sat_sim.task_compiler import compile_task_spec
from sat_sim.task_spec import TaskSpecError, load_task_spec
from sat_sim.unified_execution import (
    adapter_key_for_compiled,
    build_execution_request,
    execute_compiled_task,
    reset_execution_composition_root_for_tests,
)
from sat_sim_execution import ExecutionRequest
from sat_sim_model_assets.composite_digital_twin import (
    COMPOSITE_ADAPTER_KEY,
    COMPOSITE_IMPLEMENTATION,
    COMPOSITE_SPACECRAFT_REF,
)


def _short_example(name: str, tmp_path: Path | None = None) -> dict:
    spec = deepcopy(load_task_spec(f"examples/{name}").data)
    spec["simulation"]["duration_s"] = 40.0 if "mixed" in name else 20.0
    spec["simulation"]["sample_s"] = 10.0
    spec["task_id"] = f"a4r_{Path(name).stem}"
    spec["outputs"]["output_root"] = str((tmp_path or Path("runs")) / spec["task_id"])
    return spec


def _request_for(spec: dict, *, write_dataset: bool = False) -> ExecutionRequest:
    compiled = compile_task_spec(spec)
    resolution = resolve_composite_digital_twin_execution(spec)
    bound_payload = resolution.bound_graph.to_dict()
    bound_payload["content_sha256"] = resolution.bound_graph.content_sha256
    return ExecutionRequest(
        request_id=f"req_{compiled.spec_hash[:16]}",
        task_id=compiled.task_id,
        execution_plan_sha256=compiled.spec_hash,
        adapter_key=COMPOSITE_ADAPTER_KEY,
        model_ref=COMPOSITE_SPACECRAFT_REF,
        implementation_id=COMPOSITE_IMPLEMENTATION,
        capability_id="whole_spacecraft.composite_digital_twin.v1",
        parameters=dict(resolution.bound_graph.resolved_parameters),
        effects=resolution.bound_graph.resolved_effects,
        requested_outputs=resolution.bound_graph.requested_outputs,
        output_root=compiled.outputs.get("output_root"),
        write_dataset=write_dataset,
        metadata={
            "compiled_task": compiled.to_dict(),
            "canonical_task_spec": dict(spec),
            "resolved_runtime_task_spec": resolution.runtime_task_spec,
            "bound_model_graph": bound_payload,
            "a4r_diagnostics": list(resolution.diagnostics),
        },
        legacy_payload=None,
    )


def test_a4_runtime_does_not_call_legacy_runner_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    import sat_sim.legacy_execution.runner_bridge as bridge

    def forbidden(*args, **kwargs):
        raise AssertionError("legacy runner bridge was called")

    monkeypatch.setattr(bridge, "execute_legacy_runner", forbidden)
    request = _request_for(_short_example("whole_spacecraft_composite_digital_twin_nominal.yaml"))
    result = CompositeDigitalTwinGraphExecutionAdapter().execute(request)

    assert result.state.value == "SUCCEEDED"
    assert result.runtime_metadata["unified_execution"]["adapter_key"] == COMPOSITE_ADAPTER_KEY
    assert result.runtime_metadata["unified_execution"]["legacy_mode"] is False
    assert result.runtime_metadata["unified_execution"]["legacy_bridge_called"] is False
    assert result.runtime_metadata["model_asset_execution"]["legacy_bridge_called"] is False
    assert result.legacy_compatibility["enabled"] is False


def test_a4_fault_runtime_persists_model_asset_effect_evidence() -> None:
    request = _request_for(_short_example("whole_spacecraft_composite_digital_twin_fault.yaml"))
    result = CompositeDigitalTwinGraphExecutionAdapter().execute(request)
    evidence = result.runtime_metadata["model_asset_execution"]

    assert evidence["resolved_effects"][0]["effect_id"] == "payload_instrument_off"
    assert evidence["model_graph_sha256"]
    assert evidence["parameter_set_sha256"]
    assert evidence["binding_set_sha256"]
    assert evidence["capability_projection_sha256"]
    assert result.summary["operations_object"]["object_ref"] == "spacecraft.composite_digital_twin"
    assert any(bool(row.get("label.fault_active")) for row in result.trace_rows)


def test_a4_dataset_contains_model_asset_hashes(tmp_path: Path) -> None:
    request = _request_for(
        _short_example("whole_spacecraft_composite_digital_twin_degradation.yaml", tmp_path),
        write_dataset=True,
    )
    result = CompositeDigitalTwinGraphExecutionAdapter().execute(request)

    assert result.native_result.dataset is not None
    metadata = result.runtime_metadata["model_asset_execution"]
    manifest_text = (
        Path(result.native_result.dataset.output_root) / result.native_result.dataset.files["manifest"]
    ).read_text(encoding="utf-8")
    assert metadata["binding_set_sha256"] in manifest_text
    assert metadata["capability_projection_sha256"] in manifest_text


def test_composite_capability_compiles_to_a4r_adapter_after_cutover() -> None:
    spec = _short_example("whole_spacecraft_composite_digital_twin_nominal.yaml")
    compiled = compile_task_spec(spec)
    request = build_execution_request(compiled, spec, write_dataset=False)

    assert adapter_key_for_compiled(compiled) == COMPOSITE_ADAPTER_KEY
    assert request.adapter_key == COMPOSITE_ADAPTER_KEY
    assert request.legacy_payload is None
    assert request.metadata["legacy_route"] is False
    assert request.metadata["model_projection"]["projection_id"] == "composite-digital-twin-projection-v1"
    assert request.metadata["bound_model_graph"]["root_model_ref"]["model_id"]["value"] == "spacecraft.composite_digital_twin"
    assert request.metadata["bound_model_graph"]["root_model_ref"]["version"] == "1"


def test_unified_entry_executes_composite_nominal_fault_degradation_and_mixed_without_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sat_sim.legacy_execution.runner_bridge as bridge

    def forbidden(*args, **kwargs):
        raise AssertionError("legacy runner bridge was called")

    monkeypatch.setattr(bridge, "execute_legacy_runner", forbidden)
    reset_execution_composition_root_for_tests()
    examples = (
        "whole_spacecraft_composite_digital_twin_nominal.yaml",
        "whole_spacecraft_composite_digital_twin_fault.yaml",
        "whole_spacecraft_composite_digital_twin_degradation.yaml",
        "whole_spacecraft_composite_digital_twin_mixed.yaml",
    )
    for name in examples:
        spec = _short_example(name)
        compiled = compile_task_spec(spec)
        result = execute_compiled_task(compiled, task_spec=spec, write_dataset=False)
        metadata = result.runtime_metadata["unified_execution"]

        assert metadata["adapter_key"] == COMPOSITE_ADAPTER_KEY
        assert metadata["legacy_mode"] is False
        assert metadata["legacy_bridge_called"] is False
        assert result.runtime_metadata["model_asset_execution"]["legacy_bridge_called"] is False
        assert result.summary["effect_execution_status"] == "PASS"


def test_unbound_composite_effect_is_rejected_before_runtime() -> None:
    spec = _short_example("whole_spacecraft_composite_digital_twin_nominal.yaml")
    spec["target"]["mode"] = "fault"
    spec["modifiers"]["faults"] = [{
        "modifier_id": "unknown_001",
        "target": "payload.instrument",
        "fault_type": "instrument_off",
        "onset_time_s": 1.0,
        "duration_s": -1.0,
        "severity": 1.0,
        "parameters": {},
    }]
    compiled = compile_task_spec(spec)

    with pytest.raises(TaskSpecError, match="A4R_EFFECT_NOT_BOUND"):
        build_execution_request(compiled, spec, write_dataset=False)


def test_unknown_effect_fail_closed_before_adapter_and_records_error_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called = False

    def forbidden_execute(self, request):  # noqa: ANN001
        nonlocal called
        called = True
        raise AssertionError("composite adapter must not be called for unknown effects")

    monkeypatch.setattr(CompositeDigitalTwinGraphExecutionAdapter, "execute", forbidden_execute)
    spec = _short_example("whole_spacecraft_composite_digital_twin_nominal.yaml", tmp_path)
    spec["target"]["mode"] = "fault"
    spec["modifiers"]["faults"] = [{
        "modifier_id": "unknown_001",
        "target": "payload.instrument",
        "fault_type": "instrument_off",
        "onset_time_s": 1.0,
        "duration_s": -1.0,
        "severity": 1.0,
        "parameters": {},
    }]
    compiled = compile_task_spec(spec)

    with pytest.raises(TaskSpecError) as exc_info:
        execute_compiled_task(compiled, task_spec=spec, write_dataset=True)

    evidence = {
        "schema_version": "sat-sim.a4r-dc2.unknown-effect-fail-closed.v1",
        "ok": True,
        "phase": "binding_resolution",
        "error_code": "A4R_EFFECT_NOT_BOUND",
        "exception_type": type(exc_info.value).__name__,
        "exception_message": str(exc_info.value),
        "adapter_key": COMPOSITE_ADAPTER_KEY,
        "adapter_called": called,
        "run_result_created": False,
        "nominal_fallback": False,
    }
    assert "A4R_EFFECT_NOT_BOUND" in evidence["exception_message"]
    assert called is False
    evidence_path = tmp_path / "unknown_effect_fail_closed_evidence.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert evidence_path.is_file()


@pytest.mark.parametrize("key", ["status", "effect_execution_status", "validation_status", "overall_status"])
@pytest.mark.parametrize("value", ["FAIL", "FAILED", "ERROR", "INCONCLUSIVE"])
def test_a4_runtime_state_treats_non_success_summary_status_as_failed(key: str, value: str) -> None:
    state = CompositeDigitalTwinGraphExecutionAdapter._run_state_from_summary({key: value})

    assert state.value == "FAILED"
