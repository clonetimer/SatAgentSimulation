"""Selected telemetry fields must survive the real sealed dataset writer."""
import csv
import json
from pathlib import Path

import pytest
import yaml

from sat_sim.execution_planner import plan_task_spec


PREFIX = "propulsion.fuel_tank."
MASS = PREFIX + "propellant_mass_kg"


def _spec(pattern):
    example = Path(__file__).resolve().parents[1] / "examples/component_fuel_tank_basilisk_native.yaml"
    spec = yaml.safe_load(example.read_text())
    spec["outputs"]["telemetry_streams"] = [
        {"stream_id": "tank", "sample_s": .2, "fields": [pattern], "format": "csv"},
    ]
    return spec


@pytest.mark.parametrize("pattern", [
    PREFIX + "propellant_mass_k?", PREFIX + "propellant_*_kg",
    PREFIX + "propellant_mass_[a-z]g", PREFIX + "propellant_*kg*",
])
def test_native_preflight_rejects_telemetry_globs_the_writer_cannot_export(pattern):
    spec = _spec(pattern)
    planning = plan_task_spec(spec)
    assert not planning.ok
    assert any("telemetry_streams" in issue.path for issue in planning.validation.errors)
    spec["outputs"]["telemetry_streams"] = []
    spec["outputs"]["plots"] = [pattern]
    assert plan_task_spec(spec).ok


@pytest.mark.parametrize("pattern", [MASS, PREFIX + "propellant_mass_*"])
def test_supported_telemetry_selection_contains_actual_recorded_mass(tmp_path, pattern):
    pytest.importorskip("Basilisk")
    from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle

    prepared = prepare_run(_spec(pattern), output_root=tmp_path)
    result = execute_prepared_run(prepared.bundle_root, expected_plan_sha256=prepared.execution_plan_sha256)
    assert result.run_record.status.value == "SUCCEEDED"
    assert result.validation.result.value == "PASS" and result.run_record.sealed
    assert verify_run_bundle(prepared.bundle_root)["ok"]
    telemetry = Path(prepared.bundle_root) / "results/dataset/telemetry"
    manifest = json.loads((telemetry / "multi_rate_manifest.json").read_text())
    assert manifest["streams"][0]["fields"] == ["time_s", MASS]
    assert manifest["interpolation_used"] is False
    with (telemetry / "streams/tank.csv").open(newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == ["time_s", MASS]
        rows = list(reader)
    expected = result.trace_rows[::2]
    assert len(rows) == len(expected) == 6
    for actual, recorded in zip(rows, expected):
        assert float(actual["time_s"]) == recorded["time_s"]
        assert float(actual[MASS]) == recorded[MASS]
