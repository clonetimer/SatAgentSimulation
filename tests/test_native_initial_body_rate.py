"""Exercise the registered TaskSpec through the native whole-spacecraft adapter."""
import csv
import gzip
import json
from pathlib import Path

import pytest

from sat_sim.capability_registry import get_adapter_for_capability
from sat_sim.scenario_templates import instantiate_scenario_template
from sat_sim.task_compiler import compile_task_spec
from sat_sim.unified_execution import build_execution_request


@pytest.mark.parametrize("case", ["default", "legacy", "custom"])
def test_initial_rates_reach_native_spacecraft(case):
    custom = case == "custom"
    spec = instantiate_scenario_template("whole_spacecraft_unified_native", task_id="initial_rate_test")
    spec["simulation"].update(duration_s=6.0, step_s=0.1, sample_s=0.3)
    values = spec["parameters"]["values"]
    keys = [f"initial_body_rate_{axis}_rad_s" for axis in "xyz"]
    expected = [-0.003, 0.0, 0.006] if custom else [0.01, -0.004, 0.002]
    if custom:
        values.update(zip(keys, expected))
    request = build_execution_request(compile_task_spec(spec), spec)
    runtime = request.metadata["resolved_runtime_task_spec"]
    assert [runtime["parameters"][k] for k in keys] == expected
    if case == "legacy":
        for key in keys:
            del runtime["parameters"][key]
    rows = get_adapter_for_capability(request.capability_id).run(runtime).trace_rows
    fields = [f"attitude.truth.omega_BN_B_{axis}" for axis in "xyz"]
    assert len(rows) == 21 and rows[0]["time_s"] == 0
    assert [rows[0][k] for k in fields] == pytest.approx(expected, abs=1e-14)
    if not custom:
        archive = Path(__file__).resolve().parents[1] / "reports/external_reference_alignment/model_telemetry.csv.gz"
        with gzip.open(archive, "rt") as f:
            old = csv.DictReader(f)
            for row in rows:
                reference = next(old)
                for k in ["time_s", *fields, "thermal.battery_temp_c"]:
                    assert row[k] == pytest.approx(float(reference[k]), abs=1e-10, rel=0)
    print(json.dumps({"case": case, "samples": len(rows), "requested_rad_s": expected,
                      "initial_truth_rad_s": [rows[0][k] for k in fields],
                      "final_truth_rad_s": [rows[-1][k] for k in fields]}))
