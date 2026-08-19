from __future__ import annotations

from pathlib import Path

import yaml

from sat_sim.capability_registry import get_adapter_for_capability, get_capability
from sat_sim.execution_planner import plan_task_spec
from sat_sim.run_bundle import execute_prepared_run, prepare_run

WHOLE = "whole_spacecraft.unified_native.v1"


def _spec(name: str = "whole_spacecraft_unified_native_propulsion.yaml") -> dict:
    return yaml.safe_load(Path("examples", name).read_text(encoding="utf-8"))


def test_propulsion_is_physically_coupled_into_recommended_whole_runtime() -> None:
    spec = _spec()
    contract = get_capability(WHOLE)
    adapter = get_adapter_for_capability(WHOLE)
    assert not [issue for issue in adapter.validate(spec, contract.data) if issue.severity == "error"]
    result = adapter.run(spec, contract.data)
    assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
    assert result.summary["propulsion_enabled"] is True
    assert result.summary["official_module_count"] == 23
    assert result.summary["project_native_module_count"] == 22
    assert result.summary["external_or_proxy_module_count"] == 5
    assert result.summary["propellant_used_kg"] > 0.0
    assert result.summary["max_total_thrust_n"] > 0.0
    assert result.summary["propulsion_burn_observed"] is True
    assert result.summary["propulsion_permission_observed"] is True
    assert any(row["propulsion.total_thrust_n"] > 0.0 for row in result.trace_rows)
    assert result.trace_rows[-1]["propulsion.fuel_mass_kg"] < result.trace_rows[0]["propulsion.fuel_mass_kg"]
    assert "propulsion_thrusters" in result.metadata["model_source_boundary"]["basilisk_official_native"]
    project_modules = set(result.metadata["model_source_boundary"]["basilisk_project_native"])
    proxy_modules = set(result.metadata["model_source_boundary"]["external_or_proxy"])
    assert "project_propulsion_burn_controller" in project_modules
    assert "project_propulsion_activity_power_bridge" in proxy_modules
    assert "project_heater_status_power_bridge" in proxy_modules
    assert result.summary["resource_feedback_closure_status"] == "PASS"


def test_propulsion_burn_changes_orbit_relative_to_same_no_burn_baseline() -> None:
    burn = _spec()
    baseline = _spec("whole_spacecraft_unified_native_nominal.yaml")
    adapter = get_adapter_for_capability(WHOLE)
    burn_result = adapter.run(burn, get_capability(WHOLE).data)
    base_result = adapter.run(baseline, get_capability(WHOLE).data)
    radius_delta = burn_result.summary["final_orbit_radius_m"] - base_result.summary["final_orbit_radius_m"]
    assert abs(radius_delta) > 0.1
    assert base_result.summary["propulsion_enabled"] is False
    assert base_result.summary["official_module_count"] == 21
    assert base_result.summary["project_native_module_count"] == 21


def test_low_soc_blocks_propulsion_burn_inside_runtime() -> None:
    spec = _spec()
    values = spec["parameters"]["values"]
    values["initial_soc"] = 0.15
    values["propulsion_min_burn_soc"] = 0.95
    result = get_adapter_for_capability(WHOLE).run(spec, get_capability(WHOLE).data)
    assert result.summary["propellant_used_kg"] == 0.0
    assert result.summary["max_total_thrust_n"] == 0.0
    assert result.summary["propulsion_burn_observed"] is False
    assert all(row["propulsion.burn_permitted"] == 0 for row in result.trace_rows)


def test_propulsion_outputs_resolve_through_execution_plan() -> None:
    planning = plan_task_spec(_spec())
    assert planning.ok is True
    fields = {binding.requested_field for binding in planning.resolved_spec.output_bindings}
    assert "propulsion.fuel_mass_kg" in fields
    assert "propulsion.total_thrust_n" in fields
    assert all(binding.binding_status == "resolved" for binding in planning.resolved_spec.output_bindings)


def test_propulsion_whole_run_bundle_is_sealed(tmp_path: Path) -> None:
    spec = _spec()
    prepared = prepare_run(spec, output_root=tmp_path, run_id="whole_propulsion_coupling")
    result = execute_prepared_run(prepared.bundle_root, expected_plan_sha256=prepared.execution_plan_sha256)
    assert result.run_record.status == "SUCCEEDED"
    assert result.validation.result == "PASS"
    assert result.run_record.sealed is True
    assert (Path(result.bundle_root) / "SEALED.json").is_file()


def test_platform_agent_is_required_unless_explicitly_skipped() -> None:
    text = Path("scripts/run_platform_acceptance.py").read_text(encoding="utf-8")
    assert 'parser.add_argument("--skip-vllm", action="store_true")' in text
    assert '"required": False' in text
    assert "if args.skip_vllm" in text
    assert "return 0 if required_failed == 0 else 1" in text


def test_whole_capability_exposes_propulsion_contract() -> None:
    contract = get_capability(WHOLE).data
    parameters = contract["parameters"]
    for key in (
        "propulsion_enabled",
        "propulsion_burn_start_s",
        "propulsion_burn_on_time_s",
        "propulsion_thrust_n",
        "propulsion_isp_s",
        "propulsion_initial_propellant_kg",
        "propulsion_tank_capacity_kg",
        "propulsion_min_burn_soc",
    ):
        assert key in parameters
    outputs = set(contract["outputs"]["trace_fields"])
    assert "propulsion.fuel_mass_kg" in outputs
    assert "propulsion.total_thrust_n" in outputs
