from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml

from sat_sim.capability_registry import get_adapter_for_capability, get_capability
from sat_sim.run_bundle import execute_prepared_run, prepare_run


def _example(name: str) -> dict:
    return yaml.safe_load((Path(__file__).parents[1] / "examples" / name).read_text(encoding="utf-8"))


def _manifest(bundle_root: Path | str) -> dict:
    path = Path(bundle_root) / "results" / "dataset" / "telemetry" / "multi_rate_manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_eps_uses_basilisk_native_grouped_recorders(tmp_path: Path) -> None:
    spec = _example("subsystem_eps_unified_native_nominal.yaml")
    prepared = prepare_run(spec, output_root=tmp_path, run_id="eps_native_recorder_groups")
    result = execute_prepared_run(prepared.bundle_root, expected_plan_sha256=prepared.execution_plan_sha256)
    assert result.run_record.sealed is True
    manifest = _manifest(prepared.bundle_root)
    assert manifest["schema_version"] == "sat-sim.multi-rate-telemetry.v2"
    assert manifest["recorder_mode"] == "basilisk_message_recorder_groups"
    assert manifest["same_writer_task"] is True
    assert manifest["post_run_downsampling"] is False
    streams = {row["stream_id"]: row for row in manifest["streams"]}
    assert streams["eps_fast"]["selection_policy"] == "basilisk_native_recorder_no_postprocess_sampling"
    assert streams["eps_housekeeping"]["row_count"] == 7
    with (Path(prepared.bundle_root) / "results" / "dataset" / streams["eps_housekeeping"]["file"]).open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [round(float(row["time_s"]), 9) for row in rows] == [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0]


def test_comm_data_uses_basilisk_native_grouped_recorders(tmp_path: Path) -> None:
    spec = _example("subsystem_comm_data_unified_native_nominal.yaml")
    prepared = prepare_run(spec, output_root=tmp_path, run_id="comm_native_recorder_groups")
    result = execute_prepared_run(prepared.bundle_root, expected_plan_sha256=prepared.execution_plan_sha256)
    assert result.run_record.sealed is True
    manifest = _manifest(prepared.bundle_root)
    assert manifest["recorder_mode"] == "basilisk_message_recorder_groups"
    assert {row["stream_id"] for row in manifest["native_groups"]} == {"data_fast", "data_summary"}
    assert all(row["native_recorder"] for row in manifest["native_groups"])


def test_recommended_whole_runtime_exposes_native_recorder_groups() -> None:
    spec = _example("whole_spacecraft_unified_native_nominal.yaml")
    spec["outputs"]["telemetry_streams"] = [
        {"stream_id": "adcs_fast", "sample_s": 2.0, "fields": ["adcs.pointing_error_deg"], "format": "csv"},
        {"stream_id": "housekeeping", "sample_s": 10.0, "fields": ["eps.battery_soc"], "format": "jsonl"},
    ]
    adapter = get_adapter_for_capability("whole_spacecraft.unified_native.v1")
    result = adapter.run(spec, get_capability("whole_spacecraft.unified_native.v1").data)
    payload = result.metadata["native_multi_rate_telemetry"]
    assert payload["recorder_mode"] == "basilisk_message_recorder_groups"
    assert [(row["stream_id"], len(row["rows"])) for row in payload["streams"]] == [
        ("adcs_fast", 61), ("housekeeping", 13)
    ]
    assert all(row["task_name"] == "UnifiedNativeTask" for row in payload["streams"])


def test_legacy_export_fallback_is_explicitly_postprocess(tmp_path: Path) -> None:
    from sat_sim.multi_rate_telemetry import write_multi_rate_telemetry

    payload = write_multi_rate_telemetry(
        output_root=tmp_path,
        rows=[{"time_s": 0.0, "x": 1}, {"time_s": 1.0, "x": 2}, {"time_s": 2.0, "x": 3}],
        base_sample_s=1.0,
        streams=[{"stream_id": "legacy", "sample_s": 2.0, "fields": ["x"], "format": "csv"}],
    )
    assert payload["recorder_mode"] == "postprocess_integer_stride"
    assert payload["post_run_downsampling"] is True
