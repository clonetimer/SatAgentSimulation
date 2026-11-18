"""Acceptance of the official Basilisk SensorThermal component."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from sat_sim.adapters.component_source_native_misc import ThermalNodeAdapter


PREFIX = "thermal.node."


def thermal_spec(**parameters):
    return {
        "schema_version": "0.1.0",
        "task_id": "native-thermal-node-component",
        "task_type": "component",
        "capability_id": "component.thermal_node.v1",
        "target": {"level": "component", "name": "thermal_node", "mode": "nominal"},
        "simulation": {"duration_s": 10.0, "sample_s": 1.0, "step_s": 0.1},
        "parameters": {"physics_backend": "basilisk_native", **parameters},
    }


def test_native_export_matches_official_temperature_recorder(monkeypatch):
    basilisk = pytest.importorskip("Basilisk")
    adapter = ThermalNodeAdapter()
    spec = thermal_spec()
    result = adapter.run(spec)
    assert result.metadata.get("native_provenance") == "Basilisk.simulation.sensorThermal.SensorThermal"

    from Basilisk.simulation import sensorThermal
    from sat_sim.adapters import component_thermal_node_native as native

    execute = native._execute
    recorded = {}

    def capture(case):
        version, module_path, recorder = execute(case)
        recorded.update(times=list(recorder.times()), written=list(recorder.timesWritten()), celsius=list(recorder.temperature))
        return version, module_path, recorder

    monkeypatch.setattr(native, "_execute", capture)
    assert not adapter.validate(spec)
    result = adapter.run(spec)
    rows = result.trace_rows
    qoi = result.summary["qoi"]
    metadata = result.metadata
    assert metadata["basilisk_version"] == basilisk.__version__
    assert metadata["native_module_path"] == sensorThermal.__file__
    assert metadata["runtime_truth_status"] == "official_native_temperature_recording"
    assert any("Earth IR/albedo" in limitation for limitation in metadata.get("known_physics_limits", []))
    assert metadata["message_bindings"] == {
        "sunInMsg": "SpicePlanetStateMsg.PositionVector",
        "stateInMsg": "SCStatesMsg.r_BN_N, SCStatesMsg.sigma_BN",
        "sunEclipseInMsg": "EclipseMsg.illuminationFactor",
        "sensorStatusInMsg": "DeviceStatusMsg.deviceStatus",
        "temperatureOutMsg": "TemperatureMsg.temperature",
    }
    assert metadata["native_model_constants"] == {
        "solar_flux_w_per_m2": 1366.0, "emission_constant_w_per_m2_k4": 5.76051e-8,
    }
    assert metadata["native_fixture"]["sun_position_n_m"] == [149_597_870_693.0, 0.0, 0.0]
    assert metadata["native_fixture"]["device_status"] == 1
    assert metadata["native_recorder_samples"] == 101
    assert metadata["native_step_s"] == 0.1
    assert result.summary["physics_backend"] == "basilisk_native"
    assert result.labels["run_labels"][0]["physics_backend"] == "basilisk_native"
    assert recorded["times"] == recorded["written"] == list(range(0, 10_000_000_001, 100_000_000))
    assert [row["time_s"] for row in rows] == list(range(11))
    assert [row["sample_index"] for row in rows] == list(range(11))
    assert [row[PREFIX + "temp_c"] for row in rows] == recorded["celsius"][::10]
    kelvin = [value + 273.15 for value in recorded["celsius"]]
    assert [row[PREFIX + "temp_k"] for row in rows] == kelvin[::10]
    for row in rows:
        assert {name for name in row if name.startswith(PREFIX)} == {
            PREFIX + "temp_c", PREFIX + "temp_k", PREFIX + "input_power_w",
        }
        assert row[PREFIX + "input_power_w"] == 10.0
    assert qoi == {
        PREFIX + "initial_temp_k": kelvin[0],
        PREFIX + "final_temp_k": kelvin[-1],
        PREFIX + "min_temp_k": min(kelvin),
        PREFIX + "max_temp_k": max(kelvin),
        PREFIX + "delta_temp_k": kelvin[-1] - kelvin[0],
    }
    assert kelvin[0] == 300.0
    assert kelvin[-1] == pytest.approx(300.06961637618775, abs=1e-11, rel=0)
    sources = metadata["field_sources"]
    assert set(sources) == set(qoi) | {name for name in rows[0] if name.startswith(PREFIX)}
    assert "temperatureOutMsg.temperature" in sources[PREFIX + "temp_c"]
    assert "273.15" in sources[PREFIX + "temp_k"]
    assert "configured" in sources[PREFIX + "input_power_w"]
    assert all("dense" in sources[PREFIX + name] for name in ("min_temp_k", "max_temp_k"))


def test_native_exact_fractional_grid_and_time_zero_celsius():
    pytest.importorskip("Basilisk")
    spec = thermal_spec(initial_temp_k=273.15)
    spec["simulation"].update(duration_s=0.63, sample_s=0.21, step_s=0.03,
                              solver={"method": "euler", "step_s": 0.0300000001})
    result = ThermalNodeAdapter().run(spec)
    assert [row["time_s"] for row in result.trace_rows] == [0.0, 0.21, 0.42, 0.63]
    assert result.metadata.get("native_recorder_samples") == 22
    assert result.metadata["native_step_s"] == 0.03
    assert result.trace_rows[0][PREFIX + "temp_c"] == 0.0
    assert result.trace_rows[0][PREFIX + "temp_k"] == 273.15


@pytest.mark.parametrize("parameters,expected_k", [
    ({"power_w": 0}, 299.9585356779154),
    ({"power_w": 20}, 300.18069705204715),
    ({"illumination_factor": 1}, 300.10755042951445),
    ({"mass_kg": 2}, 300.0348129539443),
    ({"specific_heat_j_per_kg_k": 1800}, 300.0348129539443),
    ({"illumination_factor": 1, "normal_b": [-1, 0, 0]}, 300.06961637618775),
    ({"illumination_factor": 1, "sun_direction_n": [-1, 0, 0]}, 300.06961637618775),
    ({"initial_temp_k": 273.15}, 273.2325895527683),
])
def test_native_temperature_responds_to_physical_inputs(parameters, expected_k):
    pytest.importorskip("Basilisk")
    result = ThermalNodeAdapter().run(thermal_spec(**parameters))
    assert result.trace_rows[-1][PREFIX + "temp_k"] == pytest.approx(expected_k, abs=1e-11, rel=0)


@pytest.mark.parametrize("name,value", [
    ("initial_temp_k", 0), ("initial_temp_k", 2001), ("power_w", 1_000_001),
    ("power_w", True), ("power_w", "0"), ("power_w", float("nan")), ("power_w", 10**400),
    ("surface_area_m2", 0), ("surface_area_m2", 1001), ("mass_kg", 0), ("mass_kg", 1_000_001),
    ("specific_heat_j_per_kg_k", 0), ("specific_heat_j_per_kg_k", float("inf")),
    ("absorptivity", 0), ("emissivity", 1.01), ("illumination_factor", -0.01), ("illumination_factor", 1.01),
    ("normal_b", [True, 0, 0]), ("normal_b", [1, 0]), ("normal_b", [1, 0, 0.01]),
    ("normal_b", [1, 0, 2e-6]), ("sun_direction_n", ["1", 0, 0]), ("sun_direction_n", [1, float("nan"), 0]),
    ("sun_direction_n", [0, 0, 0]), ("ambient_k", 300), ("tau_s", 10), ("heat_gain_k_per_w", 1),
    ("power_profile_w", []), ("min_temp_k", 0), ("max_temp_k", 1000), ("unknown", 0),
    ("overrides", [{"path": "power_w", "value": 30}]),
])
def test_invalid_native_parameters_rejected_without_fallback(name, value):
    adapter = ThermalNodeAdapter()
    spec = thermal_spec(**{name: value})
    assert any(name in issue.path for issue in adapter.validate(spec))
    with pytest.raises(ValueError):
        adapter.run(spec)


@pytest.mark.parametrize("simulation,path", [
    ({"duration_s": True}, "$.simulation.duration_s"),
    ({"duration_s": 4_503_600}, "$.simulation.duration_s"),
    ({"sample_s": "1"}, "$.simulation.sample_s"),
    ({"sample_s": 11}, "$.simulation.sample_s"),
    ({"step_s": False}, "$.simulation.step_s"),
    ({"step_s": None}, "$.simulation.step_s"),
    ({"step_s": 1e-12}, "$.simulation.step_s"),
    ({"step_s": 2}, "$.simulation.step_s"),
    ({"solver": []}, "$.simulation.solver"),
    ({"solver": {"method": "rk4"}}, "$.simulation.solver.method"),
    ({"solver": {"method": []}}, "$.simulation.solver.method"),
    ({"solver": {"step_s": .2}}, "$.simulation.solver.step_s"),
    ({"duration_s": 10.1}, "$.simulation"),
    ({"step_s": 1e-5}, "$.simulation"),
])
def test_invalid_native_schedule_rejected_without_fallback(simulation, path):
    spec = thermal_spec()
    spec["simulation"].update(simulation)
    adapter = ThermalNodeAdapter()
    assert any(issue.path == path for issue in adapter.validate(spec))
    with pytest.raises(ValueError):
        adapter.run(spec)


@pytest.mark.parametrize("update,path", [
    ({"target": {"level": "component", "name": "thermal_node", "mode": "fault"}}, "$.target.mode"),
    ({"events": {"faults": [{"effect": "sensor_bias"}]}}, "$.events.faults"),
    ({"constraints": [{"constraint_type": "thermal_limit"}]}, "$.constraints"),
    ({"mission": {"required_couplings": ["solar"]}}, "$.mission.required_couplings"),
    ({"outputs": {"plots": [PREFIX + "measured_temp_k"]}}, "$.outputs.plots[0]"),
])
def test_native_only_accepts_its_nominal_uncoupled_contract(update, path):
    spec = thermal_spec()
    spec.update(update)
    adapter = ThermalNodeAdapter()
    assert any(issue.path == path for issue in adapter.validate(spec))
    with pytest.raises(ValueError):
        adapter.run(spec)


def test_rejects_excess_equilibrium_envelope_before_graph_construction():
    spec = thermal_spec(power_w=1_000_000)
    adapter = ThermalNodeAdapter()
    assert any("envelope" in issue.message for issue in adapter.validate(spec))
    with pytest.raises(ValueError, match="envelope"):
        adapter.run(spec)


def test_stiff_fixture_accepts_safe_step_and_converges_with_refinement(monkeypatch):
    pytest.importorskip("Basilisk")
    adapter = ThermalNodeAdapter()
    spec = thermal_spec(surface_area_m2=0.1, mass_kg=0.001, specific_heat_j_per_kg_k=900,
                        emissivity=0.8, initial_temp_k=300, power_w=0, illumination_factor=0)
    spec["simulation"].update(duration_s=1.0, sample_s=0.2)
    final = []
    for step in (.1, .05, .025):
        spec["simulation"]["step_s"] = step
        assert not adapter.validate(spec)
        result = adapter.run(spec)
        final.append(result.summary["qoi"][PREFIX + "final_temp_k"])
        assert result.metadata["native_max_step_s"] == pytest.approx(0.18082889651552843, abs=1e-12, rel=0)
        assert result.metadata["native_temperature_envelope_k"] == pytest.approx(300.0)
    assert all(value < 300 for value in final)
    assert 0 < final[2] - final[1] < final[1] - final[0]

    from sat_sim.adapters import component_thermal_node_native as native

    def unexpected_graph(case):
        pytest.fail("an unstable integration step reached graph construction")

    monkeypatch.setattr(native, "_execute", unexpected_graph)
    spec["simulation"]["step_s"] = .2
    assert any(issue.path == "$.simulation.step_s" for issue in adapter.validate(spec))
    with pytest.raises(ValueError, match="step"):
        adapter.run(spec)


def test_exact_initial_envelope_limit_is_accepted():
    pytest.importorskip("Basilisk")
    result = ThermalNodeAdapter().run(thermal_spec(initial_temp_k=2000, power_w=0))
    assert result.trace_rows[0][PREFIX + "temp_k"] == 2000.0
    assert result.trace_rows[-1][PREFIX + "temp_k"] < 2000.0


def test_positive_native_temperature_may_cool_below_one_kelvin():
    pytest.importorskip("Basilisk")
    result = ThermalNodeAdapter().run(thermal_spec(initial_temp_k=1, power_w=0, surface_area_m2=10, mass_kg=.001))
    assert 0 < result.trace_rows[-1][PREFIX + "temp_k"] < 1


def test_step_defaults_to_sample_when_omitted():
    pytest.importorskip("Basilisk")
    spec = thermal_spec()
    del spec["simulation"]["step_s"]
    result = ThermalNodeAdapter().run(spec)
    assert result.metadata.get("native_recorder_samples") == 11
    assert result.metadata["native_step_s"] == 1.0


def test_native_version_outside_the_verified_model_fails_closed(monkeypatch):
    basilisk = pytest.importorskip("Basilisk")
    monkeypatch.setattr(basilisk, "__version__", "3.0.0")
    adapter = ThermalNodeAdapter()
    spec = thermal_spec()
    issues = adapter.validate(spec)
    assert any(issue.code == "native_thermal_node_dependency" and "2.11.0" in issue.message for issue in issues)
    with pytest.raises(RuntimeError, match=r"2\.11\.0"):
        adapter.run(spec)


@pytest.mark.parametrize("has_write_member", [False, True], ids=["missing", "none"])
def test_native_recorder_requires_write_timestamps(monkeypatch, has_write_member):
    pytest.importorskip("Basilisk")
    from sat_sim.adapters import component_thermal_node_native as native

    execute = native._execute

    def without_write_timestamps(case):
        version, module_path, recorder = execute(case)
        incomplete = SimpleNamespace(times=recorder.times, temperature=recorder.temperature)
        if has_write_member:
            incomplete.timesWritten = None
        return version, module_path, incomplete

    monkeypatch.setattr(native, "_execute", without_write_timestamps)
    with pytest.raises(RuntimeError, match="missing or malformed native temperatureOutMsg recorder data"):
        ThermalNodeAdapter().run(thermal_spec())


@pytest.mark.parametrize("field,bad_value", [
    ("times", 100_000_001), ("timesWritten", 0), ("temperature", float("nan")),
    ("temperature", -273.15), ("temperature", 5000), ("temperature", "26.85"),
    ("temperature", [26.85]), ("temperature", None),
])
def test_tampered_intermediate_native_records_cannot_reach_sampled_trace(monkeypatch, field, bad_value):
    pytest.importorskip("Basilisk")
    adapter = ThermalNodeAdapter()
    spec = thermal_spec()
    assert adapter.run(spec).metadata.get("native_recorder_samples") == 101

    from sat_sim.adapters import component_thermal_node_native as native

    execute = native._execute

    def tamper(case):
        version, module_path, recorder = execute(case)
        records = {"times": list(recorder.times()), "timesWritten": list(recorder.timesWritten()),
                   "temperature": list(recorder.temperature)}
        records[field][1] = bad_value
        return version, module_path, SimpleNamespace(
            times=lambda: records["times"], timesWritten=lambda: records["timesWritten"],
            temperature=records["temperature"],
        )

    monkeypatch.setattr(native, "_execute", tamper)
    with pytest.raises(RuntimeError, match="recorder"):
        adapter.run(deepcopy(spec))
