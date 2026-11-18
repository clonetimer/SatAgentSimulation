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
    assert executed.trace_rows
    assert executed.trace_rows[-1]["eps.battery.soc"] < executed.trace_rows[0]["eps.battery.soc"]
    assert verify_run_bundle(prepared.bundle_root)["ok"] is True
