from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.catalog_consistency import catalog_document_consistency
from sat_sim.form_schema import capability_form_schema
from sat_sim.execution_planner import plan_task_spec
from sat_sim.product_closure import product_capability_closure
from sat_sim.release_closure import RELEASE_VERSION
from sat_sim.scenario_templates import list_scenario_templates
from sat_sim.workbench_catalog import workbench_presentation_catalog


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    ))


def test_physical_backlog_is_explicitly_closed_for_release():
    payload = product_capability_closure()
    decisions = payload["migration_backlog_decisions"]
    assert len(decisions) == 7
    assert all(item["status"] in {"closed", "closed_for_release"} for item in decisions)
    assert {
        "whole_spacecraft.unified_native.v1",
        "subsystem.adcs_unified_native.v1",
        "subsystem.eps.unified_native.v1",
        "subsystem.comm_data.unified_native.v1",
        "subsystem.propulsion.unified_native.v1",
    }.issubset(set(payload["recommended_capability_ids"]))


def test_default_workbench_prioritizes_unified_runtime_and_hides_legacy_entries():
    default = workbench_presentation_catalog()
    objects = {item["object_id"]: item for item in default["objects"]}
    assert objects["whole_spacecraft"]["primary_capability_id"] == "whole_spacecraft.unified_native.v1"
    assert objects["subsystem.adcs"]["primary_capability_id"] == "subsystem.adcs_unified_native.v1"
    assert objects["subsystem.eps"]["primary_capability_id"] == "subsystem.eps.unified_native.v1"
    assert objects["subsystem.comm_data"]["primary_capability_id"] == "subsystem.comm_data.unified_native.v1"
    assert objects["subsystem.propulsion"]["primary_capability_id"] == "subsystem.propulsion.unified_native.v1"
    for item in default["objects"]:
        for variant in item.get("variants", []):
            assert variant["product_tier"] not in {"compatibility", "explicit", "blocked"}
            assert variant["lifecycle_status"] not in {"deprecated", "internal", "archived", "blocked"}

    opt_in = workbench_presentation_catalog(include_compatibility=True, include_explicit=True)
    opt_in_ids = {
        variant["capability_id"]
        for item in opt_in["objects"]
        for variant in item.get("variants", [])
    }
    assert "component.antenna.v1" in opt_in_ids


def test_default_scenario_template_api_hides_historical_and_explicit_entries(tmp_path: Path):
    all_templates = {item["template_id"] for item in list_scenario_templates()["templates"]}
    assert "bsksim_foundation_orbit_attitude" in all_templates
    assert "bsksim_whole_spacecraft_strong_coupling" in all_templates

    with _client(tmp_path) as client:
        default_ids = {item["template_id"] for item in client.get("/scenario-templates").json()["templates"]}
        assert "bsksim_foundation_orbit_attitude" not in default_ids
        assert "bsksim_whole_spacecraft_strong_coupling" not in default_ids
        opt_in_ids = {
            item["template_id"]
            for item in client.get("/scenario-templates?include_compatibility=true&include_explicit=true").json()["templates"]
        }
        assert "bsksim_foundation_orbit_attitude" in opt_in_ids
        assert "bsksim_whole_spacecraft_strong_coupling" in opt_in_ids



def test_recommended_unified_event_templates_have_valid_owner_targets():
    from sat_sim.scenario_templates import instantiate_scenario_template

    for template_id in (
        "adcs_unified_native_event_chain",
        "whole_spacecraft_unified_native_fault_chain",
        "whole_spacecraft_unified_native_degradation_chain",
    ):
        spec = instantiate_scenario_template(template_id)
        planning = plan_task_spec(spec)
        assert planning.validation.ok, [issue.to_dict() for issue in planning.validation.issues]

def test_default_form_exposes_multirate_telemetry_and_fmea_configuration():
    form = capability_form_schema("whole_spacecraft.unified_native.v1")["default_form"]
    assert form["outputs"]["telemetry_streams"] == []
    assert form["outputs"]["fmea"] == {"enabled": False, "formats": ["csv", "json"]}


def test_fmea_filter_download_and_traceability_summary(tmp_path: Path):
    run_root = tmp_path / "runs" / "run-fmea"
    fmea_root = run_root / "results" / "dataset" / "fmea"
    trace_root = run_root / "results" / "dataset" / "traceability"
    fmea_root.mkdir(parents=True)
    trace_root.mkdir(parents=True)
    (fmea_root / "fmea_manifest.json").write_text(json.dumps({"schema_version": "fmea.v1", "row_count": 2}), encoding="utf-8")
    rows = [
        {
            "event_id": "battery_loss",
            "category": "fault",
            "failure_mode": "Battery capacity loss",
            "target": "battery",
            "rating_status": "rated",
            "evidence_status": "verified",
            "physical_effect_verified": True,
            "rpn": 120,
            "local_effect": "capacity ratio decreased",
            "system_effect": "SOC falls faster",
        },
        {
            "event_id": "solar_loss",
            "category": "degradation",
            "failure_mode": "Solar efficiency loss",
            "target": "solar_panel",
            "rating_status": "unrated",
            "evidence_status": "candidate",
            "physical_effect_verified": False,
            "rpn": None,
        },
    ]
    (fmea_root / "fmea.json").write_text(json.dumps({"schema_version": "fmea.table.v1", "rows": rows}), encoding="utf-8")
    (trace_root / "fault_traceability.json").write_text(json.dumps({
        "schema_version": "trace.v1",
        "link_count": 2,
        "episode_linked_count": 2,
        "telemetry_covered_count": 1,
        "physical_effect_verified_count": 1,
        "validation_result": "PASS",
    }), encoding="utf-8")

    with _client(tmp_path) as client:
        response = client.get(
            "/runs/run-fmea/fmea?category=fault&rating_status=rated&physical_effect_verified=true&search=battery"
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["filtered_row_count"] == 1
        assert payload["source_row_count"] == 2
        assert payload["table"]["rows"][0]["event_id"] == "battery_loss"
        assert payload["traceability_summary"]["validation_result"] == "PASS"

        csv_download = client.get("/runs/run-fmea/fmea/download?format=csv&category=fault")
        assert csv_download.status_code == 200
        assert "run-fmea_fmea_filtered.csv" in csv_download.headers["content-disposition"]
        assert "battery_loss" in csv_download.content.decode("utf-8-sig")
        assert "solar_loss" not in csv_download.content.decode("utf-8-sig")

        json_download = client.get("/runs/run-fmea/fmea/download?format=json&physical_effect_verified=true")
        assert json_download.status_code == 200
        exported = json.loads(json_download.content.decode("utf-8"))
        assert exported["filtered_row_count"] == 1


def test_product_closure_endpoint_and_catalog_consistency(tmp_path: Path):
    with _client(tmp_path) as client:
        response = client.get("/capabilities/product-closure")
        assert response.status_code == 200
        assert response.json()["product_closure"]["release_version"] == RELEASE_VERSION
    consistency = catalog_document_consistency()
    assert consistency["status"] == "PASS"
    assert consistency["error_count"] == 0


def test_workbench_assets_contain_v0559_controls():
    root = Path(__file__).resolve().parents[1]
    html = (root / "src" / "sat_sim" / "web" / "index.html").read_text(encoding="utf-8")
    js = (root / "src" / "sat_sim" / "web" / "app.js").read_text(encoding="utf-8")
    for token in (
        "showCompatibilityCapabilities", "addTelemetryStreamBtn", "telemetryStreamList",
        "fmeaEnabled", "fmeaSearch", "downloadFmeaCsvBtn", "downloadFmeaJsonBtn", "fmeaTable",
    ):
        assert token in html
    for token in ("renderAdvancedOutputs", "addTelemetryStream", "renderFmeaTable", "fmeaDownloadQuery"):
        assert token in js
