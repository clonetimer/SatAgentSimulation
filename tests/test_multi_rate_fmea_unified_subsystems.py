from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.capability_planner import plan_capability_for_request
from sat_sim.fmea import build_fmea_rows
from sat_sim.task_compiler import compile_task_spec
from sat_sim.task_models import CanonicalTaskSpec, to_runtime_task_spec
from sat_sim.task_runner import run_compiled_task
from sat_sim.run_bundle import execute_prepared_run, prepare_run


def _load(name: str) -> dict:
    return yaml.safe_load((Path(__file__).parents[1] / "examples" / name).read_text(encoding="utf-8"))


def test_multirate_contract_rejects_non_multiple() -> None:
    spec = _load("subsystem_eps_unified_native_nominal.yaml")
    spec["outputs"]["telemetry_streams"][1]["sample_s"] = 2.5
    with pytest.raises(ValidationError, match="integer multiple"):
        CanonicalTaskSpec.model_validate(spec)


def test_fmea_does_not_fabricate_risk_scores() -> None:
    spec = _load("subsystem_eps_unified_native_nominal.yaml")
    spec["events"] = {
        "faults": [{
            "id": "battery_capacity_loss",
            "target": "battery",
            "effect": "eps_battery_capacity_loss",
            "start_s": 10.0,
            "parameters": {},
        }],
        "degradations": [],
        "constraints": [],
    }
    rows = build_fmea_rows(spec)
    assert len(rows) == 1
    assert rows[0].rpn is None
    assert rows[0].rating_status == "unrated_no_fabricated_score"


def test_fmea_computes_explicit_rpn() -> None:
    spec = _load("subsystem_eps_unified_native_nominal.yaml")
    spec["events"] = {
        "faults": [{
            "id": "battery_capacity_loss",
            "target": "battery",
            "effect": "eps_battery_capacity_loss",
            "start_s": 10.0,
            "parameters": {"fmea": {"severity": 8, "occurrence": 3, "detectability": 4}},
        }],
        "degradations": [],
        "constraints": [],
    }
    rows = build_fmea_rows(spec)
    assert rows[0].rpn == 96
    assert rows[0].rating_status == "rated"


@pytest.mark.parametrize(
    "example,capability_id,stream_ids",
    [
        ("subsystem_eps_unified_native_nominal.yaml", "subsystem.eps.unified_native.v1", {"eps_fast", "eps_housekeeping"}),
        ("subsystem_comm_data_unified_native_nominal.yaml", "subsystem.comm_data.unified_native.v1", {"data_fast", "data_summary"}),
    ],
)
def test_unified_subsystem_run_and_multirate_exports(tmp_path: Path, example: str, capability_id: str, stream_ids: set[str]) -> None:
    canonical = CanonicalTaskSpec.model_validate(_load(example)).model_dump(mode="json")
    runtime = to_runtime_task_spec(canonical)
    compiled = compile_task_spec(runtime)
    result = run_compiled_task(compiled, task_spec=canonical, output_root=tmp_path / capability_id)
    assert result.summary["status"] == "PASS"
    assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded"
    assert result.summary["external_or_proxy_module_count"] == 0
    assert result.dataset is not None
    manifest_path = result.dataset.output_root / "telemetry" / "multi_rate_manifest.json"
    telemetry_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert {item["stream_id"] for item in telemetry_manifest["streams"]} == stream_ids
    assert telemetry_manifest["interpolation_used"] is False
    assert (result.dataset.output_root / "fmea" / "fmea.csv").exists()
    assert (result.dataset.output_root / "fmea" / "fmea.json").exists()


def test_planner_prefers_new_unified_subsystems() -> None:
    eps = plan_capability_for_request("创建一个电源统一运行图，输出SOC和净功率")
    comm = plan_capability_for_request("创建通信数据统一运行图，输出存储量和下行数据率")
    assert eps.selected_capability_id == "subsystem.eps.unified_native.v1"
    assert comm.selected_capability_id == "subsystem.comm_data.unified_native.v1"


def test_multirate_and_fmea_api_reads_sealed_run(tmp_path: Path) -> None:
    spec = _load("subsystem_eps_unified_native_nominal.yaml")
    prepared = prepare_run(spec, output_root=tmp_path / "runs", run_id="eps_multirate_api")
    result = execute_prepared_run(prepared.bundle_root, expected_plan_sha256=prepared.execution_plan_sha256)
    assert result.run_record.sealed is True

    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    with TestClient(app) as client:
        manifest = client.get("/runs/eps_multirate_api/telemetry-streams")
        assert manifest.status_code == 200
        assert {item["stream_id"] for item in manifest.json()["multi_rate_telemetry"]["streams"]} == {"eps_fast", "eps_housekeeping"}

        stream = client.get("/runs/eps_multirate_api/telemetry-streams/eps_housekeeping?limit=5")
        assert stream.status_code == 200
        payload = stream.json()
        assert payload["stream"]["stream_id"] == "eps_housekeeping"
        assert payload["count"] <= 5

        fmea = client.get("/runs/eps_multirate_api/fmea")
        assert fmea.status_code == 200
        assert "no risk score is fabricated" in fmea.json()["manifest"]["warning"]
