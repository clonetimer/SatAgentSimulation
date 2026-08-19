from __future__ import annotations

import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.run_bundle import execute_prepared_run, prepare_run


def _spec() -> dict:
    values = {
        "initial_pointing_error_deg": 8.0,
        "orbit_radius_m": 7000000.0,
        "inclination_deg": 35.0,
        "initial_soc": 0.62,
        "battery_capacity_wh": 160.0,
        "solar_panel_area_m2": 2.5,
        "solar_efficiency": 0.28,
        "payload_power_w": 38.0,
        "payload_data_rate_bps": 2500000.0,
        "downlink_rate_bps": 1500000.0,
        "storage_capacity_bits": 6000000000.0,
    }
    return {
        "schema_version": "1.0.0",
        "task": {"id": "whole_traceability_fault", "name": "FMEA Episode traceability"},
        "simulation": {
            "level": "whole_spacecraft", "duration_s": 30.0, "step_s": 0.2,
            "sample_s": 1.0, "backend": "basilisk", "time_base": "simulation_seconds", "time_system": "UTC",
        },
        "model": {
            "capability_id": "whole_spacecraft.unified_native.v1",
            "target": {"level": "whole_spacecraft", "name": "whole_spacecraft", "mode": "fault"},
            "spacecraft": {"mission": {"template": "whole_spacecraft.unified_native.v1"}},
            "config": dict(values),
        },
        "parameters": {"profile": "demo", "values": dict(values), "overrides": []},
        "events": {
            "faults": [{
                "id": "battery_capacity_loss",
                "target": "whole_spacecraft",
                "effect": "eps_battery_capacity_loss",
                "start_s": 10.0,
                "end_s": 20.0,
                "parameters": {
                    "remaining_capacity_ratio": 0.5,
                    "fmea": {"severity": 8, "occurrence": 3, "detectability": 4},
                },
            }],
            "degradations": [],
            "constraints": [],
        },
        "outputs": {
            "output_root": "runs/whole_traceability_fault",
            "qoi": [
                "eps.battery_soc", "eps.battery_capacity_j",
                "eps.effective_battery_capacity_wh", "eps.battery_energy_j",
            ],
            "plots": [], "files": [],
            "telemetry_streams": [
                {
                    "stream_id": "eps_fault_evidence", "sample_s": 1.0,
                    "fields": [
                        "eps.battery_soc", "eps.battery_capacity_j", "eps.effective_battery_capacity_wh",
                        "eps.battery_energy_j", "label.fault_active", "event.active_effects",
                    ],
                    "format": "jsonl",
                },
                {
                    "stream_id": "eps_housekeeping", "sample_s": 5.0,
                    "fields": ["eps.battery_soc", "eps.battery_capacity_j"], "format": "csv",
                },
            ],
            "fmea": {"enabled": True, "formats": ["csv", "json"]},
            "include_summary": True, "include_trace": True, "include_labels": True, "include_manifest": True,
        },
        "assurance": {
            "allow_proxy": False, "claim_level": "analysis_only", "parameter_profile": "demo",
            "fidelity_level": "declared_by_capability", "validation_profile": "default",
        },
    }


def test_fmea_episode_native_telemetry_are_bound_in_run_bundle(tmp_path: Path) -> None:
    prepared = prepare_run(_spec(), output_root=tmp_path / "runs", run_id="traceability_run")
    result = execute_prepared_run(prepared.bundle_root, expected_plan_sha256=prepared.execution_plan_sha256)
    assert result.run_record.sealed is True
    root = Path(prepared.bundle_root)

    trace = json.loads((root / "results/dataset/traceability/fault_traceability.json").read_text(encoding="utf-8"))
    assert trace["schema_version"] == "sat-sim.fault-traceability.v1"
    assert trace["link_count"] == 1
    link = trace["links"][0]
    assert link["event_id"] == "battery_capacity_loss"
    assert link["episode_id"] == "episode::battery_capacity_loss"
    assert link["evidence_status"] == "observed"
    assert link["physical_effect_verified"] is True
    assert link["telemetry_coverage_status"] == "covered"
    assert {row["stream_id"] for row in link["telemetry_evidence"]} == {"eps_fault_evidence", "eps_housekeeping"}
    assert all(row["row_count"] > 0 for row in link["telemetry_evidence"])

    fmea = json.loads((root / "results/dataset/fmea/fmea.json").read_text(encoding="utf-8"))
    row = fmea["rows"][0]
    assert row["fmea_row_id"] == "fmea::battery_capacity_loss"
    assert row["traceability_link_id"] == "trace::battery_capacity_loss"
    assert row["evidence_status"] == "observed"
    assert row["physical_effect_verified"] is True
    assert row["rpn"] == 96
    assert "eps_fault_evidence" in row["telemetry_stream_ids"]

    events = json.loads((root / "results/events.json").read_text(encoding="utf-8"))
    assert events["episode_count"] == 1
    assert events["episodes"][0]["effect"] == "eps_battery_capacity_loss"


def test_traceability_api_returns_time_filtered_evidence_window(tmp_path: Path) -> None:
    prepared = prepare_run(_spec(), output_root=tmp_path / "runs", run_id="traceability_api")
    result = execute_prepared_run(prepared.bundle_root, expected_plan_sha256=prepared.execution_plan_sha256)
    assert result.run_record.sealed is True
    app = create_app(runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts", embedded_worker=False, auth_mode="disabled")
    with TestClient(app) as client:
        summary = client.get("/runs/traceability_api/fault-traceability")
        assert summary.status_code == 200
        assert summary.json()["traceability"]["telemetry_covered_count"] == 1

        event = client.get("/runs/traceability_api/fault-traceability/battery_capacity_loss")
        assert event.status_code == 200
        assert event.json()["link"]["validation_result"] == "PASS"

        window = client.get("/runs/traceability_api/fault-traceability/battery_capacity_loss/evidence-window")
        assert window.status_code == 200
        payload = window.json()
        assert len(payload["windows"]) == 2
        for stream in payload["windows"]:
            times = [float(row["time_s"]) for row in stream["rows"]]
            assert times
            assert min(times) >= 5.0 - 1e-9
            assert max(times) <= 25.0 + 1e-9

        filtered = client.get(
            "/runs/traceability_api/telemetry-streams/eps_fault_evidence?start_s=9&end_s=12&limit=100"
        )
        assert filtered.status_code == 200
        times = [float(row["time_s"]) for row in filtered.json()["rows"]]
        assert times == [9.0, 10.0, 11.0, 12.0]

        fmea = client.get("/runs/traceability_api/fmea")
        assert fmea.status_code == 200
        assert fmea.json()["traceability_summary"]["physical_effect_verified_count"] == 1


def test_workbench_exposes_fmea_traceability_tab() -> None:
    root = Path(__file__).parents[1] / "src" / "sat_sim" / "web"
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "app.js").read_text(encoding="utf-8")
    assert 'data-result-tab="traceability"' in html
    assert "fault-traceability" in js
    assert "查看证据窗口" in js
