from __future__ import annotations

import pytest


def _native_battery_spec(task_id: str = "p9n3-native-battery"):
    return {
        "schema_version": "0.1.0",
        "task_id": task_id,
        "task_type": "component",
        "description": "P9-N3 official Basilisk battery component",
        "capability_id": "component.battery.v1",
        "simulation": {"duration_s": 60.0, "sample_s": 10.0},
        "target": {"level": "component", "name": "battery", "mode": "nominal"},
        "parameters": {
            "physics_backend": "basilisk_native",
            "capacity_wh": 100.0,
            "initial_soc": 0.6,
            "net_power_w": -20.0,
            "charge_efficiency": 1.0,
            "discharge_efficiency": 1.0,
            "min_soc": 0.0,
            "max_soc": 1.0,
        },
        "faults": [],
        "modifiers": {"faults": [], "degradations": []},
        "outputs": {
            "include_summary": True,
            "include_trace": True,
            "include_labels": True,
            "include_manifest": True,
        },
    }


def test_native_battery_adapter_uses_official_simple_battery_and_sink():
    pytest.importorskip("Basilisk")
    from sat_sim.adapters.component_battery import BatteryAdapter

    result = BatteryAdapter().run(_native_battery_spec())
    assert result.metadata["physics_backend"] == "basilisk_native"
    assert "Basilisk.simulation.simpleBattery.SimpleBattery" in result.metadata["basilisk_official_native"]
    assert result.metadata["native_model_status"] == "PASS"
    assert result.trace_rows
    assert result.trace_rows[-1]["eps.battery.soc"] < result.trace_rows[0]["eps.battery.soc"]
    assert result.summary["qoi"]["eps.battery.final_soc"] == pytest.approx(result.trace_rows[-1]["eps.battery.soc"])


def test_native_battery_adapter_fails_closed_for_unsupported_efficiency():
    from sat_sim.adapters.component_battery import BatteryAdapter

    spec = _native_battery_spec()
    spec["parameters"]["discharge_efficiency"] = 0.97
    with pytest.raises(ValueError, match="UNITY_EFFICIENCY"):
        BatteryAdapter().run(spec)


def test_native_battery_runs_through_sealed_run_bundle(tmp_path):
    pytest.importorskip("Basilisk")
    from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle

    spec = _native_battery_spec("p9n3-native-battery-run-bundle")
    prepared = prepare_run(spec, output_root=tmp_path / "runs", run_id="p9n3-native-battery-run-bundle")
    executed = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1,
    )
    assert executed.run_record.status.value == "SUCCEEDED"
    assert executed.run_record.sealed is True
    assert executed.validation.result.value == "PASS"
    assert executed.summary["physics_backend"] == "basilisk_native"
    assert executed.summary["adapter_metadata"]["physics_backend"] == "basilisk_native"
    assert executed.summary["adapter_metadata"]["native_provenance"] == "components.battery.builder.run_battery_native_case"
    assert executed.trace_rows
    assert executed.trace_rows[-1]["eps.battery.soc"] < executed.trace_rows[0]["eps.battery.soc"]
    assert verify_run_bundle(prepared.bundle_root)["ok"] is True


def _native_solar_panel_spec(task_id: str = "p9n3-native-solar-panel"):
    return {
        "schema_version": "0.1.0",
        "task_id": task_id,
        "task_type": "component",
        "description": "P9-N3 official Basilisk solar panel component",
        "capability_id": "component.solar_panel.v1",
        "simulation": {"duration_s": 60.0, "sample_s": 10.0},
        "target": {"level": "component", "name": "solar_panel", "mode": "nominal"},
        "parameters": {
            "physics_backend": "basilisk_native",
            "max_power_w": 100.0,
            "efficiency": 0.9,
            "panel_count": 1,
            "deployment_fraction": 1.0,
            "initial_normal_b": [1.0, 0.0, 0.0],
            "sun_vector_b": [1.0, 0.0, 0.0],
            "shadow_factor": 0.5,
            "enable_tracking": False,
            "max_slew_rate_rad_s": 0.0,
        },
        "faults": [],
        "modifiers": {"faults": [], "degradations": []},
        "outputs": {
            "include_summary": True,
            "include_trace": True,
            "include_labels": True,
            "include_manifest": True,
        },
    }


def test_native_solar_panel_adapter_uses_official_simple_solar_panel():
    pytest.importorskip("Basilisk")
    from sat_sim.adapters.component_solar_panel import SolarPanelAdapter

    result = SolarPanelAdapter().run(_native_solar_panel_spec())
    assert result.metadata["physics_backend"] == "basilisk_native"
    assert result.metadata["native_provenance"] == "Basilisk.simulation.simpleSolarPanel.SimpleSolarPanel"
    assert result.trace_rows
    assert result.trace_rows[-1]["eps.solar.array_power_w"] == pytest.approx(45.0, abs=5.0)


def test_native_solar_panel_adapter_fails_closed_for_tracking():
    from sat_sim.adapters.component_solar_panel import SolarPanelAdapter

    spec = _native_solar_panel_spec()
    spec["parameters"]["enable_tracking"] = True
    with pytest.raises(ValueError, match="TRACKING_NOT_SUPPORTED"):
        SolarPanelAdapter().run(spec)


def test_native_solar_panel_runs_through_sealed_run_bundle(tmp_path):
    pytest.importorskip("Basilisk")
    from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle

    spec = _native_solar_panel_spec("p9n3-native-solar-panel-run-bundle")
    prepared = prepare_run(spec, output_root=tmp_path / "runs", run_id="p9n3-native-solar-panel-run-bundle")
    executed = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1,
    )
    assert executed.run_record.status.value == "SUCCEEDED"
    assert executed.run_record.sealed is True
    assert executed.validation.result.value == "PASS", executed.validation.model_dump(mode="json")
    assert executed.summary["physics_backend"] == "basilisk_native"
    assert executed.summary["adapter_metadata"]["physics_backend"] == "basilisk_native"
    assert executed.summary["adapter_metadata"]["native_provenance"] == "Basilisk.simulation.simpleSolarPanel.SimpleSolarPanel"
    assert executed.trace_rows
    assert executed.trace_rows[-1]["eps.solar.array_power_w"] == pytest.approx(45.0, abs=5.0)
    assert verify_run_bundle(prepared.bundle_root)["ok"] is True


def test_native_eps_solar_panel_power_node_charges_simple_battery_in_one_simbase():
    pytest.importorskip("Basilisk")
    from subsystems.eps.runner import run_eps_basilisk_scenario
    from subsystems.eps.schemas import EPSBasiliskConfig

    summary, rows = run_eps_basilisk_scenario(EPSBasiliskConfig(
        duration_s=60.0,
        step_s=10.0,
        battery_capacity_wh=100.0,
        initial_soc=0.4,
        solar_power_w=80.0,
        solar_efficiency=0.25,
        solar_normal_b=(1.0, 0.0, 0.0),
        use_simple_solar_panel=True,
        solar_panel_enabled=True,
        bus_power_w=0.0,
        payload_power_w=0.0,
        adcs_power_w=0.0,
        comm_power_w=0.0,
        heater_power_w=0.0,
        rw_power_w=0.0,
        antenna_power_w=0.0,
        payload_requested=False,
        adcs_requested=False,
        comm_requested=False,
        heater_requested=False,
    ))
    assert summary.status == "PASS"
    assert summary.basilisk_simbase_used is True
    assert "simpleSolarPanel.SimpleSolarPanel" in summary.native_modules
    assert rows
    assert rows[-1].battery_storage_j > rows[0].battery_storage_j
    assert rows[-1].solar_power_w > 0.0


def test_native_solar_energy_is_integrated_from_recorded_power():
    pytest.importorskip("Basilisk")
    from sat_sim.adapters.component_solar_panel import SolarPanelAdapter

    result = SolarPanelAdapter().run(_native_solar_panel_spec())
    # The native irradiance differs from the project analytical check. Summary
    # energy must still agree with the power actually exported in the trace.
    recorded_energy_wh = sum(
        row["eps.solar.array_power_w"] * (row["time_s"] - previous["time_s"]) / 3600.0
        for previous, row in zip(result.trace_rows, result.trace_rows[1:])
    )
    assert result.summary["qoi"]["eps.solar.energy_generated_wh"] == pytest.approx(recorded_energy_wh, rel=1e-12)


def test_native_battery_accounts_for_energy_clipped_at_full_capacity():
    pytest.importorskip("Basilisk")
    from sat_sim.adapters.component_battery import BatteryAdapter

    spec = _native_battery_spec()
    spec["parameters"].update(initial_soc=1.0, net_power_w=20.0)
    result = BatteryAdapter().run(spec)
    assert result.trace_rows[-1]["eps.battery.storage_wh"] == pytest.approx(100.0)
    # 20 W for 60 s cannot enter an already-full 100 Wh battery.
    assert result.trace_rows[-1]["eps.battery.shunt_dissipated_wh"] == pytest.approx(1.0 / 3.0)
    assert result.summary["qoi"]["eps.battery.energy_throughput_wh"] == pytest.approx(1.0 / 3.0)


@pytest.mark.parametrize("component", ["battery", "solar_panel"])
def test_native_eps_components_reject_incomplete_recording_grid(component):
    from sat_sim.adapters.component_battery import BatteryAdapter
    from sat_sim.adapters.component_solar_panel import SolarPanelAdapter

    spec, adapter = ((_native_battery_spec(), BatteryAdapter()) if component == "battery"
                     else (_native_solar_panel_spec(), SolarPanelAdapter()))
    spec["simulation"]["duration_s"] = 65.0
    with pytest.raises(ValueError, match="TIME_GRID"):
        adapter.run(spec)


@pytest.mark.parametrize("component", ["battery", "solar_panel"])
@pytest.mark.parametrize("kind", ["constraints", "degradations"])
def test_native_eps_components_reject_unimplemented_modifiers(component, kind):
    from sat_sim.adapters.component_battery import BatteryAdapter
    from sat_sim.adapters.component_solar_panel import SolarPanelAdapter

    spec, adapter = ((_native_battery_spec(), BatteryAdapter()) if component == "battery"
                     else (_native_solar_panel_spec(), SolarPanelAdapter()))
    spec["modifiers"][kind] = [{"type": "unimplemented_effect", "target": component}]
    with pytest.raises(ValueError, match="NO_MODIFIERS"):
        adapter.run(spec)


@pytest.mark.parametrize("name,value", [
    ("initial_normal_b", [0.0, 0.0, 0.0]),
    ("sun_vector_b", [0.0, 0.0, 0.0]),
    ("panel_count", 1.5),
    ("deployment_fraction", 1.1),
])
def test_native_solar_rejects_inputs_that_would_be_silently_replaced(name, value):
    from sat_sim.adapters.component_solar_panel import SolarPanelAdapter

    spec = _native_solar_panel_spec()
    spec["parameters"][name] = value
    with pytest.raises(ValueError, match="NATIVE_SOLAR_PANEL"):
        SolarPanelAdapter().run(spec)


def test_native_battery_rejects_nonfinite_efficiency():
    from sat_sim.adapters.component_battery import BatteryAdapter

    spec = _native_battery_spec()
    spec["parameters"]["charge_efficiency"] = float("nan")
    with pytest.raises(ValueError, match="UNITY_EFFICIENCY"):
        BatteryAdapter().run(spec)


@pytest.mark.parametrize("component", ["battery", "solar_panel"])
@pytest.mark.parametrize("duration_s,sample_s,accepted", [
    (10.01, 1.001, True),
    (10.0000000001, 1.0000000009, False),
])
def test_native_time_grid_matches_basilisk_nanosecond_rounding(component, duration_s, sample_s, accepted):
    pytest.importorskip("Basilisk")
    from sat_sim.adapters.component_battery import BatteryAdapter
    from sat_sim.adapters.component_solar_panel import SolarPanelAdapter

    spec, adapter = ((_native_battery_spec(), BatteryAdapter()) if component == "battery"
                     else (_native_solar_panel_spec(), SolarPanelAdapter()))
    spec["simulation"].update(duration_s=duration_s, sample_s=sample_s)
    if accepted:
        result = adapter.run(spec)
        assert result.trace_rows[-1]["time_s"] == pytest.approx(10.01, abs=1e-12)
        assert len(result.trace_rows) == 11
    else:
        with pytest.raises(ValueError, match="TIME_GRID"):
            adapter.run(spec)
