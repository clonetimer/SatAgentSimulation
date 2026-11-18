from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize(
    "solar_power_w,loads_w,nominal_solar_w,direction",
    [
        pytest.param(
            240.0,
            {"bus": 12.0, "payload": 16.0, "adcs": 8.0, "comm": 4.0},
            60.0,
            1,
            id="charging",
        ),
        pytest.param(
            80.0,
            {"bus": 18.0, "payload": 38.0, "adcs": 20.0, "comm": 12.0},
            20.0,
            -1,
            id="discharging",
        ),
    ],
)
def test_native_eps_sealed_bundle_conserves_recorded_energy(
    tmp_path: Path,
    solar_power_w: float,
    loads_w: dict[str, float],
    nominal_solar_w: float,
    direction: int,
) -> None:
    """Catch broken native wiring, ignored loads, and power/energy unit errors."""
    pytest.importorskip("Basilisk")
    from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle

    example = Path(__file__).parents[1] / "examples" / "subsystem_eps_unified_native_nominal.yaml"
    spec = yaml.safe_load(example.read_text(encoding="utf-8"))
    run_id = "native_eps_charge" if direction > 0 else "native_eps_discharge"
    spec["task"]["id"] = run_id
    spec["parameters"]["values"].update(
        initial_soc=0.70,
        battery_capacity_wh=160.0,
        solar_power_w=solar_power_w,
        **{f"{name}_power_w": power_w for name, power_w in loads_w.items()},
    )

    prepared = prepare_run(spec, output_root=tmp_path / "runs", run_id=run_id)
    assert prepared.primary_capability_id == "subsystem.eps.unified_native.v1"
    executed = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1,
    )

    assert executed.run_record.status.value == "SUCCEEDED", executed.run_record.model_dump(mode="json")
    assert executed.run_record.sealed is True
    assert executed.validation.result.value == "PASS", executed.validation.model_dump(mode="json")
    integrity = verify_run_bundle(prepared.bundle_root)
    assert integrity["ok"] is True, integrity

    summary = executed.summary
    assert summary["status"] == "PASS"
    assert summary["basilisk_simbase_used"] is True
    assert summary["execute_simulation_used"] is True
    assert summary["runtime_truth_status"] == "instantiated_connected_recorded"
    assert summary["external_or_proxy_module_count"] == 0
    assert {
        "simpleSolarPanel.SimpleSolarPanel",
        "simpleBattery.SimpleBattery",
        "simplePowerSink.SimplePowerSink",
    } <= set(summary["native_modules"])
    assert "components.pdu.builder.PduLoadSheddingSysModel" in summary["custom_modules"]

    # Inspect the persisted evidence that the bundle integrity check protects.
    assert executed.dataset is not None
    manifest_path = Path(executed.dataset.output_root) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence = manifest["runtime_evidence"]
    boundary = evidence["model_source_boundary"]
    required_official = {"solar_power_node", "battery", "bus", "payload", "adcs", "comm"}
    assert required_official <= set(boundary["basilisk_official_native"])
    assert "pdu" in boundary["basilisk_project_native"]
    assert "pdu" not in boundary["basilisk_official_native"]
    assert boundary["external_or_proxy"] == []
    runtime = evidence["runtime_manifest"]
    assert required_official | {"pdu"} <= set(runtime["instantiated_modules"])
    assert {"solar", "battery", "bus", "payload", "adcs", "comm"} <= set(runtime["recorded_sources"])

    rows = executed.trace_rows
    assert len(rows) >= 2
    assert rows[0]["time_s"] == pytest.approx(0.0)
    assert rows[-1]["time_s"] == pytest.approx(spec["simulation"]["duration_s"])
    assert rows[0]["eps.battery_storage_j"] == pytest.approx(403_200.0)
    assert summary["load_shed_event_count"] == 0
    load_fields = {
        "bus": "eps.bus_load_w",
        "payload": "eps.payload_load_enabled_w",
        "adcs": "eps.adcs_load_enabled_w",
        "comm": "eps.comm_load_enabled_w",
    }
    for row in rows:
        assert row["eps.battery_capacity_j"] == pytest.approx(576_000.0)
        # Keep this conservation check away from storage clamps and PDU thresholds.
        assert 0.60 < row["eps.battery_soc"] < 0.80
        assert row["eps.battery_soc"] == pytest.approx(
            row["eps.battery_storage_j"] / 576_000.0,
        )
        assert row["eps.load_shed_active"] is False
        for name, field in load_fields.items():
            assert row[field] == pytest.approx(loads_w[name])
        assert row["eps.heater_load_enabled_w"] == pytest.approx(0.0)
        # The fixed Sun is near one AU; allow its distance/solar-flux correction.
        # Efficiency is 0.25, so a constant source using raw solar_power_w fails.
        assert row["eps.solar_power_w"] == pytest.approx(nominal_solar_w, rel=0.02)
        assert row["eps.net_power_w"] == pytest.approx(
            row["eps.solar_power_w"] - sum(row[field] for field in load_fields.values()),
            abs=1e-9,
        )
        assert direction * row["eps.net_power_w"] > 0.0

    integrated_energy_j = 0.0
    for previous, current in zip(rows, rows[1:]):
        elapsed_s = current["time_s"] - previous["time_s"]
        assert elapsed_s > 0.0
        assert elapsed_s == pytest.approx(spec["simulation"]["step_s"])
        assert current["eps.net_power_w"] == pytest.approx(previous["eps.net_power_w"])
        interval_energy_j = current["eps.net_power_w"] * elapsed_s
        storage_change_j = current["eps.battery_storage_j"] - previous["eps.battery_storage_j"]
        assert storage_change_j == pytest.approx(interval_energy_j, rel=1e-9, abs=1e-7)
        integrated_energy_j += interval_energy_j

    measured_change_j = rows[-1]["eps.battery_storage_j"] - rows[0]["eps.battery_storage_j"]
    assert direction * measured_change_j > 0.0
    assert measured_change_j == pytest.approx(integrated_energy_j, rel=1e-9, abs=1e-6)
    assert summary["final_storage_j"] == pytest.approx(rows[-1]["eps.battery_storage_j"])
    assert summary["final_soc"] == pytest.approx(rows[-1]["eps.battery_soc"])
