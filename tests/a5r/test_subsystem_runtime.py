from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from sat_sim.task_compiler import compile_task_spec
from sat_sim.task_spec import load_task_spec
from sat_sim.unified_execution import execute_compiled_task, reset_execution_composition_root_for_tests
from sat_sim_model_assets.subsystem_verticals import COMM_BUNDLE, EPS_BUNDLE, PROPULSION_BUNDLE, THERMAL_BUNDLE


def _load(name: str) -> dict:
    return deepcopy(load_task_spec(Path("examples") / name).data)


def _thermal_spec(tmp_path: Path) -> dict:
    return {
        "schema_version": "1.0.0",
        "task": {"id": "a5r_thermal_runtime", "name": "A5R thermal runtime"},
        "simulation": {"level": "subsystem", "subsystem": "thermal", "duration_s": 30.0, "step_s": 5.0, "sample_s": 5.0, "backend": "python"},
        "parameters": {"profile": "demo", "values": {"initial_battery_temp_k": 290.0, "payload_power_w": 20.0}},
        "outputs": {"output_root": str(tmp_path / "thermal"), "qoi": ["thermal.source_native.battery_temp_k"]},
        "assurance": {"parameter_profile": "demo", "allow_proxy": False},
        "model": {"capability_id": THERMAL_BUNDLE.capability_id, "target": {"level": "subsystem", "name": "thermal", "mode": "nominal"}},
    }


def _assert_non_legacy(result, adapter_key: str) -> None:  # noqa: ANN001
    execution = result.runtime_metadata["unified_execution"]
    assets = result.runtime_metadata["model_asset_execution"]
    assert execution["adapter_key"] == adapter_key
    assert execution["legacy_mode"] is False
    assert execution["legacy_bridge_called"] is False
    assert assets["legacy_bridge_called"] is False
    assert assets["model_graph_sha256"]
    assert assets["parameter_set_sha256"]
    assert assets["binding_set_sha256"]
    assert assets["capability_projection_sha256"]


def test_a5r_thermal_runtime_is_non_legacy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import sat_sim.legacy_execution.runner_bridge as bridge

    monkeypatch.setattr(bridge, "execute_legacy_runner", lambda *a, **k: (_ for _ in ()).throw(AssertionError("legacy called")))
    reset_execution_composition_root_for_tests()
    spec = _thermal_spec(tmp_path)
    result = execute_compiled_task(compile_task_spec(spec), task_spec=spec, write_dataset=True)
    _assert_non_legacy(result, THERMAL_BUNDLE.adapter_key)
    assert result.summary["operations_object"]["object_ref"] == "spacecraft.thermal"
    assert result.dataset is not None


@pytest.mark.parametrize(
    ("bundle", "example"),
    (
        (EPS_BUNDLE, "subsystem_eps_unified_native_nominal.yaml"),
        (COMM_BUNDLE, "subsystem_comm_data_unified_native_nominal.yaml"),
        (PROPULSION_BUNDLE, "subsystem_propulsion_unified_native_nominal.yaml"),
    ),
)
def test_a5r_basilisk_subsystem_runtime_is_non_legacy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bundle, example: str) -> None:  # noqa: ANN001
    pytest.importorskip("Basilisk")
    import sat_sim.legacy_execution.runner_bridge as bridge

    monkeypatch.setattr(bridge, "execute_legacy_runner", lambda *a, **k: (_ for _ in ()).throw(AssertionError("legacy called")))
    reset_execution_composition_root_for_tests()
    spec = _load(example)
    if "task" in spec:
        spec["task"]["id"] = f"a5r_{bundle.model_ref.model_id.value.rsplit('.', 1)[-1]}"
        spec["simulation"]["duration_s"] = 6.0
        spec["simulation"]["sample_s"] = min(float(spec["simulation"]["sample_s"]), 1.0)
        spec["outputs"]["output_root"] = str(tmp_path / bundle.projection.projection_id.value)
    else:
        spec["task_id"] = f"a5r_{bundle.model_ref.model_id.value.rsplit('.', 1)[-1]}"
        spec["simulation"]["duration_s"] = min(float(spec["simulation"]["duration_s"]), 3.0)
        spec["outputs"]["output_root"] = str(tmp_path / bundle.projection.projection_id.value)
    result = execute_compiled_task(compile_task_spec(spec), task_spec=spec, write_dataset=True)
    _assert_non_legacy(result, bundle.adapter_key)
    assert result.summary["operations_object"]["object_ref"] == str(bundle.operations_object.object_ref)
    assert result.dataset is not None
