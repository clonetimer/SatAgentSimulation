"""Acceptance of the opt-in native single-thruster component."""
from __future__ import annotations

import pytest

from sat_sim.adapters.component_source_native_misc import ThrusterAdapter


PREFIX = "propulsion.thruster."


def thruster_spec(**parameters):
    return {
        "schema_version": "0.1.0",
        "task_id": "native-thruster-component",
        "task_type": "component",
        "description": "Delayed component thruster pulse",
        "capability_id": "component.thruster.v1",
        "target": {"level": "component", "name": "thruster", "mode": "nominal"},
        "simulation": {"duration_s": 1.0, "sample_s": 0.1},
        "parameters": {
            "physics_backend": "basilisk_native",
            "native_step_s": 0.005,
            "on_time_profile_s": [0, 0.1, 0.1, 0.1, 0.1, 0.1, 0],
            **parameters,
        },
    }


def test_delayed_native_burn_records_ignition_and_shutdown():
    pytest.importorskip("Basilisk")
    result = ThrusterAdapter().run(thruster_spec())
    assert result.metadata["physics_backend"] == "basilisk_native"
    rows = result.trace_rows
    assert rows[0][PREFIX + "effective_thrust_n"] == 0.0
    assert rows[3][PREFIX + "effective_thrust_n"] == pytest.approx(1.0)
    assert rows[-1][PREFIX + "effective_thrust_n"] == 0.0
    assert result.summary["qoi"][PREFIX + "total_impulse_ns"] == pytest.approx(0.5, abs=0.005)


@pytest.mark.parametrize("thrust,isp", [(1.0, 280.0), (2.0, 280.0), (1.0, 140.0)])
def test_force_and_tank_response_to_configuration(thrust, isp):
    pytest.importorskip("Basilisk")
    result = ThrusterAdapter().run(thruster_spec(thrust_n=thrust, isp_s=isp))
    assert [row["time_s"] for row in result.trace_rows] == pytest.approx([i / 10 for i in range(11)])
    assert result.trace_rows[4][PREFIX + "effective_thrust_n"] == pytest.approx(thrust)
    assert result.trace_rows[4][PREFIX + "effective_isp_s"] == isp
    assert [row[PREFIX + "on_time_s"] for row in result.trace_rows] == [0, 0, *([0.1] * 5), *([0] * 4)]
    qoi = result.summary["qoi"]
    impulse = qoi[PREFIX + "total_impulse_ns"]
    fuel = qoi[PREFIX + "propellant_used_kg"]
    assert impulse == pytest.approx(thrust * 0.5, abs=thrust * 0.005)
    assert fuel * isp * 9.80665 == pytest.approx(impulse, abs=thrust * 0.005)
    assert result.trace_rows[-1][PREFIX + "cumulative_propellant_kg"] == fuel


@pytest.mark.parametrize("commands", [[0], [0, 0.005, 0]])
def test_subsample_one_step_burn_and_zero_control(commands):
    pytest.importorskip("Basilisk")
    result = ThrusterAdapter().run(thruster_spec(on_time_profile_s=commands))
    # The pulse lies entirely between output times. A sparse force integral
    # cannot distinguish this case from the zero control.
    assert all(row[PREFIX + "effective_thrust_n"] == 0 for row in result.trace_rows)
    qoi = result.summary["qoi"]
    impulse = qoi[PREFIX + "total_impulse_ns"]
    fuel = qoi[PREFIX + "propellant_used_kg"]
    assert impulse == pytest.approx(sum(commands), abs=1e-12)
    if any(commands):
        assert fuel > 0
        assert fuel * 280 * 9.80665 == pytest.approx(impulse, abs=0.005)
        assert result.trace_rows[1][PREFIX + "cumulative_impulse_ns"] == 0
        assert result.trace_rows[2][PREFIX + "cumulative_impulse_ns"] == pytest.approx(0.005)
    else:
        assert fuel == 0


def test_fractional_grid_holds_last_full_period_and_includes_endpoint():
    pytest.importorskip("Basilisk")
    spec = thruster_spec(native_step_s=0.001, on_time_profile_s=[0, 0.101])
    spec["simulation"] = {"duration_s": 1.0100000001, "sample_s": 0.1010000001}
    result = ThrusterAdapter().run(spec)
    assert len(result.trace_rows) == 11
    assert result.trace_rows[-1]["time_s"] == 1.01
    assert result.metadata["burn_start_s"] == 0.101
    assert result.metadata["burn_duration_s"] == 0.909
    assert result.summary["qoi"][PREFIX + "total_impulse_ns"] == pytest.approx(0.909, abs=0.001)


@pytest.mark.parametrize("params", [
    {"on_time_profile_s": []}, {"on_time_profile_s": [0] * 11},
    {"on_time_profile_s": [0.1, 0, 0.1]}, {"on_time_profile_s": [0.05, 0.1, 0]},
    {"on_time_profile_s": [0.05]}, {"on_time_profile_s": [0.101]},
    {"on_time_profile_s": [0.003, 0]}, {"on_time_profile_s": ["0.1"]},
    {"on_time_profile_s": [float("nan")]}, {"on_time_profile_s": [-0.1]},
    {"on_time_profile_s": [1e-12]}, {"on_time_s": float("nan")},
    {"thrust_n": -1}, {"thrust_n": float("inf")}, {"thrust_n": 1e308},
    {"thrust_n": 1e200, "isp_s": 1e200}, {"isp_s": 1e308},
    {"isp_s": 0.0}, {"isp_s": False}, {"isp_s": float("nan")},
    {"native_step_s": 0}, {"native_step_s": 1e-9}, {"native_step_s": 0.003},
    {"native_step_s": 1e308}, {"native_step_s": True},
])
def test_native_invalid_parameters_fail_preflight_and_run(params):
    adapter = ThrusterAdapter()
    spec = thruster_spec(**params)
    issues = adapter.validate(spec)
    assert issues and all(issue.severity == "error" for issue in issues)
    with pytest.raises(ValueError, match=issues[0].message):
        adapter.run(spec)


@pytest.mark.parametrize("simulation", [
    {"duration_s": 1.01, "sample_s": 0.1},
    {"duration_s": 1.0, "sample_s": 0.1000000009},
    {"duration_s": float("nan"), "sample_s": 0.1},
    {"duration_s": 1e308, "sample_s": 1e308},
])
def test_invalid_time_grids_fail_preflight_and_run(simulation):
    spec = thruster_spec()
    spec["simulation"] = simulation
    assert ThrusterAdapter().validate(spec)
    with pytest.raises(ValueError):
        ThrusterAdapter().run(spec)


@pytest.mark.parametrize("event", ["faults", "degradations", "constraints"])
@pytest.mark.parametrize("nested", [True, False])
def test_native_rejects_events_even_in_nominal_mode(event, nested):
    spec = thruster_spec()
    container = spec.setdefault("modifiers", {}) if nested else spec
    container[event] = [{"type": "ignored_event"}]
    assert ThrusterAdapter().validate(spec)
    with pytest.raises(ValueError, match="no events"):
        ThrusterAdapter().run(spec)


def test_native_rejects_non_nominal_mode_and_unknown_backend():
    spec = thruster_spec()
    spec["target"]["mode"] = "fault"
    assert ThrusterAdapter().validate(spec)
    with pytest.raises(ValueError, match="nominal"):
        ThrusterAdapter().run(spec)
    spec["parameters"]["physics_backend"] = "unknown"
    assert ThrusterAdapter().validate(spec)
    with pytest.raises(ValueError, match="unknown"):
        ThrusterAdapter().run(spec)


def test_native_recorder_rejects_missing_corrupt_or_misaligned_data():
    from types import SimpleNamespace

    import numpy as np

    from sat_sim.adapters.component_thruster_native import _recording

    grid = np.array([0, 5, 10])
    for recorders in [
        {}, {"thruster": SimpleNamespace(times=lambda: grid)},
        {"thruster": SimpleNamespace(times=lambda: [0, 5, 9], thrustForce=[0, 1, 0])},
        {"thruster": SimpleNamespace(times=lambda: grid, thrustForce=[0, 1])},
        {"thruster": SimpleNamespace(times=lambda: grid, thrustForce=[0, float("nan"), 0])},
    ]:
        with pytest.raises(RuntimeError, match="native recorder"):
            _recording(recorders, "thruster", "thrustForce", grid)


def test_reference_path_remains_available_by_default_and_explicit_selection():
    spec = thruster_spec(on_time_profile_s=[0.02])
    del spec["parameters"]["physics_backend"]
    adapter = ThrusterAdapter()
    default = adapter.run(spec)
    spec["parameters"]["physics_backend"] = "project_reference"
    assert adapter.run(spec) == default
    assert default.summary["qoi"][PREFIX + "total_impulse_ns"] == pytest.approx(0.2)


def test_canonical_native_example_runs_through_sealed_bundle(tmp_path):
    pytest.importorskip("Basilisk")
    from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle

    prepared = prepare_run(_canonical_thruster_example(), output_root=tmp_path)
    result = execute_prepared_run(prepared.bundle_root, expected_plan_sha256=prepared.execution_plan_sha256)
    assert result.run_record.status.value == "SUCCEEDED", result.run_record
    assert result.run_record.sealed and result.validation.result.value == "PASS", result.validation
    assert verify_run_bundle(prepared.bundle_root)["ok"]
    metadata = result.summary["adapter_metadata"]
    assert metadata["physics_backend"] == "basilisk_native"
    assert "ThrusterDynamicEffector" in metadata["native_provenance"]
    assert metadata["native_recorder_samples"] == 201
    assert metadata["burn_start_s"] == 0.1 and metadata["burn_duration_s"] == 0.5
    assert metadata["native_fixture"]["initial_propellant_kg"] == 1.0
    assert "THROutputMsg.thrustForce" in metadata["field_sources"][PREFIX + "effective_thrust_n"]
    assert "FuelTankMsg.fuelMass" in metadata["field_sources"][PREFIX + "cumulative_propellant_kg"]
    assert result.summary["qoi"][PREFIX + "total_impulse_ns"] == pytest.approx(0.5, abs=0.005)
    assert result.trace_rows[-1][PREFIX + "cumulative_propellant_kg"] > 0


def test_missing_native_dependency_fails_validation_and_execution(monkeypatch):
    from subsystems.propulsion import builder

    def unavailable():
        raise RuntimeError("Basilisk propulsion modules are unavailable")

    monkeypatch.setattr(builder, "_require_propulsion_basilisk", unavailable)
    spec = thruster_spec()
    issues = ThrusterAdapter().validate(spec)
    assert issues[0].code == "native_thruster_dependency"
    with pytest.raises(RuntimeError, match="unavailable"):
        ThrusterAdapter().run(spec)


@pytest.mark.parametrize("thrust,profile", [
    (6000.0, [0, 0.1, 0.1, 0.1, 0.1, 0.1, 0]),
    (0.95 * 280 * 9.80665 / 0.005, [0, 0.005, 0]),
])
def test_fuel_exhaustion_and_native_stage_reserve_are_checked_before_build(thrust, profile, monkeypatch):
    from sat_sim.adapters import component_thruster_native as native

    monkeypatch.setattr(native, "_execute", lambda case: pytest.fail("unexpected native build"))
    spec = thruster_spec(thrust_n=thrust, on_time_profile_s=profile)
    issues = ThrusterAdapter().validate(spec)
    assert issues and "exhaust" in issues[0].message
    with pytest.raises(ValueError, match="exhaust"):
        ThrusterAdapter().run(spec)


@pytest.mark.parametrize("draw,valid", [(2.5e-6, True), (0.0, False), (4e-6, False)])
def test_qois_follow_dense_recorded_values_and_enforce_conservation(monkeypatch, draw, valid):
    from types import SimpleNamespace

    import numpy as np

    from sat_sim.adapters import component_thruster_native as native

    spec = thruster_spec(on_time_profile_s=[0, 0.005, 0])
    times = np.arange(201, dtype=np.int64) * 5_000_000
    force = np.zeros(201)
    force[21] = 0.75
    mass = np.ones(201)
    mass[21:] -= draw
    records = {
        "thruster_0": SimpleNamespace(times=lambda: times, thrustForce=force),
        "fuel_tank": SimpleNamespace(times=lambda: times, fuelMass=mass),
    }
    monkeypatch.setattr(native, "_execute", lambda case: SimpleNamespace(recorders=records))
    if not valid:
        with pytest.raises(RuntimeError, match="conservation"):
            ThrusterAdapter().run(spec)
        return
    result = ThrusterAdapter().run(spec)
    assert result.summary["qoi"][PREFIX + "total_impulse_ns"] == pytest.approx(0.75 * 0.005)
    assert result.summary["qoi"][PREFIX + "propellant_used_kg"] == pytest.approx(draw)


@pytest.mark.parametrize("thrust,step", [(1e-10, 0.001), (1e-9, 0.0001)])
def test_unresolved_fuel_flow_is_rejected_before_native_build(thrust, step, monkeypatch):
    from sat_sim.adapters import component_thruster_native as native

    monkeypatch.setattr(native, "_execute", lambda case: pytest.fail("unexpected native build"))
    spec = thruster_spec(thrust_n=thrust, native_step_s=step)
    issues = ThrusterAdapter().validate(spec)
    assert issues and "resolution" in issues[0].message
    with pytest.raises(ValueError, match="resolution"):
        ThrusterAdapter().run(spec)


@pytest.mark.parametrize("path,bad_value", [
    (("capability_id",), "component.fuel_tank.v1"), (("task_type",), "subsystem"),
    (("target", "level"), "subsystem"), (("target", "name"), "fuel_tank"),
])
def test_native_identity_is_checked_before_build(path, bad_value, monkeypatch):
    from sat_sim.adapters import component_thruster_native as native

    monkeypatch.setattr(native, "_execute", lambda case: pytest.fail("unexpected native build"))
    spec = thruster_spec(on_time_profile_s=[0])
    container = spec
    for key in path[:-1]:
        container = container[key]
    container[path[-1]] = bad_value
    issues = ThrusterAdapter().validate(spec)
    assert issues
    with pytest.raises(ValueError) as exc:
        ThrusterAdapter().run(spec)
    assert issues[0].message in str(exc.value)


def test_low_positive_flow_just_above_resolution_floor_conserves_fuel():
    import math

    pytest.importorskip("Basilisk")
    step = 0.001
    burn_steps = 500
    rounding_budget = 2 * (burn_steps + 2) * math.ulp(1.0)
    step_fuel = 1.01 * rounding_budget
    thrust = step_fuel * (280 * 9.80665) / step
    spec = thruster_spec(thrust_n=thrust, native_step_s=step)
    assert not ThrusterAdapter().validate(spec)
    qoi = ThrusterAdapter().run(spec).summary["qoi"]
    used = qoi[PREFIX + "propellant_used_kg"]
    equivalent = qoi[PREFIX + "total_impulse_ns"] / (280 * 9.80665)
    assert used > 0 and equivalent > 0
    assert abs(used - equivalent) <= step_fuel + rounding_budget


@pytest.mark.parametrize("thrust,profile", [(0.0, [0.1]), (1e-20, [0.0])])
def test_zero_force_controls_remain_valid_below_tank_resolution(thrust, profile):
    pytest.importorskip("Basilisk")
    spec = thruster_spec(thrust_n=thrust, on_time_profile_s=profile)
    assert not ThrusterAdapter().validate(spec)
    qoi = ThrusterAdapter().run(spec).summary["qoi"]
    assert qoi[PREFIX + "propellant_used_kg"] == 0.0
    assert qoi[PREFIX + "total_impulse_ns"] == 0.0


def _canonical_thruster_example():
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[1] / "examples" / "component_thruster_basilisk_native.yaml"
    return yaml.safe_load(path.read_text())


@pytest.mark.parametrize("standard_step", [0.02, None])
def test_canonical_conflicting_or_inferred_step_fails_planning_and_before_build(standard_step, monkeypatch):
    from sat_sim.adapters import component_thruster_native as native
    from sat_sim.execution_planner import plan_task_spec
    from sat_sim.task_models import to_runtime_task_spec

    spec = _canonical_thruster_example()
    if standard_step is None:
        del spec["simulation"]["step_s"]
    else:
        spec["simulation"]["step_s"] = standard_step
    runtime = to_runtime_task_spec(spec)
    assert runtime["simulation"]["solver"]["step_s"] == (standard_step or 0.1)
    planning = plan_task_spec(spec)
    assert not planning.ok
    assert any("native_step_s" in issue.message for issue in planning.validation.errors)
    assert ThrusterAdapter().validate(runtime)
    monkeypatch.setattr(native, "_execute", lambda case: pytest.fail("unexpected native build"))
    with pytest.raises(ValueError, match="native_step_s"):
        ThrusterAdapter().run(runtime)


def test_flat_simulation_step_must_also_agree_with_native_step(monkeypatch):
    from sat_sim.adapters import component_thruster_native as native

    spec = thruster_spec()
    spec["simulation"].update(step_s=0.02, solver={"step_s": 0.005})
    monkeypatch.setattr(native, "_execute", lambda case: pytest.fail("unexpected native build"))
    assert ThrusterAdapter().validate(spec)
    with pytest.raises(ValueError, match="native_step_s"):
        ThrusterAdapter().run(spec)


@pytest.mark.parametrize("step,duration,periods_on", [
    (4342463.3232458765, 4342463.3232458765, 0),  # task period changes by 1 ns
    (104878.048780502, 4300000.000000582, 40),  # accumulated burn changes by 1 ns
])
def test_native_times_must_round_trip_through_builder_seconds(step, duration, periods_on, monkeypatch):
    from sat_sim.adapters import component_thruster_native as native

    spec = thruster_spec(native_step_s=step, thrust_n=0, on_time_profile_s=[step] * periods_on + [0])
    spec["simulation"] = {"duration_s": duration, "sample_s": step}
    monkeypatch.setattr(native, "_execute", lambda case: pytest.fail("unexpected native build"))
    issues = ThrusterAdapter().validate(spec)
    assert issues and "round-trip" in issues[0].message
    with pytest.raises(ValueError, match="round-trip"):
        ThrusterAdapter().run(spec)


@pytest.mark.parametrize("standard_step,native_step", [(0.0200000001, 0.02), (None, 0.1)])
def test_canonical_matching_steps_run_on_normalized_native_grid(standard_step, native_step):
    pytest.importorskip("Basilisk")
    from sat_sim.execution_planner import plan_task_spec
    from sat_sim.task_models import to_runtime_task_spec

    spec = _canonical_thruster_example()
    if standard_step is None:
        del spec["simulation"]["step_s"]
    else:
        spec["simulation"]["step_s"] = standard_step
    spec["parameters"]["values"].update(native_step_s=native_step, on_time_profile_s=[0, native_step, 0])
    assert plan_task_spec(spec).ok
    runtime = to_runtime_task_spec(spec)
    assert not ThrusterAdapter().validate(runtime)
    result = ThrusterAdapter().run(runtime)
    assert result.metadata["native_step_s"] == native_step
    assert result.metadata["native_recorder_samples"] == round(1 / native_step) + 1
    assert result.summary["qoi"][PREFIX + "total_impulse_ns"] == pytest.approx(native_step, abs=1e-12)
    assert result.summary["qoi"][PREFIX + "propellant_used_kg"] > 0


def test_flat_spec_without_solver_or_native_step_uses_native_default():
    pytest.importorskip("Basilisk")
    spec = thruster_spec()
    del spec["parameters"]["native_step_s"]
    result = ThrusterAdapter().run(spec)
    assert result.metadata["native_step_s"] == 0.01
    assert result.metadata["native_recorder_samples"] == 101


def test_canonical_top_level_step_survives_an_explicit_solver_step(monkeypatch):
    from sat_sim.adapters import component_thruster_native as native
    from sat_sim.execution_planner import plan_task_spec
    from sat_sim.task_models import to_runtime_task_spec

    spec = _canonical_thruster_example()
    spec["simulation"].update(step_s=0.02, solver={"step_s": 0.005})
    runtime = to_runtime_task_spec(spec)
    assert runtime["simulation"]["step_s"] == 0.02
    assert runtime["simulation"]["solver"]["step_s"] == 0.005
    assert not plan_task_spec(spec).ok
    assert ThrusterAdapter().validate(runtime)
    monkeypatch.setattr(native, "_execute", lambda case: pytest.fail("unexpected native build"))
    with pytest.raises(ValueError, match="native_step_s"):
        ThrusterAdapter().run(runtime)


@pytest.mark.parametrize("capability,backend", [
    ("component.thruster.v1", "project_reference"), ("component.battery.v1", "basilisk_native"),
])
def test_native_thruster_step_projection_is_scoped(capability, backend):
    from sat_sim.task_models import to_runtime_task_spec

    spec = _canonical_thruster_example()
    spec["model"]["capability_id"] = capability
    spec["parameters"]["values"]["physics_backend"] = backend
    assert "step_s" not in to_runtime_task_spec(spec)["simulation"]


def test_legacy_explicit_flat_step_is_preserved_across_canonical_migration():
    from sat_sim.task_models import canonicalize_task_spec, to_runtime_task_spec

    legacy = thruster_spec()
    legacy["simulation"].update(step_s=0.02, solver={"step_s": 0.005})
    canonical = canonicalize_task_spec(legacy)
    runtime = to_runtime_task_spec(canonical)
    assert runtime["simulation"]["step_s"] == 0.02
    assert runtime["simulation"]["solver"]["step_s"] == 0.005
    assert ThrusterAdapter().validate(runtime)
