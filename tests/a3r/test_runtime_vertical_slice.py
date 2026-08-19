from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from sat_sim.bsk_engine.unified_native import WholeSpacecraftUnifiedNativeAdapter
from sat_sim.capability_registry import get_capability
from sat_sim.task_compiler import compile_task_spec
from sat_sim.task_models import to_runtime_task_spec
from sat_sim.task_spec import load_task_spec
from sat_sim.unified_execution import execute_compiled_task, reset_execution_composition_root_for_tests


def _short_nominal(tmp_path: Path | None = None) -> dict:
    spec = deepcopy(load_task_spec("examples/whole_spacecraft_unified_native_nominal.yaml").data)
    spec["task_id"] = "a3r_runtime_nominal"
    spec["simulation"].update({"duration_s": 4.0, "step_s": 0.2, "sample_s": 1.0})
    spec["outputs"]["output_root"] = str((tmp_path or Path("runs")) / "a3r_runtime_nominal")
    return spec


def test_a3_runtime_does_not_call_legacy_runner_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    import sat_sim.legacy_execution.runner_bridge as bridge

    def forbidden(*args, **kwargs):
        raise AssertionError("legacy runner bridge was called")

    monkeypatch.setattr(bridge, "execute_legacy_runner", forbidden)
    spec = _short_nominal()
    compiled = compile_task_spec(spec)
    reset_execution_composition_root_for_tests()
    result = execute_compiled_task(compiled, task_spec=spec, write_dataset=False)
    evidence = result.runtime_metadata["model_asset_execution"]
    assert evidence["legacy_bridge_called"] is False
    assert result.runtime_metadata["unified_execution"]["legacy_mode"] is False


def test_new_adapter_is_numerically_identical_to_existing_native_runtime() -> None:
    spec = _short_nominal()
    runtime = to_runtime_task_spec(spec)
    contract = get_capability("whole_spacecraft.unified_native.v1")
    direct = WholeSpacecraftUnifiedNativeAdapter().run(runtime, contract.data)
    compiled = compile_task_spec(spec)
    reset_execution_composition_root_for_tests()
    bound = execute_compiled_task(compiled, task_spec=spec, write_dataset=False)
    assert len(bound.trace_rows) == len(direct.trace_rows)
    for new_row, old_row in zip(bound.trace_rows, direct.trace_rows):
        assert new_row["time_s"] == pytest.approx(old_row["time_s"], abs=1e-12)
        assert new_row["adcs.pointing_error_deg"] == pytest.approx(old_row["adcs.pointing_error_deg"], rel=0, abs=1e-12)
        assert new_row["adcs.rw.speed_rad_s_0"] == pytest.approx(old_row["adcs.rw.speed_rad_s_0"], rel=0, abs=1e-12)
        assert new_row["eps.battery_soc"] == pytest.approx(old_row["eps.battery_soc"], rel=0, abs=1e-12)


def test_object_action_effect_and_evidence_close_the_loop() -> None:
    spec = deepcopy(load_task_spec("examples/whole_spacecraft_attitude_control_a3r_object_calls.yaml").data)
    compiled = compile_task_spec(spec)
    reset_execution_composition_root_for_tests()
    result = execute_compiled_task(compiled, task_spec=spec, write_dataset=False)
    model_evidence = result.runtime_metadata["model_asset_execution"]
    assert model_evidence["resolved_actions"][0]["binding_id"] == "enter-safe-mode"
    assert model_evidence["resolved_effects"][0]["binding_id"] == "inject-rw-jamming"
    assert any(bool(row.get("label.power_safe_mode_engaged")) for row in result.trace_rows)
    assert any(bool(row.get("label.fault_active")) for row in result.trace_rows)
    assert result.summary["operations_object"]["object_ref"] == "spacecraft.attitude_control"
    assert "final_stable" in result.summary["binding_evidence"]


def test_dataset_contains_model_binding_and_parameter_hashes(tmp_path: Path) -> None:
    spec = _short_nominal(tmp_path)
    compiled = compile_task_spec(spec)
    reset_execution_composition_root_for_tests()
    result = execute_compiled_task(compiled, task_spec=spec, write_dataset=True)
    assert result.dataset is not None
    metadata = result.runtime_metadata["model_asset_execution"]
    assert len(metadata["graph_sha256"]) == 64
    assert len(metadata["binding_set_sha256"]) == 64
    assert len(metadata["parameter_set_sha256"]) == 64
    assert len(metadata["capability_projection_sha256"]) == 64
    manifest_text = (Path(result.dataset.output_root) / result.dataset.files["manifest"]).read_text(encoding="utf-8")
    assert metadata["binding_set_sha256"] in manifest_text
