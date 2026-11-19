"""Legacy native FuelTank requests retain explicit inputs through migration."""
from __future__ import annotations

import pytest

from sat_sim.adapters import component_fuel_tank_native as native
from sat_sim.adapters.component_source_native_misc import FuelTankAdapter
from sat_sim.execution_planner import plan_task_spec
from sat_sim.task_models import CanonicalTaskSpec, canonicalize_task_spec


PREFIX = "propulsion.fuel_tank."


@pytest.fixture
def legacy_spec():
    return {
        "schema_version": "0.1.0",
        "task_id": "legacy-native-fuel-tank",
        "task_type": "component",
        "capability_id": "component.fuel_tank.v1",
        "target": {"level": "component", "name": "fuel_tank", "mode": "nominal"},
        "simulation": {"duration_s": 20.0, "sample_s": 10.0, "step_s": 1.0, "solver": {}},
        "parameters": {"physics_backend": "basilisk_native", "mass_flow_kg_s": 0.0},
    }


def _assert_direct_rejection(spec, path, monkeypatch):
    monkeypatch.setattr(native, "_execute", lambda case: pytest.fail("invalid input reached the native graph"))
    adapter = FuelTankAdapter()
    issues = adapter.validate(spec)
    assert any(issue.severity == "error" and issue.path == path for issue in issues)
    with pytest.raises(ValueError):
        adapter.run(spec)


def _set_time(spec, key, value):
    simulation = spec["simulation"]
    if key == "solver.step_s":
        simulation.setdefault("solver", {})["step_s"] = value
    else:
        simulation[key] = value


@pytest.mark.parametrize("key,value", [
    ("duration_s", 0), ("sample_s", 0), ("step_s", None), ("solver.step_s", None),
    ("duration_s", "20"), ("step_s", True),
])
def test_flat_native_migration_rejects_explicit_invalid_times(legacy_spec, key, value, monkeypatch):
    _set_time(legacy_spec, key, value)
    path = f"$.simulation.{key}"
    _assert_direct_rejection(legacy_spec, path, monkeypatch)
    planning = plan_task_spec(legacy_spec)
    assert not planning.ok
    assert any(path in issue.message or path == issue.path for issue in planning.validation.errors)


@pytest.mark.parametrize("key", ["duration_s", "sample_s", "step_s", "solver.step_s"])
def test_canonical_native_timing_rejects_boolean_before_float_coercion(legacy_spec, key, monkeypatch):
    legacy_spec["simulation"].update(duration_s=1.0, sample_s=1.0, step_s=1.0)
    canonical = canonicalize_task_spec(legacy_spec)
    _set_time(legacy_spec, key, True)
    _set_time(canonical, key, True)
    path = f"$.simulation.{key}"
    _assert_direct_rejection(legacy_spec, path, monkeypatch)
    planning = plan_task_spec(canonical)
    assert not planning.ok
    assert any(path in issue.message or path == issue.path for issue in planning.validation.errors)


def test_canonical_native_accepts_already_validated_simulation_model(legacy_spec):
    canonical = canonicalize_task_spec(legacy_spec)
    parsed = CanonicalTaskSpec.model_validate(canonical)
    assert CanonicalTaskSpec.model_validate({**canonical, "simulation": parsed.simulation}) == parsed


def test_canonical_native_timing_guard_applies_with_typed_model_binding(legacy_spec):
    canonical = canonicalize_task_spec(legacy_spec)
    parsed = CanonicalTaskSpec.model_validate(canonical)
    simulation = {**canonical["simulation"], "step_s": True}
    with pytest.raises(ValueError, match=r"\$\.simulation\.step_s"):
        CanonicalTaskSpec.model_validate({
            **canonical, "simulation": simulation, "model": parsed.model, "parameters": parsed.parameters,
        })


@pytest.mark.parametrize("invalid_alias,valid_alias", [("qoi", "record_fields"), ("record_fields", "qoi")])
def test_native_legacy_output_alias_never_hides_pressure(legacy_spec, invalid_alias, valid_alias, monkeypatch):
    legacy_spec["outputs"] = {invalid_alias: [PREFIX + "final_pressure_pa"], valid_alias: [PREFIX + "final_mass_kg"]}
    _assert_direct_rejection(legacy_spec, f"$.outputs.{invalid_alias}[0]", monkeypatch)
    planning = plan_task_spec(legacy_spec)
    assert not planning.ok
    assert any("final_pressure_pa" in issue.message for issue in planning.validation.errors)


@pytest.mark.parametrize("outer,nested_backend,bad_key", [
    ({"full_pressure_pa": 2.5e6}, "basilisk_native", "full_pressure_pa"),
    ({"physics_backend": "project_reference"}, "basilisk_native", "physics_backend"),
    ({"physics_backend": "basilisk_native"}, "project_reference", "physics_backend"),
])
def test_native_legacy_parameter_envelope_rejects_mixed_device_values(legacy_spec, outer, nested_backend, bad_key):
    legacy_spec["parameters"] = {"values": {"physics_backend": nested_backend, "mass_flow_kg_s": 0.0}, **outer}
    planning = plan_task_spec(legacy_spec)
    assert not planning.ok
    assert any(bad_key in issue.message for issue in planning.validation.errors)


@pytest.mark.parametrize("key", ["events", "modifiers"])
def test_native_rejects_unknown_nonempty_event_envelope(legacy_spec, key, monkeypatch):
    legacy_spec[key] = {"transients": [{"effect": "leak"}]}
    _assert_direct_rejection(legacy_spec, f"$.{key}.transients", monkeypatch)
    assert not plan_task_spec(legacy_spec).ok


def test_native_legacy_preserves_compatible_nested_parameters_and_output_precedence(legacy_spec):
    legacy_spec["parameters"] = {"values": legacy_spec["parameters"], "profile": "demo", "overrides": []}
    legacy_spec["outputs"] = {"qoi": [PREFIX + "initial_mass_kg"], "record_fields": [PREFIX + "final_mass_kg"]}
    canonical = canonicalize_task_spec(legacy_spec)
    assert canonical["parameters"]["values"] == {"physics_backend": "basilisk_native", "mass_flow_kg_s": 0.0}
    assert canonical["outputs"]["qoi"] == [PREFIX + "final_mass_kg"]


def test_reference_legacy_time_and_output_compatibility_is_preserved(legacy_spec):
    legacy_spec["parameters"]["physics_backend"] = "project_reference"
    legacy_spec["simulation"].update(duration_s=0, sample_s=0, step_s=None)
    legacy_spec["outputs"] = {"qoi": [PREFIX + "final_mass_kg"], "record_fields": [PREFIX + "final_pressure_pa"]}
    canonical = canonicalize_task_spec(legacy_spec)
    assert canonical["simulation"]["duration_s"] == 300.0
    assert canonical["simulation"]["sample_s"] == canonical["simulation"]["step_s"] == 10.0
    assert canonical["outputs"]["qoi"] == [PREFIX + "final_pressure_pa"]
