from __future__ import annotations

import hashlib
import io
import json
import zipfile
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
from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle


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


@pytest.mark.parametrize(
    "example,run_id,expected_streams,housekeeping_stream",
    [
        (
            "subsystem_eps_unified_native_nominal.yaml",
            "eps_multirate_api",
            {"eps_fast", "eps_housekeeping"},
            "eps_housekeeping",
        ),
        (
            "subsystem_comm_data_unified_native_nominal.yaml",
            "comm_data_multirate_api",
            {"data_fast", "data_summary"},
            "data_summary",
        ),
    ],
)
def test_multirate_and_fmea_api_reads_sealed_run(
    tmp_path: Path,
    example: str,
    run_id: str,
    expected_streams: set[str],
    housekeeping_stream: str,
) -> None:
    pytest.importorskip("Basilisk")
    spec = _load(example)
    spec["task"]["id"] = run_id
    prepared = prepare_run(spec, output_root=tmp_path / "runs", run_id=run_id)
    result = execute_prepared_run(prepared.bundle_root, expected_plan_sha256=prepared.execution_plan_sha256)
    assert result.run_record.status.value == "SUCCEEDED"
    assert result.run_record.sealed is True
    assert result.validation.result.value == "PASS"
    assert verify_run_bundle(prepared.bundle_root)["ok"] is True

    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    with TestClient(app) as client:
        manifest = client.get(f"/runs/{run_id}/telemetry-streams")
        assert manifest.status_code == 200
        stream_manifest = manifest.json()["multi_rate_telemetry"]
        assert {item["stream_id"] for item in stream_manifest["streams"]} == expected_streams
        assert stream_manifest["interpolation_used"] is False

        stream = client.get(f"/runs/{run_id}/telemetry-streams/{housekeeping_stream}?limit=5")
        assert stream.status_code == 200
        payload = stream.json()
        assert payload["stream"]["stream_id"] == housekeeping_stream
        assert 0 < payload["count"] <= 5

        fmea = client.get(f"/runs/{run_id}/fmea")
        assert fmea.status_code == 200
        assert "no risk score is fabricated" in fmea.json()["manifest"]["warning"]

        bundle = client.get(f"/runs/{run_id}/bundle/download")
        assert bundle.status_code == 200, bundle.text
        assert bundle.headers["X-Sat-Sim-Run-ID"] == run_id
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            prefix = f"{run_id}/"
            manifest_name = f"{prefix}bundle_manifest.json"
            seal_name = f"{prefix}SEALED.json"
            names = set(archive.namelist())
            manifest_bytes = archive.read(manifest_name)
            bundle_manifest = json.loads(manifest_bytes)
            seal = json.loads(archive.read(seal_name))
            expected_files = {f"{prefix}{relative}" for relative in bundle_manifest["files"]}
            assert names == expected_files | {manifest_name, seal_name}
            assert seal["run_id"] == run_id
            assert seal["bundle_manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
            assert bundle.headers["X-Sat-Sim-Run-Bundle-Manifest-SHA256"] == seal["bundle_manifest_sha256"]
            for relative, entry in bundle_manifest["files"].items():
                assert hashlib.sha256(archive.read(f"{prefix}{relative}")).hexdigest() == entry["sha256"]
