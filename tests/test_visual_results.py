from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.assembly_graph import assembly_contract
from sat_sim.visual_results import build_visual_run_overlay


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    return TestClient(app)


def test_whole_spacecraft_overlay_maps_nodes_and_registered_binding_signals() -> None:
    contract = assembly_contract("whole_spacecraft.power_thermal_orbit_coupled.v1")
    graph = contract["default_graph"]
    rows = [
        {
            "time_s": 0.0,
            "environment.shadow_factor": 1.0,
            "orbit.altitude_m": 500000.0,
            "eps.battery.soc": 0.80,
            "eps.loads.requested_power_w": 11.0,
            "thermal.node.battery_temp_c": 21.0,
            "thermal.heater.power_w": 0.0,
        },
        {
            "time_s": 10.0,
            "environment.shadow_factor": 0.25,
            "orbit.altitude_m": 499950.0,
            "eps.battery.soc": 0.78,
            "eps.loads.requested_power_w": 12.5,
            "thermal.node.battery_temp_c": 22.5,
            "thermal.heater.power_w": 2.0,
        },
    ]
    overlay = build_visual_run_overlay(
        run_id="run-1",
        run_record={"status": "SUCCEEDED"},
        validation_outcome={"result": "PASS"},
        metrics_payload={"metrics": {"qoi.eps.final_soc": 0.78, "qoi.thermal.max_battery_temp_c": 22.5}},
        telemetry_rows=rows,
        assembly_graph=graph,
    )
    assert overlay["mode"] == "assembly"
    by_alias = {item["module_alias"]: item for item in overlay["node_overlays"]}
    assert by_alias["eps"]["headline"]["field"] == "eps.battery.soc"
    assert by_alias["thermal"]["headline"]["field"] == "thermal.node.battery_temp_c"
    assert by_alias["orbit_environment"]["headline"]["field"] == "environment.shadow_factor"
    shadow = next(item for item in overlay["edge_overlays"] if item["binding_id"] == "shadow_to_eps_solar")
    assert shadow["primary"]["field"] == "environment.shadow_factor"
    assert shadow["primary"]["last"] == 0.25
    assert shadow["primary"]["max"] == 1.0
    assert overlay["arbitrary_code_inspected"] is False


def test_adcs_overlay_maps_allocator_and_reaction_wheel_vector_signals() -> None:
    contract = assembly_contract("subsystem.adcs_fidelity.v1")
    graph = contract["default_graph"]
    graph["scopes"] = [
        {
            "id": "scope-rw",
            "name": "Wheel speed",
            "x": 850,
            "y": 80,
            "probes": [
                {
                    "id": "probe-rw-speed",
                    "sourceNode": "module-reaction_wheel",
                    "sourcePort": "reaction_wheel.out.wheel_speed_rad_s",
                }
            ],
        }
    ]
    rows = [
        {
            "time_s": 0.0,
            "adcs.control.command_torque_nm_0": 0.001,
            "adcs.control.command_torque_nm_1": -0.002,
            "adcs.control.command_torque_nm_2": 0.0005,
            "adcs.rw.speed_rad_s_0": 10.0,
            "adcs.rw.speed_rad_s_1": 12.0,
            "adcs.rw.speed_rad_s_2": 8.0,
            "adcs.rw.max_abs_speed_rad_s": 12.0,
            "adcs.pointing.error_deg": 4.0,
            "adcs.rw.saturation_flag": 0.0,
        },
        {
            "time_s": 1.0,
            "adcs.control.command_torque_nm_0": 0.002,
            "adcs.control.command_torque_nm_1": -0.001,
            "adcs.control.command_torque_nm_2": 0.0002,
            "adcs.rw.speed_rad_s_0": 11.0,
            "adcs.rw.speed_rad_s_1": 13.0,
            "adcs.rw.speed_rad_s_2": 8.5,
            "adcs.rw.max_abs_speed_rad_s": 13.0,
            "adcs.pointing.error_deg": 2.0,
            "adcs.rw.saturation_flag": 0.0,
        },
    ]
    overlay = build_visual_run_overlay(
        run_id="adcs-run",
        run_record={"status": "SUCCEEDED"},
        validation_outcome={"result": "PASS"},
        metrics_payload={"metrics": {"qoi.adcs.final_pointing_error_deg": 2.0}},
        telemetry_rows=rows,
        assembly_graph=graph,
    )
    by_alias = {item["module_alias"]: item for item in overlay["node_overlays"]}
    assert by_alias["reaction_wheel"]["headline"]["field"] == "adcs.rw.max_abs_speed_rad_s"
    assert by_alias["rigid_body"]["headline"]["field"] == "adcs.pointing.error_deg"
    allocator = next(item for item in overlay["edge_overlays"] if item["binding_id"] == "allocator_to_reaction_wheel")
    assert allocator["primary"]["field"] == "adcs.control.command_torque_nm_0"
    speed = next(item for item in overlay["edge_overlays"] if item["binding_id"] == "reaction_wheel_speed_to_saturation_monitor")
    assert speed["primary"]["field"] == "adcs.rw.speed_rad_s_0"
    scope = overlay["scope_overlays"][0]
    assert scope["scope_id"] == "scope-rw"
    assert scope["resolved_probe_count"] == 1
    assert scope["probes"][0]["primary"]["field"] == "adcs.rw.speed_rad_s_0"
    assert scope["probes"][0]["source_path"] == "reaction_wheel.wheel_speed_rad_s"



def test_v14_observer_overlay_preserves_scope_display_workspace_kinds() -> None:
    contract = assembly_contract("subsystem.adcs_fidelity.v1")
    graph = json.loads(json.dumps(contract["default_graph"]))
    graph["scopes"] = [
        {
            "kind": "scope",
            "id": "scope-rw",
            "name": "RW waveform",
            "probes": [{"id": "p1", "sourceNode": "module-reaction_wheel", "sourcePort": "reaction_wheel.out.wheel_speed_rad_s"}],
        },
        {
            "kind": "display",
            "id": "display-rw",
            "name": "RW value",
            "probes": [{"id": "p2", "sourceNode": "module-reaction_wheel", "sourcePort": "reaction_wheel.out.wheel_speed_rad_s"}],
        },
        {
            "kind": "workspace",
            "id": "workspace-rw",
            "name": "RW export",
            "probes": [{"id": "p3", "sourceNode": "module-reaction_wheel", "sourcePort": "reaction_wheel.out.wheel_speed_rad_s"}],
        },
    ]
    overlay = build_visual_run_overlay(
        run_id="observer-run",
        run_record={"status": "SUCCEEDED"},
        validation_outcome={"result": "PASS"},
        metrics_payload={"metrics": {}},
        telemetry_rows=[
            {"time_s": 0.0, "adcs.rw.speed_rad_s_0": 10.0, "adcs.rw.speed_rad_s_1": 11.0, "adcs.rw.speed_rad_s_2": 12.0},
            {"time_s": 1.0, "adcs.rw.speed_rad_s_0": 13.0, "adcs.rw.speed_rad_s_1": 14.0, "adcs.rw.speed_rad_s_2": 15.0},
        ],
        assembly_graph=graph,
    )
    assert overlay["observer_overlays"] == overlay["scope_overlays"]
    by_id = {item["observer_id"]: item for item in overlay["observer_overlays"]}
    assert by_id["scope-rw"]["observer_kind"] == "scope"
    assert by_id["display-rw"]["observer_kind"] == "display"
    assert by_id["workspace-rw"]["observer_kind"] == "workspace"
    assert by_id["display-rw"]["probes"][0]["primary"]["last"] == 13.0
    assert by_id["workspace-rw"]["series_count"] == 3

def test_scope_probe_rejects_source_node_and_port_alias_mismatch() -> None:
    contract = assembly_contract("subsystem.adcs_fidelity.v1")
    graph = json.loads(json.dumps(contract["default_graph"]))
    graph["scopes"] = [{
        "id": "scope-bad",
        "name": "Bad observer",
        "probes": [{
            "id": "probe-bad",
            "sourceNode": "module-rigid_body",
            "sourcePort": "reaction_wheel.out.wheel_speed_rad_s",
        }],
    }]
    overlay = build_visual_run_overlay(
        run_id="adcs-run",
        run_record={"status": "SUCCEEDED"},
        validation_outcome={"result": "PASS"},
        metrics_payload={"metrics": {}},
        telemetry_rows=[{"time_s": 0.0, "adcs.rw.speed_rad_s_0": 10.0}],
        assembly_graph=graph,
    )
    probe = overlay["scope_overlays"][0]["probes"][0]
    assert probe["endpoint_matches"] is False
    assert probe["resolved"] is False
    assert probe["signals"] == []
    assert probe["unresolved_reason"] == "scope_endpoint_mismatch"


def test_visual_run_overlay_api_reads_run_bundle_without_executing_code(tmp_path: Path) -> None:
    run_id = "overlay-run"
    root = tmp_path / "runs" / run_id
    (root / "results").mkdir(parents=True)
    (root / "validation").mkdir(parents=True)
    (root / "run_record.json").write_text(json.dumps({"status": "SUCCEEDED", "validation_result": "PASS"}), encoding="utf-8")
    (root / "validation" / "validation_outcome.json").write_text(json.dumps({"result": "PASS"}), encoding="utf-8")
    (root / "results" / "metrics.json").write_text(json.dumps({"metrics": {"qoi.primary": 1.25}}), encoding="utf-8")
    with (root / "results" / "telemetry.jsonl").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"time_s": 0.0, "adcs.rw.speed_rad_s_0": 5.0}) + "\n")
        handle.write(json.dumps({"time_s": 1.0, "adcs.rw.speed_rad_s_0": 6.0}) + "\n")
    graph = assembly_contract("subsystem.adcs_fidelity.v1")["default_graph"]
    with _client(tmp_path) as client:
        response = client.post("/visual-composer/run-overlay", json={"run_id": run_id, "assembly_graph": graph})
    assert response.status_code == 200
    overlay = response.json()["overlay"]
    assert overlay["schema_version"] == "sat-sim.visual-run-overlay.v1"
    assert overlay["run_status"] == "SUCCEEDED"
    assert overlay["validation_result"] == "PASS"
    assert overlay["telemetry_row_count"] == 2
    assert overlay["numeric_series_count"] == 1
    assert overlay["arbitrary_code_inspected"] is False



def test_v14_to_workspace_export_streams_full_recorded_telemetry(tmp_path: Path) -> None:
    run_id = "workspace-export-run"
    root = tmp_path / "runs" / run_id
    (root / "results").mkdir(parents=True)
    with (root / "results" / "telemetry.jsonl").open("w", encoding="utf-8") as handle:
        for index in range(5105):
            handle.write(json.dumps({
                "time_s": float(index),
                "adcs.rw.speed_rad_s_0": float(index) + 1.0,
                "adcs.rw.speed_rad_s_1": float(index) + 2.0,
                "adcs.rw.speed_rad_s_2": float(index) + 3.0,
                "unrelated.field": 99.0,
            }) + "\n")
    graph = json.loads(json.dumps(assembly_contract("subsystem.adcs_fidelity.v1")["default_graph"]))
    graph["scopes"] = [{
        "kind": "workspace",
        "id": "workspace-rw",
        "name": "RW Export",
        "probes": [{
            "id": "probe-rw",
            "sourceNode": "module-reaction_wheel",
            "sourcePort": "reaction_wheel.out.wheel_speed_rad_s",
        }],
    }]
    with _client(tmp_path) as client:
        response = client.post(
            "/visual-composer/export-observer-data",
            json={"run_id": run_id, "assembly_graph": graph, "observer_id": "workspace-rw", "format": "csv"},
        )
        json_response = client.post(
            "/visual-composer/export-observer-data",
            json={"run_id": run_id, "assembly_graph": graph, "observer_id": "workspace-rw", "format": "json"},
        )
    assert response.status_code == 200
    assert response.headers["x-sat-sim-observer-runtime-effect"] == "none"
    assert "RW_Export_workspace-export-run.csv" in response.headers["content-disposition"]
    lines = response.text.strip().splitlines()
    assert len(lines) == 5106  # header + all rows, not the 5000-row workbench preview cap
    assert lines[0].split(",") == [
        "time_s",
        "adcs.rw.speed_rad_s_0",
        "adcs.rw.speed_rad_s_1",
        "adcs.rw.speed_rad_s_2",
    ]
    assert "unrelated.field" not in lines[0]
    assert json_response.status_code == 200
    payload = json_response.json()
    assert payload["schema_version"] == "sat-sim.observer-export.v1"
    assert payload["observer_kind"] == "workspace"
    assert payload["runtime_effect"] == "none"
    assert len(payload["rows"]) == 5105
    assert payload["rows"][-1]["adcs.rw.speed_rad_s_0"] == 5105.0


def test_v14_observer_data_export_rejects_non_workspace_sink(tmp_path: Path) -> None:
    run_id = "scope-export-rejected"
    root = tmp_path / "runs" / run_id
    (root / "results").mkdir(parents=True)
    (root / "results" / "telemetry.jsonl").write_text(
        json.dumps({"time_s": 0.0, "adcs.rw.speed_rad_s_0": 1.0}) + "\n",
        encoding="utf-8",
    )
    graph = json.loads(json.dumps(assembly_contract("subsystem.adcs_fidelity.v1")["default_graph"]))
    graph["scopes"] = [{
        "kind": "scope",
        "id": "scope-rw",
        "probes": [{"id": "p1", "sourceNode": "module-reaction_wheel", "sourcePort": "reaction_wheel.out.wheel_speed_rad_s"}],
    }]
    with _client(tmp_path) as client:
        response = client.post(
            "/visual-composer/export-observer-data",
            json={"run_id": run_id, "assembly_graph": graph, "observer_id": "scope-rw", "format": "csv"},
        )
    assert response.status_code == 422
    assert response.json()["detail"]["reason_code"] == "OBSERVER_EXPORT_REQUIRES_WORKSPACE_SINK"

def test_visual_run_overlay_api_requires_exactly_one_graph_source(tmp_path: Path) -> None:
    root = tmp_path / "runs" / "r1"
    root.mkdir(parents=True)
    with _client(tmp_path) as client:
        both = client.post("/visual-composer/run-overlay", json={"run_id": "r1", "visual_graph": {}, "assembly_graph": {}})
        missing = client.post("/visual-composer/run-overlay", json={"run_id": "r1"})
    assert both.status_code == 422
    assert both.json()["detail"]["reason_code"] == "VISUAL_RUN_OVERLAY_MULTIPLE_GRAPH_SOURCES"
    assert missing.status_code == 422
    assert missing.json()["detail"]["reason_code"] == "VISUAL_RUN_OVERLAY_GRAPH_REQUIRED"
