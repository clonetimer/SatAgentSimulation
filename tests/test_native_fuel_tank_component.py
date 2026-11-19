"""Acceptance of the official Basilisk fuel-tank component."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from sat_sim.adapters.component_source_native_misc import FuelTankAdapter


PREFIX = "propulsion.fuel_tank."


def fuel_tank_spec(**parameters):
    return {
        "schema_version": "0.1.0",
        "task_id": "native-fuel-tank-component",
        "task_type": "component",
        "capability_id": "component.fuel_tank.v1",
        "description": "Prescribed constant mass withdrawal on a free carrier",
        "target": {"level": "component", "name": "fuel_tank", "mode": "nominal"},
        "simulation": {"duration_s": 1.0, "sample_s": 0.1, "step_s": 0.01},
        "parameters": {
            "physics_backend": "basilisk_native",
            "capacity_kg": 3.0,
            "initial_mass_kg": 2.0,
            "mass_flow_kg_s": 0.001,
            **parameters,
        },
    }


def test_native_withdrawal_exports_official_recording_and_field_provenance(monkeypatch):
    basilisk = pytest.importorskip("Basilisk")
    adapter = FuelTankAdapter()
    spec = fuel_tank_spec()
    assert not adapter.validate(spec)

    from sat_sim.adapters import component_fuel_tank_native as native

    execute = native._execute
    recorded = {}

    def capture(case):
        version, recorder = execute(case)
        recorded.update(time_ns=recorder.times(), mass=recorder.fuelMass, rate=recorder.fuelMassDot, capacity=recorder.maxFuelMass)
        return version, recorder

    monkeypatch.setattr(native, "_execute", capture)
    result = adapter.run(spec)

    assert result.metadata["physics_backend"] == "basilisk_native"
    assert result.summary["physics_backend"] == "basilisk_native"
    assert result.labels["run_labels"][0]["physics_backend"] == "basilisk_native"
    assert result.metadata["native_provenance"] == "Basilisk.simulation.fuelTank.FuelTank"
    assert result.metadata["basilisk_version"] == basilisk.__version__
    assert result.metadata["native_recorder_samples"] == 101
    assert result.metadata["native_step_s"] == 0.01
    assert [row["time_s"] for row in result.trace_rows] == [index / 10 for index in range(11)]
    assert list(recorded["time_ns"]) == list(range(0, 1_000_000_001, 10_000_000))
    assert [row[PREFIX + "propellant_mass_kg"] for row in result.trace_rows] == list(recorded["mass"][::10])
    assert [row[PREFIX + "mass_rate_kg_s"] for row in result.trace_rows] == list(recorded["rate"][::10])
    assert [row[PREFIX + "max_mass_kg"] for row in result.trace_rows] == list(recorded["capacity"][::10])
    for row in result.trace_rows:
        assert {name for name in row if name.startswith(PREFIX)} == {
            PREFIX + name for name in ("propellant_mass_kg", "mass_rate_kg_s", "max_mass_kg", "commanded_mass_flow_kg_s")
        }
        assert row[PREFIX + "mass_rate_kg_s"] == -0.001
        assert row[PREFIX + "max_mass_kg"] == 3.0
        assert row[PREFIX + "commanded_mass_flow_kg_s"] == 0.001
    assert result.trace_rows[5][PREFIX + "propellant_mass_kg"] == pytest.approx(1.9995, abs=1e-13)
    qoi = result.summary["qoi"]
    assert set(qoi) == {PREFIX + name for name in ("initial_mass_kg", "final_mass_kg", "consumed_mass_kg")}
    assert qoi[PREFIX + "initial_mass_kg"] == result.trace_rows[0][PREFIX + "propellant_mass_kg"] == 2.0
    assert qoi[PREFIX + "final_mass_kg"] == result.trace_rows[-1][PREFIX + "propellant_mass_kg"]
    assert qoi[PREFIX + "final_mass_kg"] == pytest.approx(1.999, abs=1e-13)
    assert qoi[PREFIX + "consumed_mass_kg"] == qoi[PREFIX + "initial_mass_kg"] - qoi[PREFIX + "final_mass_kg"]
    sources = result.metadata["field_sources"]
    assert set(sources) == set(qoi) | {name for name in result.trace_rows[0] if name.startswith(PREFIX)}
    assert "fuelTankOutMsg.fuelMass" in sources[PREFIX + "propellant_mass_kg"]
    assert "fuelTankOutMsg.fuelMassDot" in sources[PREFIX + "mass_rate_kg_s"]
    assert "fuelTankOutMsg.maxFuelMass" in sources[PREFIX + "max_mass_kg"]
    assert "input" in sources[PREFIX + "commanded_mass_flow_kg_s"]
    assert "recorded" in sources[PREFIX + "consumed_mass_kg"]


@pytest.mark.parametrize(
    "initial,capacity,flow,final",
    [(2.0, 3.0, 0.0, 2.0), (0.0, 3.0, 0.0, 0.0), (2.0, 3.0, 0.002, 1.998), (1.0, 5.0, 0.001, 0.999)],
)
def test_native_mass_response_to_changed_flow_or_fixture_configuration(initial, capacity, flow, final):
    pytest.importorskip("Basilisk")
    result = FuelTankAdapter().run(fuel_tank_spec(initial_mass_kg=initial, capacity_kg=capacity, mass_flow_kg_s=flow))
    assert result.summary["qoi"][PREFIX + "final_mass_kg"] == pytest.approx(final, abs=1e-13)
    for row in result.trace_rows:
        assert row[PREFIX + "max_mass_kg"] == capacity
        assert row[PREFIX + "mass_rate_kg_s"] == -flow
        if not flow:
            assert row[PREFIX + "propellant_mass_kg"] == initial


def test_matching_rounded_steps_run_on_a_fractional_native_grid():
    pytest.importorskip("Basilisk")
    spec = fuel_tank_spec()
    spec["simulation"].update(duration_s=0.63, sample_s=0.21, step_s=0.03, solver={"step_s": 0.0300000001})
    result = FuelTankAdapter().run(spec)
    assert [row["time_s"] for row in result.trace_rows] == [0.0, 0.21, 0.42, 0.63]
    assert result.metadata["native_step_s"] == 0.03
    assert result.metadata["native_recorder_samples"] == 22
    assert result.summary["qoi"][PREFIX + "final_mass_kg"] == pytest.approx(1.99937, abs=1e-13)


@pytest.mark.parametrize("parameters,path", [
    ({"capacity_kg": 0}, "capacity_kg"),
    ({"capacity_kg": float("inf")}, "capacity_kg"),
    ({"capacity_kg": 10**400}, "capacity_kg"),
    ({"initial_mass_kg": -1}, "initial_mass_kg"),
    ({"initial_mass_kg": 4}, "initial_mass_kg"),
    ({"initial_mass_kg": float("nan")}, "initial_mass_kg"),
    ({"mass_flow_kg_s": -1}, "mass_flow_kg_s"),
    ({"mass_flow_kg_s": False}, "mass_flow_kg_s"),
    ({"mass_flow_kg_s": "0.001"}, "mass_flow_kg_s"),
    ({"mass_flow_kg_s": float("inf")}, "mass_flow_kg_s"),
    ({"mass_flow_kg_s": 1e308}, "mass_flow_kg_s"),
    ({"initial_mass_kg": 0, "mass_flow_kg_s": 1e-5}, "mass_flow_kg_s"),
    ({"initial_mass_kg": 1.01, "mass_flow_kg_s": 1.0}, "mass_flow_kg_s"),
    ({"mass_flow_kg_s": 5e-324}, "mass_flow_kg_s"),
    ({"mass_flow_kg_s": 1e-12}, "mass_flow_kg_s"),
    ({"full_pressure_pa": 2.5e6}, "full_pressure_pa"),
    ({"dry_pressure_pa": 1.5e5}, "dry_pressure_pa"),
    ({"mass_flow_profile_kg_s": []}, "mass_flow_profile_kg_s"),
    ({"leak_rate_kg_s": 0.0}, "leak_rate_kg_s"),
    ({"native_step_s": 0.01}, "native_step_s"),
    ({"overrides": [{"path": "mass_flow_kg_s", "value": 0.0}]}, "overrides"),
    ({"physics_backend": "other"}, "physics_backend"),
])
def test_native_rejects_invalid_parameters_before_executing(parameters, path, monkeypatch):
    _assert_rejected(fuel_tank_spec(**parameters), f"$.parameters.{path}", monkeypatch)


@pytest.mark.parametrize("initial,capacity,flow", [
    (1e308, 1e308, 1e308),  # The initial mass-times-flow product overflows.
    (1e-200, 3.0, 1e-201),  # The product rounds to zero even at initial mass.
    (1.6e-154, 3.0, 1.5e-154),  # The product is normal initially and subnormal later.
], ids=["overflow", "underflow", "subnormal-during-withdrawal"])
def test_native_rejects_unrepresentable_withdrawal_before_build_or_plan(initial, capacity, flow, monkeypatch):
    from sat_sim.execution_planner import plan_task_spec

    spec = fuel_tank_spec(initial_mass_kg=initial, capacity_kg=capacity, mass_flow_kg_s=flow)
    spec["simulation"].update(duration_s=0.3, sample_s=0.3, step_s=0.1)
    _assert_rejected(spec, "$.parameters.mass_flow_kg_s", monkeypatch)
    planning = plan_task_spec(spec)
    assert not planning.ok
    assert any(issue.path == "$.parameters.mass_flow_kg_s" and "mass-times-flow" in issue.message
               for issue in planning.validation.errors)


def test_native_withdrawal_accepts_large_values_with_representable_intermediates():
    pytest.importorskip("Basilisk")
    spec = fuel_tank_spec(initial_mass_kg=1e154, capacity_kg=1e154, mass_flow_kg_s=1.7e154)
    spec["simulation"].update(duration_s=0.3, sample_s=0.3, step_s=0.1)
    adapter = FuelTankAdapter()
    assert not adapter.validate(spec)
    result = adapter.run(spec)
    assert result.trace_rows[-1][PREFIX + "propellant_mass_kg"] == pytest.approx(4.9e153, rel=1e-14)
    assert result.trace_rows[-1][PREFIX + "mass_rate_kg_s"] == -1.7e154


def test_native_direct_adapter_rejects_required_mission_coupling(monkeypatch):
    spec = fuel_tank_spec()
    spec["mission"] = {"required_couplings": ["propulsion_effector_to_spacecraft"]}
    _assert_rejected(spec, "$.mission.required_couplings", monkeypatch)


@pytest.mark.parametrize("simulation,path", [
    ({"duration_s": float("nan")}, "duration_s"),
    ({"sample_s": float("inf")}, "sample_s"),
    ({"step_s": True}, "step_s"),
    ({"step_s": 0}, "step_s"),
    ({"step_s": 4e-10}, "step_s"),
    ({"duration_s": 1e308}, "duration_s"),
    ({"duration_s": 4342463.3232458765}, "duration_s"),
    ({"duration_s": 1.01}, None),
    ({"sample_s": 0.101}, None),
    ({"step_s": 0.03}, None),
    ({"step_s": 1e-6}, None),
    ({"solver": {"step_s": 0.005}}, "solver.step_s"),
])
def test_native_rejects_invalid_time_grids_before_executing(simulation, path, monkeypatch):
    spec = fuel_tank_spec()
    spec["simulation"].update(simulation)
    _assert_rejected(spec, "$.simulation" if path is None else f"$.simulation.{path}", monkeypatch)


@pytest.mark.parametrize("simulation,path", [
    ({"duration_s": 1e-9, "sample_s": 1.1e-9, "step_s": 1e-9}, "sample_s"),
    ({"duration_s": 1.3e-9, "sample_s": 1.1e-9, "step_s": 1.2e-9}, "step_s"),
    ({"duration_s": 1.3e-9, "sample_s": 1.1e-9, "step_s": 1e-9, "solver": {"step_s": 1.2e-9}}, "solver.step_s"),
])
def test_native_requires_ordered_times_even_if_they_round_to_the_same_tick(simulation, path, monkeypatch):
    from sat_sim.execution_planner import plan_task_spec

    spec = fuel_tank_spec()
    spec["simulation"] = simulation
    assert not plan_task_spec(spec).ok
    _assert_rejected(spec, f"$.simulation.{path}", monkeypatch)


@pytest.mark.parametrize("key,value,path", [
    ("capability_id", "component.thruster.v1", "$.capability_id"),
    ("task_type", "subsystem", "$.task_type"),
    ("target", {"level": "component", "name": "battery", "mode": "nominal"}, "$.target"),
    ("target", {"level": "component", "name": "fuel_tank", "mode": "fault"}, "$.target.mode"),
    ("faults", [{"fault_type": "leak"}], "$.faults"),
    ("modifiers", {"degradations": [{"degradation_type": "pressure_decay"}]}, "$.modifiers.degradations"),
    ("modifiers", {"constraints": [{"constraint_type": "outlet_blockage"}]}, "$.modifiers.constraints"),
])
def test_native_rejects_wrong_identity_or_events_before_executing(key, value, path, monkeypatch):
    spec = fuel_tank_spec()
    spec[key] = value
    _assert_rejected(spec, path, monkeypatch)


def _assert_rejected(spec, path, monkeypatch):
    from sat_sim.adapters import component_fuel_tank_native as native

    monkeypatch.setattr(native, "_execute", lambda case: pytest.fail("unexpected native graph construction"))
    issues = FuelTankAdapter().validate(spec)
    assert issues and issues[0].severity == "error" and issues[0].path == path
    with pytest.raises(ValueError) as exc_info:
        FuelTankAdapter().run(spec)
    assert issues[0].message in str(exc_info.value)


def test_missing_basilisk_fails_without_using_reference_model(monkeypatch):
    monkeypatch.setitem(sys.modules, "Basilisk", None)
    spec = fuel_tank_spec()
    issues = FuelTankAdapter().validate(spec)
    assert issues and issues[0].path == "$.parameters.physics_backend"
    assert "Basilisk" in issues[0].message
    with pytest.raises(RuntimeError, match="Basilisk"):
        FuelTankAdapter().run(spec)


@pytest.mark.parametrize("outputs,path", [
    ({"qoi": [PREFIX + "final_pressure_pa"]}, "qoi[0]"),
    ({"record_fields": [PREFIX + "final_pressure_pa"]}, "record_fields[0]"),
    ({"plots": [PREFIX + "pressure_pa"]}, "plots[0]"),
    ({"telemetry_streams": [{"fields": [PREFIX + "pressure_pa"]}]}, "telemetry_streams[0].fields[0]"),
])
def test_native_direct_adapter_rejects_pressure_output_requests(outputs, path, monkeypatch):
    spec = fuel_tank_spec()
    spec["outputs"] = outputs
    _assert_rejected(spec, f"$.outputs.{path}", monkeypatch)


def test_native_rejects_corrupted_mass_at_a_step_hidden_from_export(monkeypatch):
    pytest.importorskip("Basilisk")
    from sat_sim.adapters import component_fuel_tank_native as native

    execute = native._execute

    def corrupt_hidden_step(case):
        version, recorder = execute(case)
        mass = recorder.fuelMass.copy()
        mass[1] -= 2e-6  # Preserve monotonic mass and both exported neighbors.
        return version, SimpleNamespace(times=recorder.times, fuelMass=mass,
                                        fuelMassDot=recorder.fuelMassDot, maxFuelMass=recorder.maxFuelMass)

    monkeypatch.setattr(native, "_execute", corrupt_hidden_step)
    with pytest.raises(RuntimeError, match="dense mass conservation"):
        FuelTankAdapter().run(fuel_tank_spec())


def test_reference_nominal_and_pressure_sensor_fault_remain_available():
    adapter = FuelTankAdapter()
    spec = fuel_tank_spec()
    del spec["parameters"]["physics_backend"]
    reference = adapter.run(spec)
    spec["parameters"]["physics_backend"] = "project_reference"
    assert adapter.run(spec) == reference
    spec["target"]["mode"] = "fault"
    spec["faults"] = [{"fault_type": "pressure_sensor_fault", "parameters": {"severity": 0.5}}]
    assert not adapter.validate(spec)
    fault = adapter.run(spec)
    assert fault.trace_rows[-1][PREFIX + "pressure_pa"] == reference.trace_rows[-1][PREFIX + "pressure_pa"] * 0.5
    assert fault.trace_rows[-1][PREFIX + "propellant_mass_kg"] == reference.trace_rows[-1][PREFIX + "propellant_mass_kg"]


@pytest.mark.parametrize("legacy", [False, True], ids=["canonical-example", "legacy-no-step-zero-flow"])
def test_native_fuel_tank_runs_through_sealed_bundle(tmp_path, legacy):
    pytest.importorskip("Basilisk")
    import yaml

    from sat_sim.execution_planner import plan_task_spec
    from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle

    if legacy:
        spec = fuel_tank_spec(mass_flow_kg_s=0.0)
        del spec["simulation"]["step_s"]
        step, recorder_samples, final_mass = 0.1, 11, 2.0
    else:
        example = Path(__file__).resolve().parents[1] / "examples" / "component_fuel_tank_basilisk_native.yaml"
        spec = yaml.safe_load(example.read_text(encoding="utf-8"))
        step, recorder_samples, final_mass = 0.01, 101, 1.999

    planning = plan_task_spec(spec)
    assert planning.ok, planning.validation.to_dict()
    prepared = prepare_run(spec, output_root=tmp_path)
    result = execute_prepared_run(prepared.bundle_root, expected_plan_sha256=prepared.execution_plan_sha256)
    assert result.run_record.status.value == "SUCCEEDED", result.run_record
    assert result.run_record.sealed and result.validation.result.value == "PASS", result.validation
    assert verify_run_bundle(prepared.bundle_root)["ok"]
    assert result.summary["physics_backend"] == "basilisk_native"
    metadata = result.summary["adapter_metadata"]
    assert metadata["physics_backend"] == "basilisk_native"
    assert metadata["native_provenance"] == "Basilisk.simulation.fuelTank.FuelTank"
    assert metadata["native_step_s"] == step
    assert metadata["native_recorder_samples"] == recorder_samples
    assert len(result.trace_rows) == 11
    assert result.summary["qoi"][PREFIX + "final_mass_kg"] == pytest.approx(final_mass, abs=1e-13)
    if legacy:
        assert result.summary["qoi"][PREFIX + "consumed_mass_kg"] == 0.0
    contract = json.loads((Path(prepared.bundle_root) / "runtime" / "capability_contract.json").read_text())["contract"]
    assert contract["selected_physics_backend"] == "basilisk_native"
    assert set(contract["parameters"]) == {"physics_backend", "capacity_kg", "initial_mass_kg", "mass_flow_kg_s"}
    assert {row["name"] for row in contract["outputs"]["summary"]} == set(result.summary["qoi"])
    assert {row["name"] for row in contract["outputs"]["trace"]} == {
        name for name in result.trace_rows[0] if name.startswith(PREFIX)
    }
