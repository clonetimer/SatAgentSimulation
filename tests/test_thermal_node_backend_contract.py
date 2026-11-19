"""Backend projection preserves the thermal reference model and isolates the official plate."""
from copy import deepcopy

import pytest

from sat_sim.adapters.component_source_native_misc import ThermalNodeAdapter
from sat_sim.capability_backends import get_task_capability, select_capability_backend, validate_backend_output_requests
from sat_sim.capability_registry import get_capability


CID = "component.thermal_node.v1"
PREFIX = "thermal.node."


def test_declared_default_profile_preserves_reference_behavior_and_parameter_bounds():
    base = get_capability(CID)
    snapshot = deepcopy(base.data)
    reference = select_capability_backend(base)
    assert set(reference.data["parameters"]) == {
        "physics_backend", "ambient_k", "tau_s", "heat_gain_k_per_w", "initial_temp_k", "power_w", "power_profile_w",
    }
    assert reference.data["parameters"]["initial_temp_k"]["min"] == 0
    assert "max" not in reference.data["parameters"]["power_w"]
    assert reference.data.get("selected_physics_backend") is None
    assert "fault" in reference.data["modes"]
    assert reference.data["outputs"] == base.data["outputs"]
    assert reference.data["source_binding"]["primary_module"] == "components.thermal_node.builder"
    assert reference.data["backend_boundary"]["current_agent_path_uses_basilisk"] is False
    assert select_capability_backend(base, "project_reference") == reference
    assert base.data == snapshot


def test_native_profile_limits_fields_and_sources_to_official_plate():
    base = get_capability(CID)
    snapshot = deepcopy(base.data)
    native = select_capability_backend(base, "basilisk_native").data
    assert set(native["parameters"]) == {
        "physics_backend", "initial_temp_k", "power_w", "surface_area_m2", "mass_kg", "specific_heat_j_per_kg_k",
        "absorptivity", "emissivity", "normal_b", "sun_direction_n", "illumination_factor",
    }
    assert native["parameters"]["initial_temp_k"]["min"] == 1
    assert native["parameters"]["initial_temp_k"]["max"] == 2000
    assert native["parameters"]["power_w"]["max"] == 1_000_000
    assert native["selected_physics_backend"] == "basilisk_native"
    assert {field["name"] for field in native["outputs"]["trace"]} == {
        "time_s", "sample_index", "label.health_state", PREFIX + "temp_c", PREFIX + "temp_k", PREFIX + "input_power_w",
    }
    assert {field["name"] for field in native["outputs"]["summary"]} == {
        PREFIX + suffix for suffix in ("initial_temp_k", "final_temp_k", "min_temp_k", "max_temp_k", "delta_temp_k")
    }
    assert set(native["modes"]) == {"nominal"}
    assert native["source_binding"]["primary_module"] == "Basilisk.simulation.sensorThermal"
    assert native["source_binding"]["basilisk_required"]
    assert native["backend_boundary"]["current_agent_path_uses_basilisk"] is True
    assert native["backend_boundary"]["can_claim_high_fidelity"] is False
    assert native["integration_contract"]["implementation_class"] == "official_basilisk_native"
    assert "parameters.ambient_k" not in {field["name"] for field in native["consumes"]}
    assert PREFIX + "power_w" not in {field["name"] for field in native["produces"]}
    assert base.data == snapshot


def test_thermal_backend_profiles_cite_their_own_conformance_tests():
    base = get_capability(CID)
    reference = select_capability_backend(base)
    native = select_capability_backend(base, "basilisk_native")
    assert set(reference.data["validation"]["conformance_tests"]) == {
        "test_c2c3_thermal_node_parameter_effect_and_manifest",
        "test_c2c3_thermal_node_capability_python_export_runs",
    }
    assert set(native.data["validation"]["conformance_tests"]) == {
        "test_native_export_matches_official_temperature_recorder",
        "test_native_exact_fractional_grid_and_time_zero_celsius",
        "test_stiff_fixture_accepts_safe_step_and_converges_with_refinement",
    }


@pytest.mark.parametrize("overrides", [[], {"ambient_k": {"min": 270}}, {"power_w": 100}])
def test_backend_parameter_overrides_require_mappings_for_selected_parameters(overrides):
    base = get_capability(CID)
    data = deepcopy(base.data)
    data["backend_profiles"]["basilisk_native"]["parameter_overrides"] = overrides
    contract = type(base)(base.capability_id, data, base.path)
    with pytest.raises(ValueError, match="parameter overrides"):
        select_capability_backend(contract, "basilisk_native")


@pytest.mark.parametrize("backend", [{}, {"physics_backend": "project_reference"}])
def test_reference_still_runs_first_order_fault_with_reference_outputs(backend):
    spec = {
        "schema_version": "0.1.0", "task_id": "reference-thermal", "task_type": "component", "capability_id": CID,
        "target": {"level": "component", "name": "thermal_node", "mode": "fault"},
        "simulation": {"duration_s": 2.0, "sample_s": 1.0},
        "parameters": {**backend, "ambient_k": 280, "tau_s": 2, "initial_temp_k": 280, "power_profile_w": [10, 12]},
        "faults": [{"fault_type": "sensor_bias", "start_s": 0, "end_s": 2, "severity": 0.5}],
        "outputs": {"plots": [PREFIX + "measured_temp_k"], "qoi": ["qoi.*"]},
    }
    contract = get_task_capability(spec)
    assert not validate_backend_output_requests(spec, contract)
    adapter = ThermalNodeAdapter()
    assert not adapter.validate(spec, contract.data)
    result = adapter.run(spec, contract.data)
    assert result.summary.get("physics_backend") == "project_reference"
    assert result.metadata["physics_backend"] == "project_reference"
    assert result.labels["run_labels"][0]["physics_backend"] == "project_reference"
    assert result.trace_rows[0][PREFIX + "measured_temp_k"] == 290
    assert result.trace_rows[1][PREFIX + "temp_k"] == 285
    assert result.trace_rows[1][PREFIX + "power_w"] == 10
    assert result.trace_rows[1][PREFIX + "effective_tau_s"] == 2
